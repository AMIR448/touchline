"""The event format: the contract between the simulator and every later stage.

Events hold *raw facts only* (who, what, where, when, outcome, measured ball speed).
Derived numbers such as xG, pass difficulty or momentum belong to the stats engine.

See docs/EVENTS.md for the full field reference.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

EVENT_TYPES = {
    # match flow
    "match_start", "period_start", "period_end", "match_end", "substitution",
    # on-ball actions
    "pass", "carry", "dribble", "shot", "goal",
    # defensive actions
    "pressure", "tackle", "interception", "clearance", "ball_recovery", "goalkeeper_save",
    # discipline
    "foul", "card",
}


@dataclass
class Event:
    event_id: str
    match_id: str
    seq: int
    type: str
    period: int
    clock: str  # "mm:ss" match clock; keeps running in added time ("47:10" in period 1 = 45+2:10)
    ts_ms: int  # milliseconds of play since kick-off (half-time excluded); used for live replay
    team_id: str | None = None
    player_id: str | None = None
    possession: int | None = None  # possession sequence number; changes when the ball changes team
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    under_pressure: bool = False
    related: list[str] = field(default_factory=list)  # event_ids this event responds to
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop empty fields to keep the stream compact.
        return {k: v for k, v in d.items() if v not in (None, [], {}) and not (k == "under_pressure" and not v)}
