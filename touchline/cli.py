"""Command line: clubs / generate / summary / analyze / replay / serve."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from analytics.analyzer import MatchAnalyzer
from simulator.clubs import CLUBS, get_club
from simulator.engine import simulate
from simulator.scenarios import SCENARIOS


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def analyze(events: list[dict]) -> MatchAnalyzer:
    a = MatchAnalyzer()
    for ev in events:
        a.process(ev)
    return a


def cmd_clubs(a: argparse.Namespace) -> None:
    for c in CLUBS.values():
        t = c.tactics
        print(f"  {c.id:<10} {c.name:<20} {c.formation:<8} press {t.press:.2f}  directness {t.directness:.2f}"
              f"  tempo {t.tempo:.2f}  strength {c.strength}")


def cmd_generate(a: argparse.Namespace) -> None:
    events = simulate(get_club(a.home), get_club(a.away), a.seed, a.scenario)
    out = Path(a.out or f"data/{events[0]['match_id']}-{a.scenario}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(f"Wrote {len(events)} events to {out}  |  final score {events[-1]['details']['score']}")


STAT_ROWS = [
    ("possession_pct", "possession %"), ("passes", "passes"), ("pass_accuracy_pct", "pass accuracy %"),
    ("avg_pass_difficulty", "avg pass difficulty"), ("progressive_passes", "progressive passes"),
    ("box_entries", "box entries"), ("field_tilt_pct", "field tilt %"), ("shots", "shots"),
    ("on_target", "on target"), ("xg", "xG"), ("big_chances", "big chances"), ("corners", "corners"),
    ("ppda", "PPDA (press)"), ("fouls", "fouls"), ("yellow_cards", "yellow cards"), ("red_cards", "red cards"),
]


def cmd_summary(a: argparse.Namespace) -> None:
    events = load(a.file)
    snap = analyze(events).snapshot()
    m = snap["match"]
    h, w = m["home"]["id"], m["away"]["id"]
    meta = events[0]["details"]
    print(f"\n  {m['home']['name']} {m['score'][h]} - {m['score'][w]} {m['away']['name']}"
          f"   (scenario: {meta['scenario']}, seed: {meta['seed']}, events: {len(events)})\n")
    for key, label in STAT_ROWS:
        print(f"  {str(snap['teams'][h][key]):>8}   {label:<20} {snap['teams'][w][key]}")
    print("\n  Top performers")
    for p in snap["top_players"]:
        line = f"    {p['name']:<20} {p['team_id']:<10} impact {p['impact']:<5}"
        bits = [f"{p['goals']}G" if p["goals"] else "", f"{p['assists']}A" if p["assists"] else "",
                f"xG {p['xg']}" if p["xg"] else "", f"{p['key_passes']} key passes" if p["key_passes"] else ""]
        print(line + "  " + "  ".join(b for b in bits if b))
    print()


ICONS = {"goal": "⚽", "card": "🟥", "big_chance_missed": "😱", "great_save": "🧤", "momentum_swing": "📈",
         "counter_attack": "⚡", "key_pass": "🎯", "pressing_spell": "🔥", "chaos_spike": "🌪 ",
         "sustained_possession": "🔄", "thunderbolt": "💥", "milestone": "🏅", "substitution": "🔁",
         "half_time": "⏸ ", "full_time": "🏁"}


def cmd_analyze(a: argparse.Namespace) -> None:
    events = load(a.file)
    analyzer = MatchAnalyzer()
    for ev in events:
        for m in analyzer.process(ev):
            if m.importance < a.min_importance:
                continue
            icon = "🟨" if m.type == "card" and m.importance < 0.5 else ICONS.get(m.type, "•")
            print(f"  {icon} {m.clock:>6}  [{m.importance:.2f}] {m.title}")
            if a.facts:
                print(f"            facts: {json.dumps(m.facts, ensure_ascii=False)}")
                print(f"            evidence: {', '.join(m.evidence[-4:])}")
    if a.json:
        Path(a.json).write_text(json.dumps(analyzer.snapshot(max_moments=1000), indent=2, ensure_ascii=False))
        print(f"\n  Full snapshot written to {a.json}")


def cmd_replay(a: argparse.Namespace) -> None:
    """Print events at (match time / speed), as a live feed would deliver them."""
    events = load(a.file)
    start, first_ts = time.monotonic(), events[0]["ts_ms"]
    for ev in events:
        wait = (ev["ts_ms"] - first_ts) / 1000 / a.speed - (time.monotonic() - start)
        if wait > 0:
            time.sleep(wait)
        print(json.dumps(ev, ensure_ascii=False), flush=True)


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn

    print(f"  Touchline running at http://localhost:{a.port}")
    uvicorn.run("server.app:app", host=a.host, port=a.port, reload=a.reload)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="touchline", description="Synthetic football match intelligence")
    sub = p.add_subparsers(required=True)

    sub.add_parser("clubs", help="list the fictional clubs").set_defaults(fn=cmd_clubs)

    g = sub.add_parser("generate", help="simulate a match and write events as JSON Lines")
    g.add_argument("--home", default="kestrel", choices=CLUBS)
    g.add_argument("--away", default="marlowe", choices=CLUBS)
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--scenario", default="standard", choices=SCENARIOS)
    g.add_argument("--out")
    g.set_defaults(fn=cmd_generate)

    s = sub.add_parser("summary", help="box score and top performers of a match")
    s.add_argument("file")
    s.set_defaults(fn=cmd_summary)

    an = sub.add_parser("analyze", help="run the stats engine and print the key moments")
    an.add_argument("file")
    an.add_argument("--min-importance", type=float, default=0.3, help="hide moments below this (0..1)")
    an.add_argument("--facts", action="store_true", help="also print each moment's facts and evidence")
    an.add_argument("--json", metavar="PATH", help="write the full final snapshot to a JSON file")
    an.set_defaults(fn=cmd_analyze)

    r = sub.add_parser("replay", help="stream a match to stdout in (accelerated) real time")
    r.add_argument("file")
    r.add_argument("--speed", type=float, default=10.0, help="1 = real time, 60 = a minute per second")
    r.set_defaults(fn=cmd_replay)

    sv = sub.add_parser("serve", help="run the web server (live playback + dev viewer)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true", help="restart on code changes")
    sv.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except BrokenPipeError:  # e.g. `touchline replay ... | head`
        sys.stderr.close()
