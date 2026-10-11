"""Match tools: read-only questions about a match *as of a moment in time*.

One implementation, two consumers:
- the Q&A agent (broadcast/qa.py) calls them as Agent Framework function tools, choosing which ones
  it needs to answer a fan's question;
- the MCP server (server/mcp_server.py) exposes the same tools to any MCP client (GitHub Copilot,
  Claude, a Foundry agent).

Tools work on a frozen snapshot taken when the question is asked, so an answer never includes
anything that happens later in the match: no spoilers for a viewer who is watching a replay.
All numbers come from the stats engine; the tools never estimate anything.
"""

from __future__ import annotations

from typing import Any

from .context import PLAYER_KEYS, TEAM_KEYS, _situation

MOMENT_TYPES = ["goal", "big_chance_missed", "great_save", "key_pass", "counter_attack", "sustained_possession",
                "momentum_swing", "pressing_spell", "chaos_spike", "thunderbolt", "card", "substitution", "milestone"]
RANK_STATS = ["impact", "goals", "assists", "shots", "xg", "key_passes", "passes", "pass_accuracy_pct",
              "progressive_passes", "dribbles_won", "tackles", "interceptions", "recoveries", "saves", "max_shot_speed_kmh"]


class MatchTools:
    def __init__(self, snapshot: dict[str, Any], players: dict[str, dict[str, Any]]):
        self.snap = snapshot
        self.players = players
        self.names = {t: v["name"] for t, v in snapshot["teams"].items()}

    # ------------------------------------------------------------------ tools

    def match_situation(self) -> dict[str, Any]:
        """Score, clock, who is winning, momentum and how chaotic the game is right now."""
        m, s = self.snap["match"], self.snap
        return {
            "score": f"{m['home']['name']} {m['score'][m['home']['id']]}-{m['score'][m['away']['id']]} {m['away']['name']}",
            "situation": _situation(m), "clock": m["clock"], "period": m["period"], "status": m["status"],
            "momentum": {"value": s["momentum"]["value"], "meaning": s["momentum"]["explainer"]},
            "control_chaos": {"value": s["control_chaos"]["value"], "label": s["control_chaos"]["label"],
                              "scale": "0 calm build-up, 100 end-to-end chaos"},
            "red_cards": {self.names[t]: v["red_cards"] for t, v in s["teams"].items() if v["red_cards"]},
        }

    def team_stats(self) -> dict[str, Any]:
        """Both teams' stats for the whole match so far, and for the last 10 minutes (who is on top now)."""
        teams = self.snap["teams"]
        return {
            "whole_match": {self.names[t]: {k: teams[t][k] for k in TEAM_KEYS + ["passes", "pressures", "tackles", "saves"]}
                            for t in teams},
            "last_10_minutes": {self.names[t]: self.snap["last_10_min"][t] for t in teams},
            "metric_notes": "xG = chance quality; PPDA lower = more intense press; field tilt = share of final-third touches",
        }

    def player_stats(self, name: str) -> dict[str, Any]:
        """One player's match so far. `name` can be a full name, a surname or a shirt number."""
        found = self._find_players(name)
        if not found:
            return {"error": f"no player matches '{name}'", "hint": "try a surname"}
        if len(found) > 1:
            return {"several_players_match": [self._label(p) for p in found[:6]]}
        p = found[0]
        return {**self._label(p), "on_pitch": p["on_pitch"], **{k: p[k] for k in PLAYER_KEYS + ["touches", "pressures", "carry_m"]}}

    def top_players(self, by: str = "impact", team: str | None = None, limit: int = 5) -> dict[str, Any]:
        """Best players so far, ranked by one stat (default: overall impact). Optionally for one team."""
        if by not in RANK_STATS:
            return {"error": f"unknown stat '{by}'", "options": RANK_STATS}
        team_id = self._find_team(team) if team else None
        pool = [p for p in self.players.values() if (not team_id or p["team_id"] == team_id) and p["touches"]]
        ranked = sorted(pool, key=lambda p: p[by] or 0, reverse=True)[:max(1, min(limit, 10))]
        return {"ranked_by": by, "players": [{**self._label(p), by: p[by]} for p in ranked]}

    def key_moments(self, kind: str | None = None, team: str | None = None, limit: int = 8) -> dict[str, Any]:
        """The match's key moments so far, newest first, with the facts behind each.
        Optionally filter by kind (goal, card, big_chance_missed, momentum_swing, ...) or by team."""
        team_id = self._find_team(team) if team else None
        out = [m for m in reversed(self.snap["moments"]) if m["importance"] >= 0.3 and (not kind or m["type"] == kind)
               and (not team_id or m.get("team_id") == team_id)]
        return {"moments": [{"clock": m["clock"], "type": m["type"], "title": m["title"],
                             "team": self.names.get(m.get("team_id")), "facts": m["facts"]}
                            for m in out[:max(1, min(limit, 15))]]}

    # ------------------------------------------------------------------ helpers

    def _label(self, p: dict) -> dict[str, Any]:
        return {"name": p["name"], "team": self.names.get(p["team_id"]), "number": p["number"], "position": p["position"]}

    def _find_players(self, query: str) -> list[dict]:
        q = query.strip().lower()
        if q.isdigit():
            return [p for p in self.players.values() if p["number"] == int(q)]
        exact = [p for p in self.players.values() if p["name"].lower() == q]
        return exact or [p for p in self.players.values() if q in p["name"].lower()]

    def _find_team(self, query: str) -> str | None:
        q = query.strip().lower()
        return next((t for t, n in self.names.items() if q in n.lower() or q == t), None)

    def functions(self) -> list:
        """The tools as plain callables (name, docstring, typed parameters) for agents and MCP."""
        return [self.match_situation, self.team_stats, self.player_stats, self.top_players, self.key_moments]
