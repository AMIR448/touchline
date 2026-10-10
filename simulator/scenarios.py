"""Scenarios: nudge a match toward a particular story.

A scenario never scripts a result. It only adjusts each team's behaviour over time
(press harder, play more directly, shoot more), so the outcome stays probabilistic
and every seed tells a different version of the story.

Game-state effects (a leading team sitting deeper late on, a trailing team chasing)
are always on, because real teams do that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .engine import MatchEngine


@dataclass
class Mods:
    press: float = 0.0  # added to tactics.press
    directness: float = 0.0  # added to tactics.directness
    tempo: float = 0.0  # added to tactics.tempo
    shot_mult: float = 1.0  # multiplies willingness to shoot
    pass_bonus: float = 0.0  # added to pass success probability
    finishing_bonus: float = 0.0  # added to conversion probability

    def __add__(self, o: "Mods") -> "Mods":
        return Mods(self.press + o.press, self.directness + o.directness, self.tempo + o.tempo,
                    self.shot_mult * o.shot_mult, self.pass_bonus + o.pass_bonus,
                    self.finishing_bonus + o.finishing_bonus)


def game_state(engine: "MatchEngine", team: int) -> Mods:
    minute = engine.minute
    diff = engine.teams[team].score - engine.teams[1 - team].score
    if minute < 60 or diff == 0:
        return Mods()
    urgency = (minute - 60) / 30  # 0 at 60', 1 at 90'
    if diff > 0:  # protecting a lead
        return Mods(press=-0.25 * urgency, directness=0.15 * urgency, tempo=-0.2 * urgency, shot_mult=0.8)
    return Mods(press=0.25 * urgency, directness=0.25 * urgency, tempo=0.2 * urgency, shot_mult=1.0 + 0.4 * urgency)


def standard(engine: "MatchEngine", team: int) -> Mods:
    return Mods()


def late_comeback(engine: "MatchEngine", team: int) -> Mods:
    """Home side is sharper early; away side finds another gear after 65'."""
    minute = engine.minute
    if team == 0 and minute < 60:
        return Mods(finishing_bonus=0.08, pass_bonus=0.02)
    if team == 1 and minute >= 65:
        return Mods(press=0.25, directness=0.2, tempo=0.2, shot_mult=1.5, finishing_bonus=0.1)
    if team == 0 and minute >= 65:
        return Mods(pass_bonus=-0.04, press=-0.15)
    return Mods()


def high_press(engine: "MatchEngine", team: int) -> Mods:
    """Both teams press relentlessly: lots of turnovers, a chaotic game."""
    return Mods(press=0.35, tempo=0.2, pass_bonus=-0.02)


def dominant_home(engine: "MatchEngine", team: int) -> Mods:
    """Home side controls the ball; away side defends deep and counters."""
    if team == 0:
        return Mods(pass_bonus=0.04, tempo=0.1, shot_mult=1.25)
    return Mods(press=-0.3, directness=0.3, pass_bonus=-0.03)


def red_card(engine: "MatchEngine", team: int) -> Mods:
    """Away side has a defender sent off around the half-hour (forced by the engine).
    Being a man down is modelled by the engine itself (fewer passing options)."""
    return Mods()


SCENARIOS: dict[str, Callable[["MatchEngine", int], Mods]] = {
    "standard": standard,
    "late_comeback": late_comeback,
    "high_press": high_press,
    "dominant_home": dominant_home,
    "red_card": red_card,
}
