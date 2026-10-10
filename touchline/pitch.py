"""Pitch geometry shared by the simulator and the stats engine.

Coordinates are in metres on a 105 x 68 pitch. Every event is recorded from the
perspective of the team performing it, attacking toward x = 105 (the same
convention StatsBomb-style feeds use). The opponent's view of the same spot is
`mirror(x, y)`.
"""

from __future__ import annotations

import math

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
GOAL_Y = PITCH_WIDTH / 2
GOAL_WIDTH = 7.32


def mirror(x: float, y: float) -> tuple[float, float]:
    return PITCH_LENGTH - x, PITCH_WIDTH - y


def clamp_to_pitch(x: float, y: float) -> tuple[float, float]:
    return min(max(x, 0.5), PITCH_LENGTH - 0.5), min(max(y, 0.5), PITCH_WIDTH - 0.5)


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def distance_to_goal(x: float, y: float) -> float:
    return math.hypot(PITCH_LENGTH - x, GOAL_Y - y)


def in_box(x: float, y: float) -> bool:
    """Inside the opponent's penalty area."""
    return x >= PITCH_LENGTH - 16.5 and abs(y - GOAL_Y) <= 20.16


def xg(x: float, y: float, header: bool = False) -> float:
    """Expected goals for an open-play shot from (x, y).

    A small logistic model fitted by hand to typical open-play reference points
    (defenders present): ~0.3 from 6 m, ~0.16 from the penalty spot, ~0.05 from 20 m.
    Wide angles are penalised; headers are worth roughly 60% of a foot shot.
    """
    d = distance_to_goal(x, y)
    wideness = abs(y - GOAL_Y) / max(d, 1.0)  # 0 = straight on, 1 = from the byline
    logit = 0.0 - 0.15 * d - 2.2 * wideness**2
    value = 1 / (1 + math.exp(-logit))
    return value * (0.6 if header else 1.0)


def to_display(ev: dict, home_id: str) -> dict | None:
    """Event coordinates in one shared frame for drawing: home attacks left to right.

    Events are recorded from the acting team's perspective, so away-team
    coordinates are mirrored. Returns None for events without a location.
    """
    if "x" not in ev:
        return None
    flip = ev.get("team_id") != home_id

    def conv(x: float, y: float) -> list[float]:
        return [round(PITCH_LENGTH - x, 1), round(PITCH_WIDTH - y, 1)] if flip else [x, y]

    out = {"start": conv(ev["x"], ev["y"])}
    if "end_x" in ev:
        out["end"] = conv(ev["end_x"], ev["end_y"])
    return out
