"""Per-event football metrics. Pure functions, no state.

These are the numbers the AI layer is allowed to talk about. Each one is a simple,
explainable model, so an agent can say *why* a number is what it is.
"""

from __future__ import annotations

from touchline.pitch import PITCH_LENGTH, distance_to_goal, in_box, xg

FINAL_THIRD_X = PITCH_LENGTH * 2 / 3  # 70 m
BIG_CHANCE_XG = 0.3
PENALTY_XG = 0.76


def shot_xg(ev: dict) -> float:
    """Expected goals: the probability an average player scores this shot."""
    d = ev.get("details", {})
    if d.get("subtype") == "penalty":
        return PENALTY_XG
    value = xg(ev["x"], ev["y"], header=d.get("body_part") == "head")
    if d.get("subtype") == "free_kick":
        value *= 0.6
    if ev.get("under_pressure"):
        value *= 0.8
    return value


def pass_completion_probability(ev: dict) -> float:
    """How often an average player completes a pass like this one.

    Longer passes, passes under pressure, passes into the final third or the box,
    and crosses are all harder. It rates the pass, not the passer.
    """
    d = ev.get("details", {})
    if d.get("subtype") in ("cross", "corner"):
        return 0.3
    length = d.get("length_m", 0.0)
    end_x, end_y = ev.get("end_x", ev["x"]), ev.get("end_y", ev["y"])
    p = 0.95 - 0.0045 * max(0.0, length - 12)
    p -= 0.10 if ev.get("under_pressure") else 0.0
    p -= 0.05 if end_x > FINAL_THIRD_X else 0.0
    p -= 0.08 if in_box(end_x, end_y) else 0.0
    p += {"kick_off": 0.04, "throw_in": 0.02, "free_kick": 0.02}.get(d.get("subtype", ""), 0.0)
    return min(max(p, 0.15), 0.98)


def pass_difficulty(ev: dict) -> float:
    """Pass difficulty on a 0-10 scale (0 = routine square ball, 10 = near impossible)."""
    return round(min(10.0, 10 * (1 - pass_completion_probability(ev)) / 0.8), 1)


def is_progressive(ev: dict) -> bool:
    """Moves the ball at least 25% closer to goal, and at least 10 m (standard definition)."""
    if "end_x" not in ev:
        return False
    start = distance_to_goal(ev["x"], ev["y"])
    end = distance_to_goal(ev["end_x"], ev["end_y"])
    return end <= 0.75 * start and start - end >= 10


def enters_final_third(ev: dict) -> bool:
    return "end_x" in ev and ev["x"] < FINAL_THIRD_X <= ev["end_x"]


def enters_box(ev: dict) -> bool:
    return "end_x" in ev and not in_box(ev["x"], ev["y"]) and in_box(ev["end_x"], ev["end_y"])
