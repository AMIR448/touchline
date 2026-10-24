"""The broadcast crew: a Microsoft Agent Framework workflow of specialised agents.

    Analyst ──▶ Commentator ──▶ FactChecker ──▶ Producer ──▶ coverage per viewer
                    ▲               │
                    └── rewrite ◀───┘   (up to 2 rewrites, then a safe fallback)

- Analyst (reasoning model): why the moment matters, backed by numbers.
- Commentator (fast model): broadcast caption and line.
- FactChecker (code): every number/name must be in the facts; failures loop back with the problems.
- Producer (fast model): one tailored version per viewer profile, in parallel, each fact-checked
  again (one rewrite, then the verified neutral line). The neutral studio feed skips it.

Every step is timed and recorded in the coverage `trace`, which the UI can show.
`recap()` runs a second, smaller workflow: RecapWriter ⇄ FactChecker.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, WorkflowViz, handler

from . import prompts
from .agents import AgentPool
from .context import moment_context, recap_context, viewer_context
from .factcheck import CheckResult, check, check_names_kept
from .models import LANGUAGES, Commentary, Coverage, Insight, PersonalizedLine, Recap, Step, ViewerProfile

MAX_COMMENTARY_ATTEMPTS = 3
MAX_PRODUCER_ATTEMPTS = 2
MAX_RECAP_ATTEMPTS = 3


def _facts_block(ctx: dict) -> str:
    return "FACTS:\n" + json.dumps(ctx, ensure_ascii=False, indent=1)


async def _timed(coro):
    start = time.perf_counter()
    result = await coro
    return result, int((time.perf_counter() - start) * 1000)


# ---------------------------------------------------------------- messages between executors


@dataclass
class CoverRequest:
    moment: dict
    snapshot: dict
    players: dict[str, dict]
    profiles: list[ViewerProfile]
    squad_names: list[str]
    context: dict = field(default_factory=dict)


@dataclass
class DraftTask:
    req: CoverRequest
    insight: Insight
    trace: list[Step]
    attempt: int = 1
    feedback: str | None = None
    previous: Commentary | None = None


@dataclass
class Draft:
    task: DraftTask
    commentary: Commentary


@dataclass
class ProduceTask:
    req: CoverRequest
    insight: Insight
    commentary: Commentary
    source: str  # "ai" or "fallback"
    trace: list[Step]


# ---------------------------------------------------------------- executors


class AnalystExecutor(Executor):
    def __init__(self, agents: AgentPool):
        super().__init__(id="analyst")
        self.agents = agents

    @handler
    async def analyse(self, req: CoverRequest, ctx: WorkflowContext[DraftTask]) -> None:
        req.context = moment_context(req.moment, req.snapshot, req.players)
        try:
            insight, ms = await _timed(self.agents.run("analyst", _facts_block(req.context), Insight))
            step = Step("analyst", ms)
        except Exception as e:  # the show goes on: commentator works from the raw facts
            insight = Insight(headline=req.moment["title"], why_it_matters="", supporting_numbers=[], angle="routine")
            step = Step("analyst", 0, f"failed: {type(e).__name__}")
        await ctx.send_message(DraftTask(req, insight, [step]))


class CommentatorExecutor(Executor):
    def __init__(self, agents: AgentPool):
        super().__init__(id="commentator")
        self.agents = agents

    @handler
    async def write(self, task: DraftTask, ctx: WorkflowContext[Draft]) -> None:
        prompt = f"{_facts_block(task.req.context)}\n\nANALYST INSIGHT:\n{task.insight.model_dump_json()}"
        if task.feedback:
            prompt += "\n\n" + prompts.REWRITE.format(problems=task.feedback, draft=task.previous.model_dump_json())
        try:
            commentary, ms = await _timed(self.agents.run("commentator", prompt, Commentary))
            task.trace.append(Step("commentator", ms, f"rewrite {task.attempt - 1}" if task.attempt > 1 else ""))
        except Exception as e:
            commentary = _fallback_commentary(task.req.moment)
            task.attempt = MAX_COMMENTARY_ATTEMPTS  # nothing to rewrite: go straight to fallback handling
            task.trace.append(Step("commentator", 0, f"failed: {type(e).__name__}"))
        await ctx.send_message(Draft(task, commentary))


class FactCheckExecutor(Executor):
    def __init__(self):
        super().__init__(id="fact_checker")

    @handler
    async def verify(self, draft: Draft, ctx: WorkflowContext[DraftTask | ProduceTask]) -> None:
        task = draft.task
        result = check([draft.commentary.line], [task.req.context], task.req.squad_names, headlines=[draft.commentary.caption])
        if result.ok:
            task.trace.append(Step("fact_checker", 0, "passed"))
            await ctx.send_message(ProduceTask(task.req, task.insight, draft.commentary, "ai", task.trace))
        elif task.attempt < MAX_COMMENTARY_ATTEMPTS:
            task.trace.append(Step("fact_checker", 0, "rejected: " + "; ".join(result.problems)))
            await ctx.send_message(DraftTask(task.req, task.insight, task.trace, task.attempt + 1,
                                             "\n".join(f"- {p}" for p in result.problems), draft.commentary))
        else:
            task.trace.append(Step("fact_checker", 0, "gave up: using safe fallback"))
            await ctx.send_message(ProduceTask(task.req, task.insight, _fallback_commentary(task.req.moment),
                                               "fallback", task.trace))


class ProducerExecutor(Executor):
    def __init__(self, agents: AgentPool):
        super().__init__(id="producer")
        self.agents = agents

    @handler
    async def produce(self, job: ProduceTask, ctx: WorkflowContext[None, Coverage]) -> None:
        results = await asyncio.gather(*(self._for_viewer(job, p) for p in job.req.profiles))
        for coverage in results:
            await ctx.yield_output(coverage)

    async def _for_viewer(self, job: ProduceTask, profile: ViewerProfile) -> Coverage:
        trace = list(job.trace)
        neutral = Coverage(job.req.moment["id"], profile.key(), job.commentary.caption, job.commentary.line, "en",
                           job.source, job.insight.model_dump(), trace)
        if job.source == "fallback" or profile.is_broadcast():
            return neutral
        extra = viewer_context(profile, job.req.snapshot, job.req.players)
        base = (f"{_facts_block({**job.req.context, **extra})}\n\nCOMMENTARY TO TAILOR:\n{job.commentary.model_dump_json()}"
                f"\n\nVIEWER PROFILE:\n{json.dumps(_describe(profile, job.req.snapshot, job.req.players), ensure_ascii=False)}")
        prompt = base
        for attempt in range(1, MAX_PRODUCER_ATTEMPTS + 1):
            try:
                line, ms = await _timed(self.agents.run("producer", prompt, PersonalizedLine))
            except Exception as e:
                trace.append(Step("producer", 0, f"failed: {type(e).__name__}; neutral line used"))
                return neutral
            trace.append(Step("producer", ms, f"{profile.persona}/{profile.language}" + (" rewrite" if attempt > 1 else "")))
            result = check([line.line], [job.req.context, extra], job.req.squad_names, headlines=[line.caption])
            if profile.language != "en":
                kept = check_names_kept([line.caption, line.line], job.commentary.caption + " " + job.commentary.line,
                                        job.req.squad_names)
                result = CheckResult(result.ok and kept.ok, result.problems + kept.problems)
            if result.ok:
                trace.append(Step("fact_checker", 0, "passed"))
                return Coverage(job.req.moment["id"], profile.key(), line.caption, line.line, profile.language, "ai",
                                job.insight.model_dump(), trace)
            trace.append(Step("fact_checker", 0, "rejected: " + "; ".join(result.problems)))
            prompt = base + "\n\n" + prompts.REWRITE.format(problems="\n".join(f"- {p}" for p in result.problems),
                                                           draft=line.model_dump_json())
        trace.append(Step("producer", 0, "gave up: verified neutral line used"))
        return neutral


def _describe(profile: ViewerProfile, snapshot: dict, players: dict) -> dict:
    return {
        "persona": profile.persona,
        "language": LANGUAGES.get(profile.language, profile.language),
        "favorite_team": snapshot["teams"].get(profile.favorite_team, {}).get("name") if profile.favorite_team else None,
        "favorite_player": players.get(profile.favorite_player, {}).get("name") if profile.favorite_player else None,
    }


def _fallback_commentary(moment: dict) -> Commentary:
    """Safe, always-true text straight from the stats engine's moment title."""
    return Commentary(caption=moment["title"][:80], line=moment["title"] + ".")


