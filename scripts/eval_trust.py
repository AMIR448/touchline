"""Measured trust: run the real AI crew over fresh synthetic matches and count what happened.

    source scripts/env.sh && python scripts/eval_trust.py --matches 12

For every key moment a Studio viewer and a Spanish-speaking home fan would get, the full crew runs
(Analyst → Commentator → Fact-checker → Producer); every half-time and full-time gets a recap; and a
fixed set of fan questions goes to the Ask agent at the 60th minute. The traces the crew records
anyway (which agent ran, how long, what the fact-checker said) are tallied into docs/TRUST.md and
server/static/app/trust.json (shown in the app's Control room).

No cache is used: every line here is freshly written by the models.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.analyzer import MatchAnalyzer  # noqa: E402
from broadcast.agents import FoundryAgents  # noqa: E402
from broadcast.context import moment_context, viewer_context  # noqa: E402
from broadcast.crew import BroadcastCrew  # noqa: E402
from broadcast.factcheck import check  # noqa: E402
from broadcast.match_tools import MatchTools  # noqa: E402
from broadcast.models import ViewerProfile  # noqa: E402
from broadcast.qa import MatchQA  # noqa: E402
from broadcast.relevance import wants_commentary, wants_recap  # noqa: E402
from simulator.clubs import CLUBS  # noqa: E402
from simulator.engine import simulate  # noqa: E402
from simulator.scenarios import SCENARIOS  # noqa: E402

PRICES = {"fast": (0.40, 1.60), "reasoning": (0.25, 2.00)}  # USD per 1M tokens (in, out), list prices
QUESTIONS = ["Who's on top right now, and why?", "Who has been the best player so far?",
             "How many shots on target does each team have?", "What changed in the last 10 minutes?",
             "Who has made the most tackles?", "Will the real Premier League champions win this?"]
ROOT = Path(__file__).resolve().parents[1]


def reason(note: str) -> str:
    if "not in the facts" in note and "number" in note:
        return "invented number"
    if "is not part of this moment" in note or "keep the player name" in note:
        return "wrong or mangled name"
    if "non-Latin" in note:
        return "mixed alphabet"
    return "other"


async def run(n_matches: int, seed: int, concurrency: int) -> dict:
    agents = FoundryAgents()
    crew, qa = BroadcastCrew(agents), MatchQA(agents)
    gate = asyncio.Semaphore(concurrency)
    rng = random.Random(seed)
    clubs, scenarios = list(CLUBS), list(SCENARIOS)
    coverages, recaps, answers = [], [], []

    async def cover(moment, snap, players, profiles):
        async with gate:
            t0 = time.perf_counter()
            out = await crew.cover(moment, snap, players, profiles)
            wall = int((time.perf_counter() - t0) * 1000)
        # Independent audit: re-check every AI line a viewer would see against the facts.
        squad = [p["name"] for p in players.values()]
        by_key = {p.key(): p for p in profiles}
        audit = []
        for c in out:
            if c.source == "ai":
                facts = [moment_context(moment, snap, players), viewer_context(by_key[c.profile_key], snap, players)]
                audit.append(check([c.caption, c.line], facts, squad).ok)
        coverages.append({"wall_ms": wall, "results": [c.to_dict() for c in out], "audit": audit})

    async def recap(snap, players, profile):
        async with gate:
            recaps.append(await crew.recap(snap, snap["moments"], players, profile))

    async def ask(question, tools):
        async with gate:
            t0 = time.perf_counter()
            out = await qa.ask(question, tools, ViewerProfile())
            answers.append({**out, "wall_ms": int((time.perf_counter() - t0) * 1000)})

    jobs = []
    for i in range(n_matches):
        home, away = rng.sample(clubs, 2)
        events = simulate(CLUBS[home], CLUBS[away], rng.randint(1, 99_999), scenarios[i % len(scenarios)])
        profiles = [ViewerProfile(persona="analyst", language="en"),
                    ViewerProfile(persona="casual", language="es", favorite_team=home)]
        a, asked = MatchAnalyzer(), False
        for ev in events:
            for m in a.process(ev):
                md = m.to_dict()
                players = {p: a.player(p) for p in a.players}
                wanted = [p for p in profiles if wants_commentary(p, md)]
                if wanted:
                    jobs.append(cover(md, a.snapshot(), players, wanted))
                elif wants_recap(md):
                    jobs.append(recap(a.snapshot(max_moments=1000), players, profiles[0]))
            if not asked and ev["period"] == 2 and int(ev["clock"].split(":")[0]) >= 60:
                asked = True
                tools = MatchTools(a.snapshot(max_moments=1000), {p: a.player(p) for p in a.players})
                jobs += [ask(q, tools) for q in QUESTIONS]
    started = time.perf_counter()
    await asyncio.gather(*jobs)
    return {"coverages": coverages, "recaps": recaps, "answers": answers, "usage": agents.usage,
            "matches": n_matches, "seconds": round(time.perf_counter() - started)}


def summarize(raw: dict) -> dict:
    lines = [c for cov in raw["coverages"] for c in cov["results"]]
    drafts = Counter()
    rejections = Counter()
    first_try = fallback = rewritten = producer_kept_neutral = 0
    for cov in raw["coverages"]:
        steps = cov["results"][0]["trace"]
        checks = [s for s in steps if s["agent"] == "fact_checker"]
        commentary_checks = checks[:next((i + 1 for i, s in enumerate(checks) if s["note"] == "passed"), len(checks))]
        drafts["commentary"] += len([s for s in steps if s["agent"] == "commentator"])
        for s in commentary_checks:
            if s["note"].startswith("rejected"):
                for problem in s["note"][len("rejected: "):].split("; "):
                    rejections[reason(problem)] += 1
        if cov["results"][0]["source"] == "fallback":
            fallback += 1
        elif len([s for s in commentary_checks if s["note"].startswith("rejected")]):
            rewritten += 1
        else:
            first_try += 1
    examples = []
    for cov in raw["coverages"]:
        for c in cov["results"]:
            steps = c["trace"]
            first_producer = next((i for i, s in enumerate(steps) if s["agent"] == "producer"), len(steps))
            for s in steps[first_producer:]:  # the Producer's own drafts, checked again per viewer
                if s["agent"] == "fact_checker" and s["note"].startswith("rejected"):
                    for problem in s["note"][len("rejected: "):].split("; "):
                        rejections[reason(problem)] += 1
                    examples.append({"stage": "producer", "viewer": c["profile_key"], "caught": s["note"][10:], "shown": c["line"]})
            if any(s["agent"] == "producer" and s["note"].startswith(("gave up", "failed")) for s in steps):
                producer_kept_neutral += 1
        for s in cov["results"][0]["trace"][:next((i for i, s in enumerate(cov["results"][0]["trace"]) if s["agent"] == "producer"), None)]:
            if s["agent"] == "fact_checker" and s["note"].startswith("rejected"):
                examples.append({"stage": "commentary", "viewer": cov["results"][0]["profile_key"], "caught": s["note"][10:],
                                 "shown": cov["results"][0]["line"]})
    for a in raw["answers"]:
        for s in a["trace"]:
            if s["agent"] == "fact_checker" and s["note"].startswith("rejected"):
                for problem in s["note"][len("rejected: "):].split("; "):
                    rejections[reason(problem)] += 1
                examples.append({"stage": "ask", "viewer": "analyst|en|-|-", "caught": s["note"][10:], "shown": a["answer"]})
    tailored = [c for c in lines if c["profile_key"] != "analyst|en|-|-"]
    crew_ms = [cov["wall_ms"] for cov in raw["coverages"]]
    qa = raw["answers"]
    qa_ms = [a["wall_ms"] for a in qa]
    usage = raw["usage"]
    cost = sum(usage[k][0] / 1e6 * PRICES[k][0] + usage[k][1] / 1e6 * PRICES[k][1] for k in usage)
    n = len(raw["coverages"]) or 1
    pct = lambda x, d: round(100 * x / d, 1) if d else 0.0  # noqa: E731
    return {
        "date": date.today().isoformat(), "matches": raw["matches"], "wall_seconds": raw["seconds"],
        "commentary": {
            "moments": len(raw["coverages"]), "lines_shown": len(lines),
            "first_draft_passed_pct": pct(first_try, n), "fixed_by_rewrite_pct": pct(rewritten, n),
            "safe_fallback_pct": pct(fallback, n), "drafts_written": drafts["commentary"],
            "problems_caught": dict(rejections),
            "drafts_caught": len(examples),
            "tailored_lines": len(tailored),
            "tailoring_fell_back_to_neutral_pct": pct(producer_kept_neutral, len(tailored)),
            "crew_seconds_p50": round(statistics.median(crew_ms) / 1000, 1) if crew_ms else None,
            "crew_seconds_p95": round(sorted(crew_ms)[int(0.95 * (len(crew_ms) - 1))] / 1000, 1) if crew_ms else None,
        },
        "recaps": {"written": len(raw["recaps"]), "verified_pct": pct(sum(1 for r in raw["recaps"] if r["recap"]), len(raw["recaps"]))},
        "ask": {
            "questions": len(qa), "verified_answers_pct": pct(sum(a["source"] == "ai" for a in qa), len(qa)),
            "honest_fallback_pct": pct(sum(a["source"] == "fallback" for a in qa), len(qa)),
            "avg_tools_per_answer": round(statistics.mean(len(a["tools"]) for a in qa), 1) if qa else None,
            "seconds_p50": round(statistics.median(qa_ms) / 1000, 1) if qa_ms else None,
        },
        "examples": _varied(examples),
        "ai_lines_audited": sum(len(cov["audit"]) for cov in raw["coverages"]),
        "unverified_ai_lines_shown": sum(not ok for cov in raw["coverages"] for ok in cov["audit"]),
        "tokens": {k: {"input": v[0], "output": v[1]} for k, v in usage.items()},
        "cost_usd": round(cost, 2), "cost_per_match_usd": round(cost / raw["matches"], 3),
    }


def _varied(examples: list[dict], n: int = 6) -> list[dict]:
    """A readable sample: no duplicates, each stage and kind of problem represented first."""
    seen, out = set(), []
    order = {"commentary": 0, "ask": 1, "producer": 2}
    for e in sorted(examples, key=lambda e: (order[e["stage"]], reason(e["caught"]))):
        key = (e["caught"], e["shown"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    picked = [next(e for e in out if e["stage"] == st) for st in order if any(e["stage"] == st for e in out)]
    return (picked + [e for e in out if e not in picked])[:n]


def markdown(s: dict) -> str:
    c, r, q = s["commentary"], s["recaps"], s["ask"]
    caught = ", ".join(f"{v} {k}" for k, v in sorted(c["problems_caught"].items(), key=lambda kv: -kv[1])) or "none"
    return f"""# Measured trust

