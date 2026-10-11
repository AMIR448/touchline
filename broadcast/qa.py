"""Ask the match: a tool-using agent answers a fan's question, then the fact-checker checks it.

    question ──▶ MatchQA agent ──(calls the match tools it needs)──▶ answer ──▶ FactChecker
                      ▲                                                            │
                      └────────────── one rewrite with the problems ◀──────────────┘

Unlike the commentary crew, nobody hands this agent its facts: it decides what to look up.
The fact-checker then accepts only numbers and names that came back from those tool calls
(or the current situation), so a confident-sounding guess can't reach the fan.
"""

from __future__ import annotations

import functools
import json
import time
from typing import Any, Callable

from . import prompts
from .agents import AgentPool
from .factcheck import check
from .match_tools import MatchTools
from .models import LANGUAGES, Step, ViewerProfile

MAX_ATTEMPTS = 2
FALLBACK = {
    "en": "I couldn't verify an answer to that from the match data, so I'd rather not guess.",
    "es": "No he podido verificar una respuesta con los datos del partido, así que prefiero no adivinar.",
    "fr": "Je n'ai pas pu vérifier de réponse avec les données du match, alors je préfère ne pas deviner.",
    "pt": "Não consegui verificar uma resposta com os dados da partida, por isso prefiro não adivinhar.",
}


def _recorded(fn: Callable[..., Any], calls: list[dict]) -> Callable[..., Any]:
    """Same tool, but every call (arguments, result, time) is kept for the fact-checker and the UI."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        calls.append({"tool": fn.__name__, "args": kwargs or list(args), "result": result,
                      "ms": int((time.perf_counter() - start) * 1000)})
        return result
    return wrapper


def _describe_args(call: dict) -> str:
    args = call["args"]
    vals = args.values() if isinstance(args, dict) else args
    return ", ".join(str(v) for v in vals if v is not None)


class MatchQA:
    def __init__(self, agents: AgentPool):
        self.agents = agents

    async def ask(self, question: str, tools: MatchTools, profile: ViewerProfile) -> dict[str, Any]:
        calls: list[dict] = []
        functions = [_recorded(f, calls) for f in tools.functions()]
        situation = tools.match_situation()
        squad = [p["name"] for p in tools.players.values()]
        team = tools.names.get(profile.favorite_team) if profile.favorite_team else None
        player = tools.players.get(profile.favorite_player, {}).get("name") if profile.favorite_player else None
        favorite = "".join([f" The fan supports {team}; it's fine to say 'your side'." if team else "",
                            f" Their favourite player is {player}." if player else ""])
        instructions = prompts.QA.format(language=LANGUAGES.get(profile.language, "English"), persona=profile.persona,
                                         favorite=favorite)
        base = f"CURRENT SITUATION:\n{json.dumps(situation, ensure_ascii=False)}\n\nQUESTION: {question}"
        prompt, trace = base, []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            first_call = len(calls)
            start = time.perf_counter()
            try:
                answer = await self.agents.ask(prompt, functions, instructions)
            except Exception as e:
                trace.append(Step("match_qa", 0, f"failed: {type(e).__name__}"))
                break
            ms = int((time.perf_counter() - start) * 1000)
            used = [f"{c['tool']}({_describe_args(c)})" for c in calls[first_call:]]
            trace.append(Step("match_qa", ms, ("rewrite; " if attempt > 1 else "") +
                              ("looked up " + ", ".join(used) if used else "no lookups needed")))
            result = check([answer.answer], [situation, *(c["result"] for c in calls)], squad)
            if result.ok:
                trace.append(Step("fact_checker", 0, "passed"))
                return self._out(answer.answer, answer.follow_ups[:3], "ai", calls, trace)
            trace.append(Step("fact_checker", 0, "rejected: " + "; ".join(result.problems)))
            prompt = base + "\n\n" + prompts.REWRITE.format(problems="\n".join(f"- {p}" for p in result.problems),
                                                           draft=answer.model_dump_json())
        trace.append(Step("match_qa", 0, "gave up: honest fallback used"))
        return self._out(FALLBACK[profile.language], [], "fallback", calls, trace)

    @staticmethod
    def _out(text: str, follow_ups: list[str], source: str, calls: list[dict], trace: list[Step]) -> dict[str, Any]:
        return {"answer": text, "follow_ups": follow_ups, "source": source,
                "tools": [{"tool": c["tool"], "args": _describe_args(c), "ms": c["ms"]} for c in calls],
                "trace": [vars(s) for s in trace]}