# ---------------------------------------------------------------- recap workflow


@dataclass
class RecapTask:
    context: dict
    instructions: str
    squad_names: list[str]
    attempt: int = 1
    feedback: str | None = None
    previous: Recap | None = None
    trace: list[Step] = field(default_factory=list)


@dataclass
class RecapDraft:
    task: RecapTask
    recap: Recap | None


class RecapWriterExecutor(Executor):
    def __init__(self, agents: AgentPool):
        super().__init__(id="recap_writer")
        self.agents = agents

    @handler
    async def write(self, task: RecapTask, ctx: WorkflowContext[RecapDraft]) -> None:
        prompt = _facts_block(task.context)
        if task.feedback:
            prompt += "\n\n" + prompts.REWRITE.format(problems=task.feedback, draft=task.previous.model_dump_json())
        try:
            recap, ms = await _timed(self.agents.run("recap", prompt, Recap, instructions=task.instructions))
            task.trace.append(Step("recap_writer", ms, f"rewrite {task.attempt - 1}" if task.attempt > 1 else ""))
        except Exception as e:
            recap = None
            task.trace.append(Step("recap_writer", 0, f"failed: {type(e).__name__}"))
        await ctx.send_message(RecapDraft(task, recap))


class RecapCheckExecutor(Executor):
    def __init__(self):
        super().__init__(id="recap_fact_checker")

    @handler
    async def verify(self, draft: RecapDraft, ctx: WorkflowContext[RecapTask, dict]) -> None:
        task, recap = draft.task, draft.recap
        if recap is None:
            await ctx.yield_output({"recap": None, "trace": [vars(s) for s in task.trace]})
            return
        texts = [*recap.summary, recap.player_of_the_match, recap.player_reason, *(n.value for n in recap.key_numbers)]
        result = check(texts, [task.context], task.squad_names, headlines=[recap.headline])
        if result.ok or task.attempt >= MAX_RECAP_ATTEMPTS:
            task.trace.append(Step("recap_fact_checker", 0, "passed" if result.ok else "gave up: " + "; ".join(result.problems)))
            await ctx.yield_output({"recap": recap.model_dump() if result.ok else None,
                                    "trace": [vars(s) for s in task.trace]})
            return
        task.trace.append(Step("recap_fact_checker", 0, "rejected: " + "; ".join(result.problems)))
        await ctx.send_message(RecapTask(task.context, task.instructions, task.squad_names, task.attempt + 1,
                                         "\n".join(f"- {p}" for p in result.problems), recap, task.trace))


