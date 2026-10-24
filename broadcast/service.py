"""Runs the crew for the server: caching, de-duplication and a concurrency limit.

Several viewers with the same profile watching the same match share one AI result, and a
replay costs nothing. Results are cached in memory and, optionally, in a JSON file so a
restart (or a judge's visit) doesn't re-generate them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from opentelemetry import trace

from .agents import AgentPool
from .crew import BroadcastCrew
from .limits import DailyBudget
from .match_tools import MatchTools
from .models import Coverage, Step, ViewerProfile
from .qa import MatchQA

log = logging.getLogger("touchline.broadcast")
_tracer = trace.get_tracer("touchline.broadcast")


class BroadcastService:
    def __init__(self, agents: AgentPool, max_concurrent: int = 6, cache_file: str | Path | None = None,
                 budget: DailyBudget | None = None):
        self.crew = BroadcastCrew(agents)
        self.budget = budget
        self.qa = MatchQA(agents)
        self.limit = asyncio.Semaphore(max_concurrent)
        self.cache: dict[str, Any] = {}
        self.inflight: dict[str, asyncio.Future] = {}
        self.cache_file = Path(cache_file) if cache_file else None
        if self.cache_file and self.cache_file.exists():
            self.cache = json.loads(self.cache_file.read_text())

    def lookup(self, kind: str, match_id: str, moment_id: str, profile: ViewerProfile) -> Any:
        """An already-written result, or None. Never calls a model."""
        return self.cache.get(f"{kind}|{match_id}|{moment_id}|{profile.key()}")

    async def _once(self, key: str, make) -> Any:
        if key in self.cache:
            return self.cache[key]
        if key in self.inflight:
            return await asyncio.shield(self.inflight[key])
        future = asyncio.get_running_loop().create_future()
        self.inflight[key] = future
        try:
            async with self.limit:
                result = await make()
            if _keepable(result):
                self.cache[key] = result
                self._persist()
            future.set_result(result)
            return result
        except BaseException as e:
            future.set_exception(e)
            future.exception()  # mark retrieved so an unawaited future doesn't log noise
            raise
        finally:
            self.inflight.pop(key, None)

    def _persist(self) -> None:
        if self.cache_file:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, ensure_ascii=False))
            tmp.replace(self.cache_file)

    def _fresh_run_allowed(self, key: str) -> bool:
        """Cached or in-flight results are free; a new AI run spends from today's budget."""
        return key in self.cache or key in self.inflight or self.budget is None or self.budget.take()

    async def cover(self, match_id: str, moment: dict, snapshot: dict, players: dict[str, dict],
                    profile: ViewerProfile) -> dict:
        key = f"cover|{match_id}|{moment['id']}|{profile.key()}"
        if not self._fresh_run_allowed(key):
            return _budget_fallback(moment, profile)

        async def make():
            results = await self.crew.cover(moment, snapshot, players, [profile])
            return results[0].to_dict()
        return await self._once(key, make)

    async def recap(self, match_id: str, moment: dict, snapshot: dict, moments: list[dict],
                    players: dict[str, dict], profile: ViewerProfile) -> dict:
        key = f"recap|{match_id}|{moment['id']}|{profile.key()}"
        if not self._fresh_run_allowed(key):
            return {"recap": None, "trace": [vars(Step("budget", 0, BUDGET_NOTE))]}

        async def make():
            return await self.crew.recap(snapshot, moments, players, profile)
        return await self._once(key, make)

    async def ask(self, question: str, tools: MatchTools, profile: ViewerProfile) -> dict:
        """Fan questions are never cached: the answer depends on the minute it was asked."""
        if self.budget and not self.budget.take():
            return {"answer": "The AI crew has reached today's limit on this demo. Please try again tomorrow.",
                    "follow_ups": [], "source": "fallback", "tools": [], "trace": [vars(Step("budget", 0, BUDGET_NOTE))]}
        async with self.limit:
            with _tracer.start_as_current_span("match_qa", attributes={"touchline.question": question[:200]}):
                return await self.qa.ask(question, tools, profile)

    async def cover_many(self, match_id: str, moment: dict, snapshot: dict, players: dict[str, dict],
                         profiles: list[ViewerProfile]) -> list[dict]:
        """Several viewers at once: one Analyst + Commentator pass, then the Producer fans out in parallel."""
        keys = [f"cover|{match_id}|{moment['id']}|{p.key()}" for p in profiles]
        missing = [p for p, k in zip(profiles, keys) if k not in self.cache]
        if missing and self.budget and not self.budget.take():
            return [self.cache.get(k) or _budget_fallback(moment, p) for p, k in zip(profiles, keys)]
        if missing:
            fresh = {}
            async with self.limit:
                # one span per moment: the crew's workflow, every agent call and fact-check nest under it
                with _tracer.start_as_current_span(f"cover {moment['type']}", attributes={
                        "touchline.match": match_id, "touchline.moment": moment["id"], "touchline.viewers": len(missing)}):
                    covered = await self.crew.cover(moment, snapshot, players, missing)
                for c in covered:
                    fresh[c.profile_key] = c.to_dict()
                    if _keepable(fresh[c.profile_key]):
                        self.cache[f"cover|{match_id}|{moment['id']}|{c.profile_key}"] = fresh[c.profile_key]
            self._persist()
            return [self.cache.get(k) or fresh[p.key()] for p, k in zip(profiles, keys)]
        return [self.cache[k] for k in keys]


def _keepable(result: Any) -> bool:
    """Don't keep results where a writer crashed (e.g. a rate limit): the viewer got a fallback this time,
    but the next viewer should get a real attempt, not a cached failure."""
    if not isinstance(result, dict):
        return True
    return not any(s.get("agent") in ("commentator", "producer", "recap_writer") and s.get("note", "").startswith("failed")
                   for s in result.get("trace", []))


BUDGET_NOTE = "daily AI budget reached: the stats engine's verified text is used instead"


def _budget_fallback(moment: dict, profile: ViewerProfile) -> dict:
    """Not cached, so the real commentary is written once the budget resets."""
    return Coverage(moment["id"], profile.key(), moment["title"][:80], moment["title"] + ".", "en", "fallback",
                    None, [Step("budget", 0, BUDGET_NOTE)]).to_dict()
