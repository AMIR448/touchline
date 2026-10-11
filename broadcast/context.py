"""What the agents are allowed to know about a moment.

The context is a compact slice of the stats engine's state. It is the *only* source of
numbers the agents may use, and the fact-checker verifies against exactly this.
"""

from __future__ import annotations

from typing import Any

from .models import ViewerProfile

TEAM_KEYS = ["possession_pct", "shots", "on_target", "xg", "big_chances", "box_entries", "field_tilt_pct",
             "pass_accuracy_pct", "ppda", "corners", "yellow_cards", "red_cards"]
PLAYER_KEYS = ["goals", "assists", "shots", "xg", "key_passes", "passes", "passes_completed", "pass_accuracy_pct",
               "progressive_passes", "dribbles_won", "tackles", "interceptions", "saves", "max_shot_speed_kmh"]


def _team_name(snapshot: dict, team_id: str | None) -> str | None:
    if not team_id:
        return None
    return snapshot["teams"].get(team_id, {}).get("name")


def _situation(m: dict) -> str:
    """The score in words, so no model has to work out who is winning from "A 0-1 B"."""
    h, a = m["score"][m["home"]["id"]], m["score"][m["away"]["id"]]
    if h == a:
        return "level"
    leader, trailer = (m["home"]["name"], m["away"]["name"]) if h > a else (m["away"]["name"], m["home"]["name"])
    return f"{leader} lead by {abs(h - a)}; {trailer} are behind"


def moment_context(moment: dict, snapshot: dict, players: dict[str, dict] | None = None) -> dict[str, Any]:
    """Neutral context for one moment: the moment's facts plus the match picture around it."""
    m = snapshot["match"]
    teams = snapshot["teams"]
    names = {t: teams[t]["name"] for t in teams}
    ctx: dict[str, Any] = {
        "moment": {"type": moment["type"], "clock": moment["clock"], "title": moment["title"],
                   "team": _team_name(snapshot, moment.get("team_id")), "facts": moment["facts"]},
        "match": {"home": m["home"]["name"], "away": m["away"]["name"],
                  "score": f"{m['home']['name']} {m['score'][m['home']['id']]}-{m['score'][m['away']['id']]} {m['away']['name']}",
                  "situation": _situation(m), "clock": moment["clock"], "period": m["period"]},
        "match_stats": {names[t]: {k: teams[t][k] for k in TEAM_KEYS} for t in teams},
        "last_10_min": {names[t]: snapshot["last_10_min"][t] for t in teams},
        "momentum": {"value": snapshot["momentum"]["value"], "meaning": snapshot["momentum"]["explainer"]},
        "control_chaos": {"value": snapshot["control_chaos"]["value"], "label": snapshot["control_chaos"]["label"]},
    }
    involved = [players[p] for p in moment.get("player_ids", []) if players and p in players]
    if involved:
        ctx["players_involved"] = {p["name"]: {k: p[k] for k in PLAYER_KEYS if p.get(k)} for p in involved}
    return ctx


def viewer_context(profile: ViewerProfile, snapshot: dict, players: dict[str, dict] | None = None) -> dict[str, Any]:
    """Extra facts only this viewer needs: their club's and player's numbers."""
    extra: dict[str, Any] = {}
    if profile.favorite_team and profile.favorite_team in snapshot["teams"]:
        t = snapshot["teams"][profile.favorite_team]
        extra["your_team"] = {"name": t["name"], "goals": t["goals"],
                              **{k: t[k] for k in ("shots", "xg", "possession_pct")}}
    if profile.favorite_player and players and profile.favorite_player in players:
        p = players[profile.favorite_player]
        extra["your_player"] = {"name": p["name"], "on_pitch": p["on_pitch"], **{k: p[k] for k in PLAYER_KEYS if p.get(k)}}
    return extra


def recap_context(snapshot: dict, moments: list[dict]) -> dict[str, Any]:
    m = snapshot["match"]
    teams = snapshot["teams"]
    names = {t: teams[t]["name"] for t in teams}
    key = sorted((x for x in moments if x["importance"] >= 0.45), key=lambda x: x["ts_ms"])
    return {
        "match": {"home": m["home"]["name"], "away": m["away"]["name"],
                  "score": f"{m['home']['name']} {m['score'][m['home']['id']]}-{m['score'][m['away']['id']]} {m['away']['name']}",
                  "situation": _situation(m), "status": m["status"], "clock": m["clock"]},
        "match_stats": {names[t]: {k: teams[t][k] for k in TEAM_KEYS} for t in teams},
        "key_moments": [{"clock": x["clock"], "type": x["type"], "title": x["title"], "facts": x["facts"]} for x in key],
        "top_players": [{"name": p["name"], "team": names.get(p["team_id"]), **{k: p[k] for k in PLAYER_KEYS if p.get(k)}}
                        for p in snapshot["top_players"]],
    }