# ---------------------------------------------------------------- public API


class BroadcastCrew:
    def __init__(self, agents: AgentPool):
        self.agents = agents

    def _coverage_workflow(self):
        analyst, commentator = AnalystExecutor(self.agents), CommentatorExecutor(self.agents)
        checker, producer = FactCheckExecutor(), ProducerExecutor(self.agents)
        return (WorkflowBuilder(name="broadcast-crew", start_executor=analyst)
                .add_edge(analyst, commentator)
                .add_edge(commentator, checker)
                .add_edge(checker, commentator, condition=lambda m: isinstance(m, DraftTask))
                .add_edge(checker, producer, condition=lambda m: isinstance(m, ProduceTask))
                .build())

    def _recap_workflow(self):
        writer, checker = RecapWriterExecutor(self.agents), RecapCheckExecutor()
        return (WorkflowBuilder(name="recap", start_executor=writer)
                .add_edge(writer, checker)
                .add_edge(checker, writer)
                .build())

    async def cover(self, moment: dict, snapshot: dict, players: dict[str, dict],
                    profiles: list[ViewerProfile]) -> list[Coverage]:
        """Commentary for one moment, tailored for each profile."""
        squad = [p["name"] for p in players.values()]
        result = await self._coverage_workflow().run(CoverRequest(moment, snapshot, players, profiles, squad))
        return list(result.get_outputs())

    async def recap(self, snapshot: dict, moments: list[dict], players: dict[str, dict],
                    profile: ViewerProfile) -> dict[str, Any]:
        when = "full-time" if snapshot["match"]["status"] == "full_time" else "half-time"
        favorite = ""
        if profile.favorite_team and profile.favorite_team in snapshot["teams"]:
            favorite = f"\nThe viewer supports {snapshot['teams'][profile.favorite_team]['name']}: frame it from their side."
        instructions = prompts.RECAP.format(when=when, persona=profile.persona,
                                            language=LANGUAGES.get(profile.language, profile.language), favorite=favorite)
        squad = [p["name"] for p in players.values()]
        task = RecapTask(recap_context(snapshot, moments), instructions, squad)
        result = await self._recap_workflow().run(task)
        return {**result.get_outputs()[0], "when": when, "profile_key": profile.key()}

    def diagram(self) -> str:
        """Mermaid diagram of the coverage workflow (used in the README)."""
        return WorkflowViz(self._coverage_workflow()).to_mermaid()
