"""Match engine: simulates a football match as a stream of on-ball events.

How it works, in one paragraph: the ball is always with one player. Each step that
player may be pressed by an opponent, then chooses an action (pass, carry,
dribble, shoot) with probabilities that depend on where he is, his skills and his
team's tactics. The outcome is rolled with probabilities shaped by distance,
pressure and fatigue. Failed actions hand the ball to the other team; the ball
going out or a foul leads to a set piece. The clock advances by realistic
amounts for each action, so a match lasts 90 minutes plus added time.

Coordinates in emitted events are in the acting team's frame (see geometry.py).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .clubs import Club, Player
from touchline.pitch import (GOAL_Y, PITCH_LENGTH, PITCH_WIDTH, clamp_to_pitch, distance,
                       distance_to_goal, in_box, mirror, xg)
from .scenarios import SCENARIOS, Mods, game_state
from .schema import Event

HALF = 45 * 60

# Seconds the ball is dead before each kind of restart.
DEAD_TIME = {
    "kick_off": (40, 70), "throw_in": (6, 14), "goal_kick": (14, 24),
    "corner": (20, 34), "free_kick": (16, 36), "penalty": (50, 80),
}


@dataclass
class Side:
    club: Club
    lineup: list[Player | None]  # aligned with club.slots; None = sent off
    bench: list[Player]
    entered: dict[str, float]  # player id -> minute they came on
    sub_minutes: list[float]
    score: int = 0
    yellows: set[str] = field(default_factory=set)

    def active(self) -> list[tuple[int, Player]]:
        return [(i, p) for i, p in enumerate(self.lineup) if p is not None]

    def goalkeeper(self) -> Player:
        return self.lineup[0]  # slot 0 is always GK


class MatchEngine:
    def __init__(self, home: Club, away: Club, seed: int = 0, scenario: str = "standard"):
        if scenario not in SCENARIOS:
            raise SystemExit(f"Unknown scenario '{scenario}'. Options: {', '.join(SCENARIOS)}")
        self.rng = random.Random(seed)
        self.seed = seed
        self.scenario_name = scenario
        self.scenario = SCENARIOS[scenario]
        self.match_id = f"{home.short}-{away.short}-{seed}".lower()
        self.teams = [self._new_side(home), self._new_side(away)]
        self.events: list[Event] = []
        self.period = 1
        self.t = 0.0  # match clock, seconds (second half starts at 2700)
        self.elapsed = 0.0  # seconds of play since kick-off, used for replay timing
        self.poss_team = 0
        self.possession = 0
        self.holder: Player = home.starters[-1]
        self.ball = (52.5, 34.0)  # in the possessing team's frame
        self.assist_candidate: Event | None = None
        self.red_card_at = self.rng.uniform(25, 40) if scenario == "red_card" else None

    # ------------------------------------------------------------------ setup / helpers

    def _new_side(self, club: Club) -> Side:
        n_subs = self.rng.randint(3, 5)
        minutes = sorted(self.rng.uniform(55, 87) for _ in range(n_subs))
        return Side(club, list(club.starters), list(club.bench),
                    {p.id: 0.0 for p in club.starters}, minutes)

    @property
    def minute(self) -> int:
        return int(self.t // 60)

    def clock(self) -> str:
        # Feed convention: the clock keeps running in added time (46:30 in period 1 = 45+1:30).
        return f"{int(self.t // 60):02d}:{int(self.t % 60):02d}"

    def tick(self, seconds: float) -> None:
        self.t += seconds
        self.elapsed += seconds

    def score(self) -> dict[str, int]:
        return {s.club.id: s.score for s in self.teams}

    def mods(self, team: int) -> Mods:
        return self.scenario(self, team) + game_state(self, team)

    def tactics(self, team: int) -> tuple[float, float, float]:
        t, m = self.teams[team].club.tactics, self.mods(team)

        def c(v: float) -> float:
            return min(max(v, 0.0), 1.0)

        return c(t.press + m.press), c(t.directness + m.directness), c(t.tempo + m.tempo)

    def fatigue(self, team: int, player: Player) -> float:
        on_for = self.minute - self.teams[team].entered.get(player.id, 0.0)
        return 0.05 * max(on_for, 0) / 90

    def positions(self, team: int) -> list[tuple[Player, tuple[float, float]]]:
        """Approximate position of every player of `team`, in that team's own frame.
        Players hold their formation slot, shifted toward the ball."""
        bx, by = self.ball if team == self.poss_team else mirror(*self.ball)
        side = self.teams[team]
        out = []
        for i, p in side.active():
            _label, role, x0, y0 = side.club.slots[i]
            if role == "GK":
                out.append((p, (x0, y0)))
                continue
            shift = (bx - 52.5) * 0.45 - (6 if team != self.poss_team else 0)
            x = min(x0 + shift, 88.0)  # the opposing back line holds attackers near the box edge
            out.append((p, clamp_to_pitch(x, y0 + (by - GOAL_Y) * 0.25)))
        return out

    def nearest(self, team: int, point: tuple[float, float], exclude: str | None = None):
        cands = [(p, pos) for p, pos in self.positions(team) if p.id != exclude and p.role != "GK"]
        return min(cands, key=lambda c: distance(c[1], point))

    def emit(self, type: str, team: int | None = None, player: Player | None = None,
             loc: tuple[float, float] | None = None, end: tuple[float, float] | None = None,
             outcome: str | None = None, under_pressure: bool = False,
             related: list[Event] | None = None, **details) -> Event:
        ev = Event(
            event_id=f"{self.match_id}-{len(self.events) + 1:05d}",
            match_id=self.match_id,
            seq=len(self.events) + 1,
            type=type,
            period=self.period,
            clock=self.clock(),
            ts_ms=int(self.elapsed * 1000),
            team_id=self.teams[team].club.id if team is not None else None,
            player_id=player.id if player else None,
            possession=self.possession if team is not None else None,
            x=round(loc[0], 1) if loc else None,
            y=round(loc[1], 1) if loc else None,
            end_x=round(end[0], 1) if end else None,
            end_y=round(end[1], 1) if end else None,
            outcome=outcome,
            under_pressure=under_pressure,
            related=[r.event_id for r in related or []],
            details={k: v for k, v in details.items() if v is not None},
        )
        self.events.append(ev)
        return ev

    def set_possession(self, team: int, player: Player, loc: tuple[float, float]) -> None:
        if team != self.poss_team:
            self.possession += 1
            self.poss_team = team
            self.assist_candidate = None
        self.holder = player
        self.ball = clamp_to_pitch(*loc)

    def hold_time(self, team: int) -> float:
        _, _, tempo = self.tactics(team)
        return self.rng.uniform(0.6, 2.2) * (1.4 - 0.6 * tempo)

    # ------------------------------------------------------------------ match flow

    def run(self) -> list[dict]:
        home, away = self.teams
        self.emit("match_start", **{
            "home": home.club.to_dict(), "away": away.club.to_dict(),
            "venue": f"{home.club.name} Stadium", "scenario": self.scenario_name, "seed": self.seed,
        })
        first = self.rng.randint(0, 1)
        for period, kicker in ((1, first), (2, 1 - first)):
            self.period = period
            self.t = float(HALF * (period - 1))
            added = self.rng.randint(1, 4) if period == 1 else self.rng.randint(3, 8)
            self.emit("period_start")
            self.kickoff(kicker, after_goal=False)
            while self.t < HALF * period + added * 60:
                self._maybe_scripted_red_card()
                self.step()
            self.emit("period_end", score=self.score(), added_minutes=added)
        winner = None if home.score == away.score else (home if home.score > away.score else away).club.id
        self.emit("match_end", score=self.score(), winner=winner)
        return [e.to_dict() for e in self.events]

    def kickoff(self, team: int, after_goal: bool) -> None:
        if after_goal:
            self.tick(self.rng.uniform(*DEAD_TIME["kick_off"]))
        self.make_subs()
        side = self.teams[team]
        striker = next((p for _, p in reversed(side.active()) if p.role in ("FW", "MF")), side.active()[-1][1])
        if team == self.poss_team:
            self.possession += 1  # a kick-off always starts a new possession
        self.set_possession(team, striker, (52.5, 34.0))
        self.pass_(subtype="kick_off")

    def restart(self, team: int, kind: str, loc: tuple[float, float]) -> None:
        """Dead ball: wait, make any due substitutions, then take the set piece."""
        self.tick(self.rng.uniform(*DEAD_TIME[kind]))
        self.make_subs()
        self.assist_candidate = None
        side = self.teams[team]
        if kind == "goal_kick":
            self.set_possession(team, side.goalkeeper(), (5.5, GOAL_Y))
            self.pass_(subtype="goal_kick")
        elif kind == "corner":
            taker = max((p for _, p in side.active() if p.role in ("MF", "FW")), key=lambda p: p.passing)
            self.set_possession(team, taker, loc)
            self.pass_(subtype="corner")
        elif kind == "penalty":
            taker = max((p for _, p in side.active() if p.role != "GK"), key=lambda p: p.finishing)
            self.set_possession(team, taker, (PITCH_LENGTH - 11, GOAL_Y))
            self.shot(subtype="penalty")
        elif kind == "free_kick":
            if loc[0] > 78 and abs(loc[1] - GOAL_Y) < 18 and self.rng.random() < 0.45:
                taker = max((p for _, p in side.active() if p.role != "GK"), key=lambda p: p.finishing)
                self.set_possession(team, taker, loc)
                self.shot(subtype="free_kick")
            else:
                taker, _ = self.nearest(team, loc)
                self.set_possession(team, taker, loc)
                self.pass_(subtype="free_kick")
        else:  # throw_in
            taker, _ = self.nearest(team, loc)
            self.set_possession(team, taker, loc)
            self.pass_(subtype="throw_in")

    def step(self) -> None:
        team, opp = self.poss_team, 1 - self.poss_team
        x, y = self.ball
        press, _, _ = self.tactics(opp)
        under = False
        # Pressing teams hunt the ball high up; everyone presses near their own box.
        if self.rng.random() < 0.06 + 0.45 * press * (1 - x / PITCH_LENGTH) + 0.15 * x / PITCH_LENGTH:
            presser, _ = self.nearest(opp, mirror(x, y))
            pressure = self.emit("pressure", opp, presser, mirror(x, y))
            self.tick(self.rng.uniform(0.4, 1.2))
            under = True
            r = self.rng.random()
            if r < 0.045:
                self.foul(opp, presser)
                return
            if r < 0.045 + 0.015 + 0.06 * press:  # the press wins the ball outright
                loc = mirror(x, y)
                self.emit("tackle", opp, presser, loc, outcome="won", related=[pressure])
                self.set_possession(opp, presser, loc)
                self.tick(self.hold_time(opp))
                return
        action = self.choose_action(team, under)
        if action == "shot":
            self.shot(under=under)
        elif action == "dribble":
            self.dribble(under)
        elif action == "carry":
            self.carry()
        else:
            self.pass_(under=under)

    def choose_action(self, team: int, under: bool) -> str:
        h, (x, y) = self.holder, self.ball
        if h.role == "GK":
            return "pass"
        p_shot = 0.0
        if x > 72:
            base = 0.02 if distance_to_goal(x, y) < 28 else 0.004  # speculative long shots are rare
            p_shot = (base + 0.6 * xg(x, y)) * self.mods(team).shot_mult * (0.7 + h.finishing / 200)
            p_shot *= 0.8 if under else 1.0
        p_dribble = 0.025 + 0.05 * h.dribbling / 100 + (0.03 if x > 70 else 0.0)
        p_carry = 0.05 if under else 0.16
        r = self.rng.random()
        if r < p_shot:
            return "shot"
        if r < p_shot + p_dribble:
            return "dribble"
        if r < p_shot + p_dribble + p_carry:
            return "carry"
        return "pass"

    # ------------------------------------------------------------------ actions

    def pass_(self, under: bool = False, subtype: str | None = None) -> None:
        team, opp = self.poss_team, 1 - self.poss_team
        h, (x, y) = self.holder, self.ball
        _, direct, _ = self.tactics(team)
        m = self.mods(team)
        cands = [(p, pos) for p, pos in self.positions(team) if p.id != h.id]
        cross = subtype is None and x > 75 and abs(y - GOAL_Y) > 15 and self.rng.random() < 0.35
        aerial = cross or subtype == "corner"

        if aerial:
            role_w = {"FW": 3.0, "MF": 1.5, "DF": 1.2, "GK": 0.0}
            receiver, _ = self.rng.choices(cands, [role_w[p.role] for p, _ in cands])[0]
            end = (self.rng.uniform(93, 101), self.rng.uniform(26, 42))
        else:
            weights = []
            for p, pos in cands:
                d = distance((x, y), pos)
                gain = pos[0] - x
                w = math.exp(-abs(d - (14 + 16 * direct)) / 9) * math.exp(gain * (0.015 + 0.05 * direct))
                if p.role == "GK":
                    w *= 0.15 if (under and x < 40) else 0.02
                if d < 4:
                    w *= 0.2
                weights.append(w)
            receiver, pos = self.rng.choices(cands, weights)[0]
            end = clamp_to_pitch(pos[0] + self.rng.gauss(0, 2), pos[1] + self.rng.gauss(0, 2))

        length = distance((x, y), end)
        height = "high" if aerial or length > 32 else ("low" if length > 18 else "ground")

        if aerial:
            p_ok = 0.28 + (h.passing - 70) * 0.004
        else:
            p_ok = 0.95 + (h.passing - 70) * 0.004 - 0.0045 * max(0.0, length - 12)
            p_ok -= 0.10 if under else 0.0
            p_ok -= 0.05 if end[0] > 70 else 0.0
            p_ok -= 0.08 if in_box(*end) else 0.0
            p_ok += {"kick_off": 0.06, "throw_in": 0.04, "free_kick": 0.03}.get(subtype or "", 0.0)
        p_ok += m.pass_bonus - self.fatigue(team, h)
        p_ok += 0.03 * (len(self.teams[team].active()) - len(self.teams[opp].active()))  # man advantage
        p_ok = min(max(p_ok, 0.15), 0.98)

        speed = min(max(22 + length * 1.4 + self.rng.gauss(0, 6), 15), 105)  # km/h
        flight = length / max(speed / 3.6 * 0.75, 4)
        common = dict(length_m=round(length, 1), ball_speed_kmh=round(speed, 1), height=height,
                      subtype=subtype or ("cross" if cross else None))

        if self.rng.random() < p_ok:
            ev = self.emit("pass", team, h, (x, y), end, "complete", under, recipient_id=receiver.id, **common)
            self.tick(flight)
            self.holder, self.ball = receiver, end
            self.assist_candidate = ev
            if aerial and in_box(*end) and self.rng.random() < 0.4:
                self.tick(0.3)
                self.shot(header=True)
            else:
                self.tick(self.hold_time(team))
            return

        if self.rng.random() < 0.78 or in_box(*end):
            # Cut out somewhere along the ball's path.
            f = self.rng.uniform(0.55, 1.0)
            cut = (x + (end[0] - x) * f, y + (end[1] - y) * f)
            ev = self.emit("pass", team, h, (x, y), cut, "incomplete", under, intended_recipient_id=receiver.id, **common)
            self.tick(flight * f)
            loc = mirror(*cut)
            defender, _ = self.nearest(opp, loc)
            if aerial and self.rng.random() < 0.6:
                clear_to = clamp_to_pitch(loc[0] + self.rng.uniform(20, 40), loc[1] + self.rng.gauss(0, 12))
                self.emit("clearance", opp, defender, loc, clear_to, related=[ev])
                self.tick(1.5)
                self.loose_ball(opp, clear_to)
            elif aerial and self.rng.random() < 0.5:
                self.emit("clearance", opp, defender, loc, (0.0, loc[1]), related=[ev])
                self.restart(team, "corner", (104.5, 0.5 if end[1] < GOAL_Y else 67.5))
            else:
                kind = "interception" if self.rng.random() < 0.15 else "ball_recovery"
                self.emit(kind, opp, defender, loc, related=[ev])
                self.set_possession(opp, defender, loc)
                self.tick(self.hold_time(opp))
            return

        # Out of play.
        if end[0] >= 100:
            out = (PITCH_LENGTH, end[1])
            self.emit("pass", team, h, (x, y), out, "out", under, **common)
            self.tick(flight)
            self.restart(opp, "goal_kick", (5.5, GOAL_Y))
        else:
            edge = 0.0 if end[1] < GOAL_Y else PITCH_WIDTH
            out = (end[0], edge)
            self.emit("pass", team, h, (x, y), out, "out", under, **common)
            self.tick(flight)
            self.restart(opp, "throw_in", mirror(end[0], edge))

    def carry(self) -> None:
        team, h, (x, y) = self.poss_team, self.holder, self.ball
        dist = self.rng.uniform(3, 9 + h.pace / 10)
        angle = self.rng.gauss(0, 0.6)
        end = clamp_to_pitch(x + dist * math.cos(angle), y + dist * math.sin(angle))
        self.emit("carry", team, h, (x, y), end, distance_m=round(distance((x, y), end), 1))
        self.tick(dist / (2 + 6 * h.pace / 100))
        self.ball = end

    def dribble(self, under: bool) -> None:
        team, opp = self.poss_team, 1 - self.poss_team
        h, (x, y) = self.holder, self.ball
        defender, _ = self.nearest(opp, mirror(x, y))
        if self.rng.random() < 0.5 + (h.dribbling - defender.tackling) / 150:
            end = clamp_to_pitch(x + self.rng.uniform(4, 9), y + self.rng.gauss(0, 4))
            self.emit("dribble", team, h, (x, y), end, "complete", under, opponent_id=defender.id)
            self.tick(self.rng.uniform(1.5, 3))
            self.ball = end
            return
        ev = self.emit("dribble", team, h, (x, y), outcome="incomplete", under_pressure=under, opponent_id=defender.id)
        self.tick(self.rng.uniform(1, 2))
        if self.rng.random() < 0.22:
            self.foul(opp, defender)
            return
        loc = mirror(x, y)
        self.emit("tackle", opp, defender, loc, outcome="won", related=[ev])
        self.set_possession(opp, defender, loc)
        self.tick(self.hold_time(opp))

    def shot(self, under: bool = False, header: bool = False, subtype: str | None = None) -> None:
        team, opp = self.poss_team, 1 - self.poss_team
        h, (x, y) = self.holder, self.ball
        if subtype == "penalty":
            chance = 0.76
        else:
            chance = xg(x, y, header) * (0.6 if subtype == "free_kick" else 1.0) * (0.8 if under else 1.0)
        conv = chance * (0.7 + h.finishing / 300) * (1 + 3 * self.mods(team).finishing_bonus)

        if self.rng.random() < conv:
            outcome = "goal"
        else:
            blocked_w = 0.05 if subtype == "penalty" or header else 0.28
            outcome = self.rng.choices(["blocked", "saved", "off_target"], [blocked_w, 0.27, 0.45])[0]

        assist = self.assist_candidate
        if assist and assist.details.get("recipient_id") != h.id:
            assist = None
        speed = self.rng.uniform(35, 65) if header else min(self.rng.uniform(60, 105) + (x - 80) * 0.3, 125)
        on_target = outcome in ("goal", "saved")
        end_y = GOAL_Y + self.rng.uniform(-3.4, 3.4) if on_target else GOAL_Y + self.rng.choice([-1, 1]) * self.rng.uniform(3.8, 9)
        end = (PITCH_LENGTH, end_y) if outcome != "blocked" else clamp_to_pitch(x + self.rng.uniform(1, 4), y)
        ev = self.emit("shot", team, h, (x, y), end, outcome, under,
                       related=[assist] if assist else None,
                       body_part="head" if header else self.rng.choice(["right_foot", "right_foot", "left_foot"]),
                       shot_speed_kmh=round(speed, 1), subtype=subtype)
        self.tick(max(distance((x, y), end) / (speed / 3.6), 0.3))

        if outcome == "goal":
            side = self.teams[team]
            side.score += 1
            self.emit("goal", team, h, (x, y), related=[ev],
                      assist_player_id=assist.player_id if assist else None, score=self.score())
            self.assist_candidate = None
            self.kickoff(opp, after_goal=True)
        elif outcome == "saved":
            gk = self.teams[opp].goalkeeper()
            self.emit("goalkeeper_save", opp, gk, (2, PITCH_WIDTH - end_y), related=[ev])
            if self.rng.random() < 0.45:
                self.restart(team, "corner", (104.5, 0.5 if y < GOAL_Y else 67.5))
            else:
                self.set_possession(opp, gk, (5, GOAL_Y))
                self.tick(self.rng.uniform(3, 8))
        elif outcome == "blocked":
            if self.rng.random() < 0.45:
                self.restart(team, "corner", (104.5, 0.5 if y < GOAL_Y else 67.5))
            else:
                self.loose_ball(team, (x - self.rng.uniform(3, 12), y + self.rng.gauss(0, 6)))
        elif self.rng.random() < 0.12:  # deflected behind
            self.restart(team, "corner", (104.5, 0.5 if y < GOAL_Y else 67.5))
        else:
            self.restart(opp, "goal_kick", (5.5, GOAL_Y))

    def loose_ball(self, last_team: int, loc: tuple[float, float]) -> None:
        """A 50/50: either side may recover it. `loc` is in last_team's frame."""
        loc = clamp_to_pitch(*loc)
        if self.rng.random() < 0.5:
            winner, wloc = last_team, loc
        else:
            winner, wloc = 1 - last_team, mirror(*loc)
        player, _ = self.nearest(winner, wloc)
        self.emit("ball_recovery", winner, player, wloc)
        self.set_possession(winner, player, wloc)
        self.tick(self.hold_time(winner))

    def foul(self, fouling_team: int, fouler: Player) -> None:
        fouled = 1 - fouling_team
        side = self.teams[fouling_team]
        loc = self.ball if self.poss_team == fouled else mirror(*self.ball)
        ev = self.emit("foul", fouling_team, fouler, mirror(*loc))
        r = self.rng.random()
        booked = fouler.id in side.yellows
        card = None
        if r < 0.004:
            card = "red"
        elif r < (0.03 if booked else 0.18):  # booked players tread carefully
            card = "second_yellow" if booked else "yellow"
            side.yellows.add(fouler.id)
        if card:
            self.emit("card", fouling_team, fouler, related=[ev], card=card)
            if card in ("red", "second_yellow"):
                self.send_off(fouling_team, fouler)
        if in_box(*loc):
            self.restart(fouled, "penalty", (PITCH_LENGTH - 11, GOAL_Y))
        else:
            self.restart(fouled, "free_kick", loc)

    def send_off(self, team: int, player: Player) -> None:
        side = self.teams[team]
        side.lineup = [None if p is not None and p.id == player.id else p for p in side.lineup]
        if player.id == self.holder.id:
            self.holder = self.nearest(team, self.ball if team == self.poss_team else mirror(*self.ball))[0]

    def _maybe_scripted_red_card(self) -> None:
        """`red_card` scenario: an away defender sees red for a professional foul."""
        if self.red_card_at is None or self.minute < self.red_card_at or self.poss_team != 0:
            return
        self.red_card_at = None
        side = self.teams[1]
        defender = next(p for _, p in side.active() if p.role == "DF")
        ev = self.emit("foul", 1, defender, mirror(*self.ball), note="professional foul")
        self.emit("card", 1, defender, related=[ev], card="red")
        self.send_off(1, defender)
        self.restart(0, "free_kick", self.ball)

    def make_subs(self) -> None:
        for ti, side in enumerate(self.teams):
            while side.sub_minutes and side.sub_minutes[0] <= self.minute:
                side.sub_minutes.pop(0)
                options = [p for p in side.bench if p.role != "GK"]
                if not options:
                    continue
                on = self.rng.choice(options)
                same_role = [(i, p) for i, p in side.active() if p.role == on.role]
                if not same_role:
                    continue
                # Take off the player who has been on longest (ties broken randomly).
                i, off = min(same_role, key=lambda ip: (side.entered.get(ip[1].id, 0), self.rng.random()))
                side.lineup[i] = on
                side.bench.remove(on)
                side.entered[on.id] = self.minute
                if self.holder.id == off.id:
                    self.holder = on
                self.emit("substitution", ti, on, player_off_id=off.id, player_on_id=on.id,
                          position=side.club.slots[i][0])


def simulate(home: Club, away: Club, seed: int = 0, scenario: str = "standard") -> list[dict]:
    return MatchEngine(home, away, seed, scenario).run()