Generated by `scripts/eval_trust.py` on {s['date']}: the real AI crew (Microsoft Foundry: gpt-5-mini and
gpt-4.1-mini, via Microsoft Agent Framework) over **{s['matches']} fresh synthetic matches**, with no cache.
Each key moment was covered for a Studio viewer (English, analyst) and a Spanish-speaking home fan;
every half-time and full-time got a recap; six fan questions went to "Ask the match" at minute 60.

## Commentary ({c['moments']} moments, {c['lines_shown']} lines shown)

| | |
|---|---|
| Passed the fact-checker on the first draft | **{c['first_draft_passed_pct']}%** |
| Rejected, then fixed by a rewrite | **{c['fixed_by_rewrite_pct']}%** |
| Never verified, so the stats engine's own text was shown | **{c['safe_fallback_pct']}%** |
| Drafts the fact-checker caught (commentary, translations, answers) | {c['drafts_caught']}: {caught} |
| Tailored lines (Producer) that fell back to the verified neutral line | {c['tailoring_fell_back_to_neutral_pct']}% of {c['tailored_lines']} |
| Crew time per moment (all agents, median / 95th percentile) | {c['crew_seconds_p50']} s / {c['crew_seconds_p95']} s |
| **AI lines shown to viewers that fail an independent re-check** | **{s['unverified_ai_lines_shown']} of {s['ai_lines_audited']}** |

