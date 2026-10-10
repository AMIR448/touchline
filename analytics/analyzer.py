"""MatchAnalyzer: feed it events one at a time, as a live feed would deliver them.

    analyzer = MatchAnalyzer()
    for event in stream:
        for moment in analyzer.process(event):
            ...  # a key moment was detected: hand it to the agents
    analyzer.snapshot()  # full current state: scores, stats, momentum, players

Two kinds of output:
- **State** (`snapshot()`): running team/player stats plus per-minute timelines of
  xG, momentum and control-vs-chaos. This is what overlays and dashboards draw.
- **Moments** (`process()` return value): things worth talking about, each with
  structured `facts` and the `evidence` event IDs behind them. No prose here:
  turning facts into words is the agents' job, and the evidence lets them cite it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .metrics import (BIG_CHANCE_XG, FINAL_THIRD_X, enters_box, enters_final_third, is_progressive,
                      pass_difficulty, shot_xg)

ON_BALL = {"pass", "carry", "dribble", "shot"}
BALL_WINS = {"interception", "ball_recovery", "tackle"}
HALFWAY = 52.5
MIN_PPDA_ACTIONS = 5  # PPDA is a ratio: with only 1-2 defensive actions it swings wildly, so wait for data
PPDA_LINE = 42.0  # defensive actions beyond this line (defender's frame) count as pressing high

# Threat values feeding the momentum model.
THREAT_FINAL_THIRD = 0.015
THREAT_BOX_ENTRY = 0.04
THREAT_CORNER = 0.02
THREAT_SHOT_BASE = 0.02


@dataclass
class Moment:
    id: str
    type: str
    period: int
    clock: str
    ts_ms: int
    team_id: str | None
    player_ids: list[str]
    importance: float  # 0..1: how much a broadcast would care
    title: str  # short factual label; the agents write the real words
    facts: dict[str, Any]
    evidence: list[str]  # event_ids that back the facts

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TeamStats:
    goals: int = 0
    possession_ms: int = 0
    passes: int = 0
    passes_completed: int = 0
    pass_difficulty_sum: float = 0.0
    progressive_passes: int = 0
    final_third_entries: int = 0
    box_entries: int = 0
    final_third_touches: int = 0
    touches: int = 0
    shots: int = 0
    on_target: int = 0
    xg: float = 0.0
    big_chances: int = 0
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    pressures: int = 0
    tackles: int = 0
    interceptions: int = 0
    recoveries: int = 0
    saves: int = 0
    opp_passes_own_60: int = 0  # PPDA numerator: opponent passes in their defensive 60%
    high_def_actions: int = 0  # PPDA denominator: our tackles/interceptions/fouls (+ pressure-forced recoveries) there


@dataclass
class PlayerStats:
    id: str
    name: str
    team_id: str
    number: int
    position: str
    on_pitch: bool
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    pass_difficulty_sum: float = 0.0
    progressive_passes: int = 0
    key_passes: int = 0
    assists: int = 0
    shots: int = 0
    on_target: int = 0
    xg: float = 0.0
    goals: int = 0
    dribbles: int = 0
    dribbles_won: int = 0
    tackles: int = 0
    interceptions: int = 0
    recoveries: int = 0
    pressures: int = 0
    fouls: int = 0
    saves: int = 0
    carry_m: float = 0.0
    max_shot_speed_kmh: float = 0.0
    max_pass_speed_kmh: float = 0.0
    yellow_cards: int = 0
    red_cards: int = 0


@dataclass
class Bucket:
    """One minute of play for one team. Rolling windows are sums of buckets."""

    possession_ms: int = 0
    passes: int = 0
    completed: int = 0
    on_ball: int = 0
    pressured: int = 0  # on-ball actions under pressure
    lost_ball: int = 0  # possession changes where this team lost the ball
    threat: float = 0.0
    xg: float = 0.0
    shots: int = 0
    box_entries: int = 0
    opp_passes_own_60: int = 0
    high_def_actions: int = 0


@dataclass
class Chain:
    """One possession: from winning the ball to losing it."""

    possession: int
    team_id: str
    start_ts: int
    start_x: float
    start_how: str  # interception / ball_recovery / tackle / throw_in / kick_off / ...
    events: list[str] = field(default_factory=list)
    passes: int = 0
    players: list[str] = field(default_factory=list)
    shot_ids: list[str] = field(default_factory=list)
    counter_reported: bool = False


def zone(x: float) -> str:
    """Pitch third in words, from the acting team's point of view."""
    return "own third" if x < 35 else ("middle third" if x < 70 else "final third")


