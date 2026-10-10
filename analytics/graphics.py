"""Graphics director: turns the live feed into timed, machine-readable overlay cues.

A broadcast graphics system (the "rendering partner" in a production chain) consumes cues
like these to draw synchronized on-screen graphics. Each cue says what to show, when, for how
long and how important it is. The UI draws the same cues, and `/api/matches/{id}/cues`
exposes them for any other renderer.

Cue types:
- `player_tag`: name and number of the player on the ball, shown when possession moves to them
- `stat_badge`: a pop-up when a threshold is crossed (shot speed, pass length or difficulty, long carry)
- `score_bug`: the score/clock box, re-cued whenever the score changes
- `moment_banner`: a big banner for major moments (goal, red card, great save, big chance)
- `lower_third`: the commentary bar; carries the AI crew's caption and line when available

All thresholds are plain numbers here, so every badge is explainable.
"""

from __future__ import annotations

from typing import Any

from .metrics import pass_difficulty, shot_xg

SHOT_SPEED_KMH = 95
PASS_LENGTH_M = 45
PASS_DIFFICULTY = 6.5
BALL_SPEED_KMH = 80
CARRY_M = 15
SHOT_XG = 0.15
BANNER_MOMENTS = {"goal": "GOAL", "great_save": "GREAT SAVE", "big_chance_missed": "BIG CHANCE",
                  "card": "RED CARD", "counter_attack": "COUNTER-ATTACK", "half_time": "HALF-TIME",
                  "full_time": "FULL-TIME"}
DURATION_MS = {"player_tag": 3000, "stat_badge": 4000, "score_bug": 0, "moment_banner": 5000, "lower_third": 9000}
PRIORITY = {"player_tag": 1, "stat_badge": 2, "score_bug": 3, "lower_third": 3, "moment_banner": 4}


class GraphicsDirector:
    def __init__(self, match_start: dict):
        d = match_start["details"]
        self.match_id = match_start["match_id"]
        self.teams = {side["id"]: side for side in (d["home"], d["away"])}
        self.players = {p["id"]: {**p, "team_id": side["id"]}
                        for side in (d["home"], d["away"]) for p in side["starters"] + side["bench"]}
        self.home, self.away = d["home"]["id"], d["away"]["id"]
        self.score = {self.home: 0, self.away: 0}
        self.holder: str | None = None
        self.count = 0

    def _cue(self, type: str, ev: dict, payload: dict[str, Any], priority: int | None = None) -> dict[str, Any]:
        self.count += 1
        return {"cue_id": f"{self.match_id}-c{self.count:05d}", "type": type, "ts_ms": ev["ts_ms"], "clock": ev["clock"],
                "duration_ms": DURATION_MS[type], "priority": priority or PRIORITY[type], "payload": payload}

    def _player(self, pid: str | None) -> dict[str, Any] | None:
        if not pid or pid not in self.players:
            return None
        p = self.players[pid]
        team = self.teams[p["team_id"]]
        return {"player_id": pid, "name": p["name"], "number": p["number"], "team_id": p["team_id"],
                "team_short": team["short"], "color": team["colors"][0]}

    def _score_payload(self) -> dict[str, Any]:
        return {"home": {"id": self.home, "short": self.teams[self.home]["short"], "goals": self.score[self.home],
                         "color": self.teams[self.home]["colors"][0]},
                "away": {"id": self.away, "short": self.teams[self.away]["short"], "goals": self.score[self.away],
                         "color": self.teams[self.away]["colors"][0]}}

    def on_event(self, ev: dict) -> list[dict[str, Any]]:
        cues: list[dict[str, Any]] = []
        typ, d = ev["type"], ev.get("details", {})
        if typ == "period_start" and ev["period"] == 1:
            cues.append(self._cue("score_bug", ev, self._score_payload()))
        if typ in ("pass", "carry", "dribble", "shot") and ev.get("player_id") != self.holder:
            self.holder = ev.get("player_id")
            tag = self._player(self.holder)
            if tag:
                cues.append(self._cue("player_tag", ev, tag))
        badge = self._badge(ev)
        if badge:
            cues.append(self._cue("stat_badge", ev, {**badge, "player": self._player(ev.get("player_id"))}))
        if typ == "goal":
            self.score = dict(d.get("score", self.score))
            cues.append(self._cue("score_bug", ev, self._score_payload()))
        return cues

    def _badge(self, ev: dict) -> dict[str, Any] | None:
        typ, d = ev["type"], ev.get("details", {})
        if typ == "shot":
            speed = d.get("shot_speed_kmh", 0)
            if speed >= SHOT_SPEED_KMH:
                return {"label": "Shot speed", "value": f"{speed:.0f}", "unit": "km/h", "threshold": SHOT_SPEED_KMH}
            xg = shot_xg(ev)
            if xg >= SHOT_XG:
                return {"label": "Chance quality", "value": f"{xg:.2f}", "unit": "xG", "threshold": SHOT_XG}
        if typ == "pass" and ev.get("outcome") == "complete":
            diff = pass_difficulty(ev)
            if diff >= PASS_DIFFICULTY:
                return {"label": "Pass difficulty", "value": f"{diff:.1f}", "unit": "/10", "threshold": PASS_DIFFICULTY}
            if d.get("length_m", 0) >= PASS_LENGTH_M:
                return {"label": "Pass distance", "value": f"{d['length_m']:.0f}", "unit": "m", "threshold": PASS_LENGTH_M}
            if d.get("ball_speed_kmh", 0) >= BALL_SPEED_KMH:
                return {"label": "Ball speed", "value": f"{d['ball_speed_kmh']:.0f}", "unit": "km/h", "threshold": BALL_SPEED_KMH}
        if typ == "carry" and d.get("distance_m", 0) >= CARRY_M:
            return {"label": "Carry", "value": f"{d['distance_m']:.0f}", "unit": "m", "threshold": CARRY_M}
        return None

    def on_moment(self, moment: dict, ev: dict) -> list[dict[str, Any]]:
        typ = moment["type"]
        if typ not in BANNER_MOMENTS or (typ == "card" and moment["importance"] < 0.5):
            return []
        if typ in ("great_save", "big_chance_missed", "counter_attack") and moment["importance"] < 0.55:
            return []
        team = self.teams.get(moment.get("team_id") or "", {})
        return [self._cue("moment_banner", ev, {
            "label": BANNER_MOMENTS[typ], "title": moment["title"], "moment_id": moment["id"],
            "team_id": moment.get("team_id"), "color": team.get("colors", ["#ffffff"])[0],
            "player": self._player(moment["player_ids"][0]) if moment.get("player_ids") else None})]

    def lower_third(self, ev_like: dict, moment: dict, caption: str, line: str, source: str,
                    language: str = "en", profile_key: str | None = None) -> dict[str, Any]:
        return self._cue("lower_third", ev_like, {"moment_id": moment["id"], "caption": caption, "line": line,
                                                  "source": source, "language": language, "profile_key": profile_key})


def all_cues(events: list[dict]) -> list[dict[str, Any]]:
    """Every deterministic cue for a match (no AI lower-thirds), for offline renderers."""
    from .analyzer import MatchAnalyzer

    director, analyzer, cues = GraphicsDirector(events[0]), MatchAnalyzer(), []
    for ev in events:
        moments = analyzer.process(ev)
        if ev["type"] == "match_start":
            continue
        cues += director.on_event(ev)
        for m in moments:
            cues += director.on_moment(m.to_dict(), ev)
            if m.importance >= 0.45:
                cues.append(director.lower_third(ev, m.to_dict(), m.title, m.title + ".", "stats_engine"))
    return cues