## Recaps and questions

| | |
|---|---|
| Recaps written / passed the fact-checker | {r['written']} / {r['verified_pct']}% |
| Fan questions answered with a verified answer | {q['verified_answers_pct']}% of {q['questions']} |
| Answered "I couldn't verify that" instead of guessing | {q['honest_fallback_pct']}% |
| Tools the Q&A agent chose per answer (average) | {q['avg_tools_per_answer']} |
| Answer time (median) | {q['seconds_p50']} s |

## Cost

{s['tokens']['reasoning']['input'] + s['tokens']['fast']['input']:,} input and {s['tokens']['reasoning']['output'] + s['tokens']['fast']['output']:,} output tokens:
about **${s['cost_usd']}** in total, **${s['cost_per_match_usd']} per match** for two viewer profiles, at list
prices. Results are cached per (match, moment, profile), so every further viewer with the same profile is free.

## Caught in this run

{chr(10).join(f"- **{e['stage']}** ({e['viewer']}): {e['caught']}. Shown instead: *{e['shown']}*" for e in s['examples'])}

## What the fact-checker does and doesn't guarantee

It guarantees that every number and player name in a shown line appears in the facts the stats engine
handed over (allowing rounding, percentages of probabilities and minute-of-play conventions). It does
not prove a number is attributed to the right team or that the sentence's meaning is right; for that,
the facts state key meanings in words (for example "Marlowe United lead by 1"), and the prompts forbid
quoting internal indices. Those are mitigations, not guarantees.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", type=int, default=12)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--from-raw", action="store_true", help="re-summarize data/trust-raw.json without new AI calls")
    args = ap.parse_args()
    raw = (json.loads((ROOT / "data" / "trust-raw.json").read_text()) if args.from_raw
           else asyncio.run(run(args.matches, args.seed, args.concurrency)))
    summary = summarize(raw)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "trust-raw.json").write_text(json.dumps(raw, ensure_ascii=False))
    (ROOT / "server" / "static" / "app" / "trust.json").write_text(json.dumps(summary, indent=1))
    (ROOT / "docs" / "TRUST.md").write_text(markdown(summary))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