def _pct(a: float, b: float) -> int:
    return round(100 * a / b) if b else 0


class MatchAnalyzer:
    def __init__(self) -> None:
        self.match_id = ""
        self.teams: list[str] = []
        self.names: dict[str, str] = {}
        self.team_names: dict[str, str] = {}
        self.team: dict[str, TeamStats] = {}
        self.players: dict[str, PlayerStats] = {}
        self.buckets: dict[str, list[Bucket]] = {}
        self.moments: list[Moment] = []
        self._new: list[Moment] = []
        self.clock, self.period, self.ts_ms, self.status = "00:00", 0, 0, "pre_match"
        self.minute_idx = 0  # completed minutes of play
        self.minute_labels: list[int] = []  # clock minute shown for each minute index
        self.owner: str | None = None
        self.last_ts: int | None = None
        self.chain: Chain | None = None
        self.last_win: dict[str, dict] = {}
        self.passes_by_id: dict[str, dict] = {}
        self.last_shot: dict | None = None
        self.trailed: dict[str, bool] = {}
        self.momentum_timeline: list[float] = []
        self.chaos_timeline: list[int] = []
        self.xg_timeline: list[dict[str, float]] = []
        self.momentum_leader: str | None = None
        self.cooldown: dict[str, int] = defaultdict(lambda: -999)  # detector key -> minute idx last fired

    # ------------------------------------------------------------------ public API

    def process(self, ev: dict) -> list[Moment]:
        self._new = []
        typ = ev["type"]
        if typ == "match_start":
            self._on_match_start(ev)
            return self._new
        self.clock, self.period, self.ts_ms = ev["clock"], ev["period"], ev["ts_ms"]
        self._advance_minutes(ev["ts_ms"] // 60_000)
        self._track_possession(ev)
        if typ in ON_BALL:
            self._track_chain(ev)
        handler = getattr(self, f"_on_{typ}", None)
        if handler:
            handler(ev)
        return self._new

    def snapshot(self, max_moments: int = 20) -> dict[str, Any]:
        h, a = self.teams
        total_poss = sum(self.team[t].possession_ms for t in self.teams) or 1
        return {
            "match": {
                "id": self.match_id, "status": self.status, "period": self.period, "clock": self.clock,
                "home": {"id": h, "name": self.team_names[h]}, "away": {"id": a, "name": self.team_names[a]},
                "score": {t: self.team[t].goals for t in self.teams},
            },
            "teams": {t: self._team_view(t, total_poss) for t in self.teams},
            "last_10_min": {t: self._window(t, 10) for t in self.teams},
            "momentum": {
                "value": self.momentum_timeline[-1] if self.momentum_timeline else 0.0,
                "explainer": "-1 = away dominating, +1 = home dominating (threat over the last ~10 min)",
                "timeline": self.momentum_timeline,
            },
            "control_chaos": {
                "value": self.chaos_timeline[-1] if self.chaos_timeline else 0,
                "label": self._chaos_label(self.chaos_timeline[-1]) if self.chaos_timeline else "n/a",
                "explainer": "0 = calm, controlled build-up; 100 = end-to-end chaos (turnovers, pressure, misplaced passes)",
                "timeline": self.chaos_timeline,
            },
            "xg_timeline": self.xg_timeline,
            "minute_labels": self.minute_labels,
            "top_players": [self._player_view(p) for p in self._top_players(6)],
            "moments": [m.to_dict() for m in self.moments[-max_moments:]],
        }

    def player(self, player_id: str) -> dict[str, Any]:
        return self._player_view(self.players[player_id])

    # ------------------------------------------------------------------ views

    def _team_view(self, t: str, total_poss: int) -> dict[str, Any]:
        s = self.team[t]
        return {
            "name": self.team_names[t],
            "goals": s.goals,
            "possession_pct": _pct(s.possession_ms, total_poss),
            "passes": s.passes,
            "pass_accuracy_pct": _pct(s.passes_completed, s.passes),
            "avg_pass_difficulty": round(s.pass_difficulty_sum / s.passes, 1) if s.passes else 0.0,
            "progressive_passes": s.progressive_passes,
            "final_third_entries": s.final_third_entries,
            "box_entries": s.box_entries,
            "field_tilt_pct": _pct(s.final_third_touches, sum(self.team[x].final_third_touches for x in self.teams)),
            "shots": s.shots,
            "on_target": s.on_target,
            "xg": round(s.xg, 2),
            "big_chances": s.big_chances,
            "corners": s.corners,
            "fouls": s.fouls,
            "yellow_cards": s.yellow_cards,
            "red_cards": s.red_cards,
            "pressures": s.pressures,
            "tackles": s.tackles,
            "interceptions": s.interceptions,
            "recoveries": s.recoveries,
            "saves": s.saves,
            "ppda": round(s.opp_passes_own_60 / s.high_def_actions, 1) if s.high_def_actions >= MIN_PPDA_ACTIONS else None,
        }

    def _window(self, t: str, minutes: int) -> dict[str, Any]:
        """Team stats over the last `minutes` completed minutes."""
        mine = self.buckets[t][-minutes:]
        theirs = self.buckets[self._opp(t)][-minutes:]
        poss = sum(b.possession_ms for b in mine)
        total = poss + sum(b.possession_ms for b in theirs)
        passes = sum(b.passes for b in mine)
        acts = sum(b.high_def_actions for b in mine)
        return {
            "possession_pct": _pct(poss, total),
            "passes": passes,
            "pass_accuracy_pct": _pct(sum(b.completed for b in mine), passes),
            "shots": sum(b.shots for b in mine),
            "xg": round(sum(b.xg for b in mine), 2),
            "box_entries": sum(b.box_entries for b in mine),
            "ppda": round(sum(b.opp_passes_own_60 for b in mine) / acts, 1) if acts >= MIN_PPDA_ACTIONS else None,
        }

    def _player_view(self, p: PlayerStats) -> dict[str, Any]:
        d = asdict(p)
        d["pass_accuracy_pct"] = _pct(p.passes_completed, p.passes)
        d["avg_pass_difficulty"] = round(p.pass_difficulty_sum / p.passes, 1) if p.passes else 0.0
        d["xg"] = round(p.xg, 2)
        d["carry_m"] = round(p.carry_m)
        d["impact"] = round(self._impact(p), 2)
        del d["pass_difficulty_sum"]
        return d

    @staticmethod
    def _impact(p: PlayerStats) -> float:
        """Simple, explainable contribution score used to pick top performers."""
        return (p.goals + 0.7 * p.assists + 0.5 * p.xg + 0.2 * p.key_passes + 0.03 * p.progressive_passes
                + 0.05 * (p.tackles + p.interceptions) + 0.03 * p.dribbles_won + 0.1 * p.saves)

    def _top_players(self, n: int) -> list[PlayerStats]:
        return sorted(self.players.values(), key=self._impact, reverse=True)[:n]

    @staticmethod
    def _chaos_label(v: int) -> str:
        return "controlled" if v < 35 else ("balanced" if v < 60 else "chaotic")

    # ------------------------------------------------------------------ bookkeeping

    def _opp(self, t: str) -> str:
        return self.teams[1] if t == self.teams[0] else self.teams[0]

    def _bucket(self, t: str) -> Bucket:
        return self.buckets[t][-1]

    def _moment(self, type: str, ev: dict, importance: float, title: str, facts: dict,
                evidence: list[str], players: list[str] | None = None, team: str | None = None) -> None:
        m = Moment(
            id=f"{self.match_id}-m{len(self.moments) + 1:03d}", type=type, period=ev["period"], clock=ev["clock"],
            ts_ms=ev["ts_ms"], team_id=team if team is not None else ev.get("team_id"),
            player_ids=players if players is not None else ([ev["player_id"]] if ev.get("player_id") else []),
            importance=round(min(importance, 1.0), 2), title=title, facts=facts, evidence=evidence,
        )
        self.moments.append(m)
        self._new.append(m)

    def _score_str(self) -> str:
        h, a = self.teams
        return f"{self.team_names[h]} {self.team[h].goals}-{self.team[a].goals} {self.team_names[a]}"

    def _on_match_start(self, ev: dict) -> None:
        d = ev["details"]
        self.match_id = ev["match_id"]
        self.status = "live"
        for side in (d["home"], d["away"]):
            t = side["id"]
            self.teams.append(t)
            self.team_names[t] = side["name"]
            self.team[t] = TeamStats()
            self.buckets[t] = [Bucket()]
            self.trailed[t] = False
            for p, starter in [(p, True) for p in side["starters"]] + [(p, False) for p in side["bench"]]:
                self.names[p["id"]] = p["name"]
                self.players[p["id"]] = PlayerStats(p["id"], p["name"], t, p["number"],
                                                    p.get("position", p["role"]), starter)

    def _advance_minutes(self, minute_idx: int) -> None:
        while self.minute_idx < minute_idx:
            self._end_minute()
            self.minute_idx += 1
            for t in self.teams:
                self.buckets[t].append(Bucket())

    def _track_possession(self, ev: dict) -> None:
        """Possession = time between events, credited to the team on the ball.
        Gaps are capped at 8 s so dead-ball stoppages don't count."""
        if self.owner and self.last_ts is not None:
            gap = min(ev["ts_ms"] - self.last_ts, 8000)
            self.team[self.owner].possession_ms += gap
            self._bucket(self.owner).possession_ms += gap
        self.last_ts = ev["ts_ms"]
        typ = ev["type"]
        if typ in ON_BALL or typ in ("interception", "ball_recovery") or (typ == "tackle" and ev.get("outcome") == "won"):
            new = ev["team_id"]
            if self.owner and new != self.owner:
                self._bucket(self.owner).lost_ball += 1
            self.owner = new

    def _track_chain(self, ev: dict) -> None:
        if self.chain is None or ev["possession"] != self.chain.possession:
            if self.chain:
                self._end_chain(self.chain, ev)
            win = self.last_win.get(ev["team_id"])
            if win and ev["ts_ms"] - win["ts_ms"] < 5000:
                how, sx, sts = win["type"], win["x"], win["ts_ms"]
            else:
                how, sx, sts = ev.get("details", {}).get("subtype") or "open_play", ev["x"], ev["ts_ms"]
            self.chain = Chain(ev["possession"], ev["team_id"], sts, sx, how)
        c = self.chain
        c.events.append(ev["event_id"])
        if ev["player_id"] not in c.players:
            c.players.append(ev["player_id"])
        if ev["type"] == "pass" and ev["outcome"] == "complete":
            c.passes += 1

    def _end_chain(self, c: Chain, ev: dict) -> None:
        if c.passes >= 15:
            ended_in_shot = bool(c.shot_ids)
            self._moment(
                "sustained_possession", ev, 0.3 + min(c.passes, 30) / 100 + (0.15 if ended_in_shot else 0.0),
                f"{self.team_names[c.team_id]} string together {c.passes} passes",
                {"team": self.team_names[c.team_id], "passes": c.passes, "players_involved": len(c.players),
                 "duration_s": round((ev["ts_ms"] - c.start_ts) / 1000), "ended_in_shot": ended_in_shot},
                c.events[-12:], players=c.players[:6], team=c.team_id,
            )

    # ------------------------------------------------------------------ per-minute detectors

    def _end_minute(self) -> None:
        h, a = self.teams
        self.minute_labels.append(int(self.clock.split(":")[0]))
        self.xg_timeline.append({t: round(self.team[t].xg, 2) for t in self.teams})

        # Momentum: recent threat, recent minutes weighted more.
        def recent_threat(t: str) -> float:
            return sum(b.threat * 0.8 ** age for age, b in enumerate(reversed(self.buckets[t][-10:])))

        th, ta = recent_threat(h), recent_threat(a)
        momentum = round((th - ta) / (th + ta + 0.08), 2)
        self.momentum_timeline.append(momentum)

        # Control vs chaos over the last 5 minutes (both teams together).
        win = [b for t in self.teams for b in self.buckets[t][-5:]]
        minutes = min(5, len(self.buckets[h]))
        turnovers_per_min = sum(b.lost_ball for b in win) / minutes
        on_ball = sum(b.on_ball for b in win)
        pressured = sum(b.pressured for b in win) / on_ball if on_ball else 0.0
        passes = sum(b.passes for b in win)
        misplaced = 1 - sum(b.completed for b in win) / passes if passes else 0.0
        raw = 100 * (0.45 * min(turnovers_per_min / 4, 1) + 0.35 * min(pressured / 0.4, 1)
                     + 0.20 * min(misplaced / 0.35, 1))
        chaos = round(min(max(50 + (raw - 55) * 2.2, 0), 100))  # centre a typical game on 50
        prev_chaos = self.chaos_timeline[-1] if self.chaos_timeline else 0
        self.chaos_timeline.append(chaos)

        anchor = {"period": self.period, "clock": self.clock, "ts_ms": self.ts_ms}
        idx = self.minute_idx
        if idx < 8:
            return

        # Momentum swing: a different team takes clear control.
        leader = h if momentum > 0.4 else (a if momentum < -0.4 else None)
        if leader and leader != self.momentum_leader and idx - self.cooldown["momentum"] >= 8:
            previous = self.momentum_leader
            self.momentum_leader = leader
            self.cooldown["momentum"] = idx
            self._moment(
                "momentum_swing", anchor, 0.5,
                f"Momentum swings to {self.team_names[leader]}" if previous else f"{self.team_names[leader]} take control",
                {"team": self.team_names[leader], "momentum": momentum,
                 "previous_leader": self.team_names[previous] if previous else None,
                 "last_10_min": {self.team_names[t]: self._window(t, 10) for t in self.teams}},
                [], team=leader,
            )

        # Chaos spike: the game opens up.
        if chaos >= 80 > prev_chaos and idx - self.cooldown["chaos"] >= 15:
            self.cooldown["chaos"] = idx
            self._moment(
                "chaos_spike", anchor, 0.35, "The game opens up",
                {"control_chaos": chaos, "turnovers_per_min": round(turnovers_per_min, 1),
                 "pressured_share_pct": round(100 * pressured), "misplaced_pass_pct": round(100 * misplaced)},
                [], team=None,
            )

        # Pressing spell: a team wins the ball high, again and again.
        for t in self.teams:
            last8 = self.buckets[t][-8:]
            acts = sum(b.high_def_actions for b in last8)
            opp_passes = sum(b.opp_passes_own_60 for b in last8)
            ppda = opp_passes / acts if acts else 99
            if acts >= 6 and ppda <= 6.5 and idx - self.cooldown[f"press:{t}"] >= 20:
                self.cooldown[f"press:{t}"] = idx
                self._moment(
                    "pressing_spell", anchor, 0.4, f"{self.team_names[t]} press high",
                    {"team": self.team_names[t], "ppda_last_8_min": round(ppda, 1), "high_ball_wins": acts,
                     "match_ppda": self._team_view(t, 1)["ppda"],
                     "ppda_explainer": "opponent passes allowed per defensive action; lower = more intense press"},
                    [], team=t,
                )

    # ------------------------------------------------------------------ event handlers

    def _touch(self, ev: dict) -> PlayerStats:
        p = self.players[ev["player_id"]]
        p.touches += 1
        s = self.team[ev["team_id"]]
        s.touches += 1
        b = self._bucket(ev["team_id"])
        b.on_ball += 1
        b.pressured += 1 if ev.get("under_pressure") else 0
        return p

    def _entries(self, ev: dict) -> None:
        s, b = self.team[ev["team_id"]], self._bucket(ev["team_id"])
        if enters_final_third(ev):
            s.final_third_entries += 1
            b.threat += THREAT_FINAL_THIRD
        if enters_box(ev):
            s.box_entries += 1
            b.box_entries += 1
            b.threat += THREAT_BOX_ENTRY
        if ev.get("end_x", 0) >= FINAL_THIRD_X:
            s.final_third_touches += 1

    def _on_pass(self, ev: dict) -> None:
        t, d = ev["team_id"], ev.get("details", {})
        p, s, b = self._touch(ev), self.team[t], self._bucket(t)
        diff = pass_difficulty(ev)
        complete = ev["outcome"] == "complete"
        p.passes += 1
        s.passes += 1
        b.passes += 1
        p.pass_difficulty_sum += diff
        s.pass_difficulty_sum += diff
        p.max_pass_speed_kmh = max(p.max_pass_speed_kmh, d.get("ball_speed_kmh", 0))
        if d.get("subtype") == "corner":
            s.corners += 1
            b.threat += THREAT_CORNER
        if ev["x"] <= 105 - PPDA_LINE:  # pass in the passer's own 60%: counts toward the opponent's PPDA
            self.team[self._opp(t)].opp_passes_own_60 += 1
            self._bucket(self._opp(t)).opp_passes_own_60 += 1
        self.passes_by_id[ev["event_id"]] = {"player_id": ev["player_id"], "difficulty": diff,
                                             "recipient_id": d.get("recipient_id"),
                                             "under_pressure": ev.get("under_pressure", False)}
        if not complete:
            return
        p.passes_completed += 1
        s.passes_completed += 1
        b.completed += 1
        if is_progressive(ev):
            p.progressive_passes += 1
            s.progressive_passes += 1
        self._entries(ev)
        if p.passes_completed == 50 and p.passes_completed / p.passes >= 0.9:
            self._moment("milestone", ev, 0.25, f"{p.name}: 50 completed passes",
                         {"player": p.name, "kind": "50_completed_passes",
                          "pass_accuracy_pct": _pct(p.passes_completed, p.passes)}, [ev["event_id"]])

    def _on_carry(self, ev: dict) -> None:
        p = self._touch(ev)
        p.carry_m += ev.get("details", {}).get("distance_m", 0.0)
        self._entries(ev)

    def _on_dribble(self, ev: dict) -> None:
        p = self._touch(ev)
        p.dribbles += 1
        if ev["outcome"] == "complete":
            p.dribbles_won += 1
            self._entries(ev)

    def _on_shot(self, ev: dict) -> None:
        t, d = ev["team_id"], ev.get("details", {})
        p, s, b = self._touch(ev), self.team[t], self._bucket(t)
        value = shot_xg(ev)
        on_target = ev["outcome"] in ("goal", "saved")
        p.shots += 1
        s.shots += 1
        b.shots += 1
        p.xg += value
        s.xg += value
        b.xg += value
        b.threat += THREAT_SHOT_BASE + value
        p.on_target += on_target
        s.on_target += on_target
        s.big_chances += value >= BIG_CHANCE_XG
        speed = d.get("shot_speed_kmh", 0)
        p.max_shot_speed_kmh = max(p.max_shot_speed_kmh, speed)
        ev = {**ev, "_xg": value}
        self.last_shot = ev
        if self.chain:
            self.chain.shot_ids.append(ev["event_id"])

        # Key pass: the pass that set up this shot.
        assist = next((self.passes_by_id[r] for r in ev.get("related", []) if r in self.passes_by_id), None)
        if assist:
            passer = self.players[assist["player_id"]]
            passer.key_passes += 1
            if assist["difficulty"] >= 6 and value >= 0.1:
                self._moment(
                    "key_pass", ev, 0.4 + value / 2, f"Defence-splitting pass by {passer.name}",
                    {"passer": passer.name, "shooter": p.name, "pass_difficulty": assist["difficulty"],
                     "shot_xg": round(value, 2), "shot_outcome": ev["outcome"]},
                    ev["related"] + [ev["event_id"]], players=[passer.id, p.id],
                )

        # Counter-attack: ball won in own half, shot within 15 seconds.
        c = self.chain
        if (c and not c.counter_reported and c.start_how in BALL_WINS and c.start_x < HALFWAY
                and ev["ts_ms"] - c.start_ts <= 15_000 and value >= 0.05):
            c.counter_reported = True
            self._moment(
                "counter_attack", ev, 0.35 + value, f"{self.team_names[t]} break at speed",
                {"team": self.team_names[t], "won_ball_by": c.start_how.replace("_", " "), "won_ball_in": zone(c.start_x),
                 "seconds_to_shot": round((ev["ts_ms"] - c.start_ts) / 1000, 1), "passes": c.passes,
                 "shot_xg": round(value, 2), "shot_outcome": ev["outcome"]},
                c.events[-8:], players=c.players[:5],
            )

        if value >= BIG_CHANCE_XG and ev["outcome"] in ("off_target", "blocked"):
            self._moment(
                "big_chance_missed", ev, 0.5 + value / 2, f"Big chance missed by {p.name}",
                {"player": p.name, "team": self.team_names[t], "xg": round(value, 2), "outcome": ev["outcome"],
                 "body_part": d.get("body_part"), "distance_to_goal_m": round(((105 - ev["x"]) ** 2 + (34 - ev["y"]) ** 2) ** 0.5, 1),
                 "score": self._score_str()},
                [ev["event_id"]],
            )
        if speed >= 105 and ev["outcome"] != "goal":
            self._moment("thunderbolt", ev, 0.3 + (0.1 if on_target else 0), f"{p.name} lets fly at {speed:.0f} km/h",
                         {"player": p.name, "shot_speed_kmh": speed, "outcome": ev["outcome"]}, [ev["event_id"]])

    def _on_goal(self, ev: dict) -> None:
        t, opp = ev["team_id"], self._opp(ev["team_id"])
        before_us, them = self.team[t].goals, self.team[opp].goals
        self.team[t].goals += 1
        p = self.players[ev["player_id"]]
        p.goals += 1
        assist_id = ev.get("details", {}).get("assist_player_id")
        if assist_id:
            self.players[assist_id].assists += 1
        after_us = before_us + 1

        if before_us + them == 0:
            state = "opener"
        elif before_us < them:
            state = "equaliser" if after_us == them else "pulls_one_back"
        elif before_us == them:
            state = "completes_comeback" if self.trailed[t] else "takes_lead"
        else:
            state = "extends_lead"
        if after_us < them:
            self.trailed[t] = True
        if them < after_us:
            self.trailed[opp] = True
        late = int(ev["clock"].split(":")[0]) >= 85 and ev["period"] == 2
        decisive = state in ("equaliser", "takes_lead", "completes_comeback")

        shot = self.last_shot or {}
        c = self.chain
        facts = {
            "scorer": p.name, "team": self.team_names[t], "assist": self.names.get(assist_id),
            "score_after": self._score_str(), "game_state": state, "late": late,
            "shot_xg": round(shot.get("_xg", 0), 2), "body_part": shot.get("details", {}).get("body_part"),
            "shot_speed_kmh": shot.get("details", {}).get("shot_speed_kmh"),
            "shot_type": shot.get("details", {}).get("subtype") or "open_play",
            "scorer_goals_today": p.goals,
        }
        evidence = [ev["event_id"]]
        if c and c.team_id == t and facts["shot_type"] == "open_play":
            facts["build_up"] = {"passes": c.passes, "players_involved": len(c.players),
                                 "duration_s": round((ev["ts_ms"] - c.start_ts) / 1000, 1),
                                 "started_by": c.start_how.replace("_", " "), "started_in": zone(c.start_x)}
            evidence = c.events[-10:] + evidence
        self._moment("goal", ev, 0.9 + (0.1 if late and decisive else 0.0),
                     f"GOAL! {p.name} ({self._score_str()})", facts, evidence,
                     players=[p.id] + ([assist_id] if assist_id else []))
        if p.goals in (2, 3):
            kind = "brace" if p.goals == 2 else "hat_trick"
            self._moment("milestone", ev, 0.5 if p.goals == 2 else 0.8, f"{p.name}: {kind.replace('_', '-')}",
                         {"player": p.name, "kind": kind, "goals": p.goals, "xg_today": round(p.xg, 2)},
                         [ev["event_id"]])

    def _defensive(self, ev: dict, attr: str) -> PlayerStats:
        t = ev["team_id"]
        p = self.players[ev["player_id"]]
        setattr(p, attr, getattr(p, attr) + 1)
        setattr(self.team[t], attr, getattr(self.team[t], attr) + 1)
        forced = attr == "recoveries" and any(self.passes_by_id.get(r, {}).get("under_pressure") for r in ev.get("related", []))
        if ev.get("x", 0) >= PPDA_LINE and (attr in ("tackles", "interceptions", "fouls") or forced):
            self.team[t].high_def_actions += 1
            self._bucket(t).high_def_actions += 1
        return p

    def _on_pressure(self, ev: dict) -> None:
        self._defensive(ev, "pressures")

    def _on_tackle(self, ev: dict) -> None:
        self._defensive(ev, "tackles")
        self.last_win[ev["team_id"]] = ev

    def _on_interception(self, ev: dict) -> None:
        self._defensive(ev, "interceptions")
        self.last_win[ev["team_id"]] = ev

    def _on_ball_recovery(self, ev: dict) -> None:
        self._defensive(ev, "recoveries")
        self.last_win[ev["team_id"]] = ev

    def _on_foul(self, ev: dict) -> None:
        self._defensive(ev, "fouls")

    def _on_goalkeeper_save(self, ev: dict) -> None:
        gk = self._defensive(ev, "saves")
        shot = self.last_shot
        if shot and shot["_xg"] >= 0.2:
            shooter = self.players[shot["player_id"]]
            self._moment(
                "great_save", ev, 0.45 + shot["_xg"] / 2, f"Great save by {gk.name}",
                {"goalkeeper": gk.name, "team": self.team_names[ev["team_id"]], "shooter": shooter.name,
                 "shot_xg": round(shot["_xg"], 2), "shot_speed_kmh": shot.get("details", {}).get("shot_speed_kmh"),
                 "score": self._score_str()},
                [shot["event_id"], ev["event_id"]], players=[gk.id, shooter.id],
            )

    def _on_card(self, ev: dict) -> None:
        t, card = ev["team_id"], ev["details"]["card"]
        p = self.players[ev["player_id"]]
        if card == "yellow":
            p.yellow_cards += 1
            self.team[t].yellow_cards += 1
            self._moment("card", ev, 0.2, f"Yellow card: {p.name}",
                         {"player": p.name, "team": self.team_names[t], "card": card,
                          "team_yellows": self.team[t].yellow_cards}, [ev["event_id"]] + ev.get("related", []))
            return
        p.red_cards += 1
        p.on_pitch = False
        self.team[t].red_cards += 1
        players_left = sum(1 for x in self.players.values() if x.team_id == t and x.on_pitch)
        self._moment("card", ev, 0.85, f"{'Second yellow' if card == 'second_yellow' else 'Red card'}: {p.name}",
                     {"player": p.name, "team": self.team_names[t], "card": card, "players_left": players_left,
                      "score": self._score_str()}, [ev["event_id"]] + ev.get("related", []))

    def _on_substitution(self, ev: dict) -> None:
        d = ev["details"]
        off, on = self.players[d["player_off_id"]], self.players[d["player_on_id"]]
        off.on_pitch, on.on_pitch = False, True
        self._moment("substitution", ev, 0.15, f"Substitution: {on.name} on for {off.name}",
                     {"team": self.team_names[ev["team_id"]], "player_on": on.name, "player_off": off.name,
                      "position": d.get("position"), "player_off_impact": round(self._impact(off), 2)},
                     [ev["event_id"]], players=[on.id, off.id])

    def _on_period_end(self, ev: dict) -> None:
        self._advance_minutes(self.minute_idx + 1)  # close the running minute
        kind = "half_time" if ev["period"] == 1 else "full_time"
        if kind == "full_time":
            self.status = "full_time"
        h, a = self.teams
        self._moment(kind, ev, 0.7, f"{kind.replace('_', '-').title()}: {self._score_str()}",
                     {"score": self._score_str(), "stats": {self.team_names[t]: self._team_view(t, sum(
                         self.team[x].possession_ms for x in self.teams) or 1) for t in (h, a)}},
                     [ev["event_id"]], team=None)
