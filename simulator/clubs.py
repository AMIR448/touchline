"""Fictional clubs and players.

Everything here is invented: no real clubs, players or trademarks. Each club has
a distinct tactical identity so different fixtures produce different stories
(possession vs pressing vs direct vs low block).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Formation slots: (label, role, base_x, base_y) in the team's own attacking frame.
# base_x/base_y is where that player stands when the ball is in midfield.
FORMATIONS: dict[str, list[tuple[str, str, float, float]]] = {
    "4-3-3": [
        ("GK", "GK", 5, 34), ("RB", "DF", 30, 8), ("RCB", "DF", 22, 24), ("LCB", "DF", 22, 44),
        ("LB", "DF", 30, 60), ("RCM", "MF", 50, 20), ("CDM", "MF", 42, 34), ("LCM", "MF", 50, 48),
        ("RW", "FW", 76, 10), ("ST", "FW", 84, 34), ("LW", "FW", 76, 58),
    ],
    "4-2-3-1": [
        ("GK", "GK", 5, 34), ("RB", "DF", 30, 8), ("RCB", "DF", 22, 24), ("LCB", "DF", 22, 44),
        ("LB", "DF", 30, 60), ("RDM", "MF", 42, 26), ("LDM", "MF", 42, 42), ("RAM", "MF", 66, 12),
        ("CAM", "MF", 66, 34), ("LAM", "MF", 66, 56), ("ST", "FW", 85, 34),
    ],
    "4-4-2": [
        ("GK", "GK", 5, 34), ("RB", "DF", 30, 8), ("RCB", "DF", 22, 24), ("LCB", "DF", 22, 44),
        ("LB", "DF", 30, 60), ("RM", "MF", 56, 10), ("RCM", "MF", 48, 26), ("LCM", "MF", 48, 42),
        ("LM", "MF", 56, 58), ("RS", "FW", 80, 27), ("LS", "FW", 80, 41),
    ],
    "5-3-2": [
        ("GK", "GK", 5, 34), ("RWB", "DF", 40, 6), ("RCB", "DF", 22, 20), ("CB", "DF", 18, 34),
        ("LCB", "DF", 22, 48), ("LWB", "DF", 40, 62), ("RCM", "MF", 46, 22), ("CM", "MF", 42, 34),
        ("LCM", "MF", 46, 46), ("RS", "FW", 78, 27), ("LS", "FW", 78, 41),
    ],
}

BENCH_ROLES = ["GK", "DF", "DF", "MF", "MF", "FW", "FW"]


@dataclass(frozen=True)
class Tactics:
    """Team style, each value 0..1."""

    press: float  # how aggressively they win the ball back high up the pitch
    directness: float  # preference for forward/long passes over keeping the ball
    tempo: float  # how quickly they move the ball on


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    number: int
    role: str  # GK / DF / MF / FW
    # Skills, 0..100
    passing: int
    finishing: int
    dribbling: int
    tackling: int
    pace: int


@dataclass(frozen=True)
class Club:
    id: str
    name: str
    short: str
    colors: tuple[str, str]
    formation: str
    tactics: Tactics
    strength: int  # overall quality, shifts every skill
    starters: list[Player] = field(default_factory=list)
    bench: list[Player] = field(default_factory=list)

    @property
    def slots(self) -> list[tuple[str, str, float, float]]:
        return FORMATIONS[self.formation]

    def to_dict(self) -> dict:
        def p(pl: Player, slot: str | None = None) -> dict:
            d = {"id": pl.id, "name": pl.name, "number": pl.number, "role": pl.role}
            if slot:
                d["position"] = slot
            return d

        return {
            "id": self.id,
            "name": self.name,
            "short": self.short,
            "colors": list(self.colors),
            "formation": self.formation,
            "slots": [list(s) for s in self.slots],  # (label, role, x, y): lets a UI draw the team's shape
            "tactics": vars(self.tactics),
            "starters": [p(pl, s[0]) for pl, s in zip(self.starters, self.slots)],
            "bench": [p(pl) for pl in self.bench],
        }


FIRST_NAMES = [
    "Theo", "Mateo", "Kofi", "Luca", "Idris", "Jonah", "Rafael", "Emil", "Tariq", "Callum",
    "Nico", "Sami", "Felix", "Arlo", "Yusuf", "Dario", "Oskar", "Malik", "Elias", "Jude",
    "Hugo", "Ilan", "Kai", "Marek", "Ruben", "Soren", "Tomas", "Ade", "Bastian", "Leon",
    "Noah", "Omar", "Pablo", "Rio", "Silas", "Tobin", "Vito", "Wes", "Zane", "Amos",
]
LAST_NAMES = [
    "Ashworth", "Okonkwo", "Varga", "Lindqvist", "Moreau", "Castell", "Brennan", "Haddad",
    "Kowal", "Delacroix", "Mbeki", "Sorensen", "Quill", "Ferraro", "Achterberg", "Navarro",
    "Pryce", "Halloran", "Duarte", "Kimura", "Osei", "Valente", "Rask", "Thorne", "Abara",
    "Larkin", "Esposito", "Wilde", "Nakata", "Fairbrook", "Ilic", "Gallo", "Strand", "Mensah",
    "Corrigan", "Baptiste", "Holm", "Petrak", "Sato", "Vidal", "Okafor", "Lund", "Marchetti",
]

# Skill profile per role: (passing, finishing, dribbling, tackling, pace) before club strength.
ROLE_PROFILE = {
    "GK": (55, 5, 20, 30, 45),
    "DF": (66, 30, 50, 76, 66),
    "MF": (78, 55, 70, 62, 68),
    "FW": (68, 78, 76, 35, 80),
}


def _make_player(rng: random.Random, club_id: str, idx: int, number: int, role: str, strength: int) -> Player:
    base = ROLE_PROFILE[role]
    shift = strength - 75

    def skill(b: int) -> int:
        return max(5, min(99, round(b + shift + rng.gauss(0, 6))))

    return Player(
        id=f"{club_id}-{idx:02d}",
        name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        number=number,
        role=role,
        passing=skill(base[0]),
        finishing=skill(base[1]),
        dribbling=skill(base[2]),
        tackling=skill(base[3]),
        pace=skill(base[4]),
    )


def _build_club(id: str, name: str, short: str, colors: tuple[str, str], formation: str,
                tactics: Tactics, strength: int) -> Club:
    # Seeded by club id, so squads are identical on every run.
    rng = random.Random(f"squad:{id}")
    starters, bench = [], []
    numbers = iter(rng.sample(range(2, 40), 30))
    for i, (_label, role, _x, _y) in enumerate(FORMATIONS[formation]):
        starters.append(_make_player(rng, id, i + 1, 1 if role == "GK" else next(numbers), role, strength))
    for j, role in enumerate(BENCH_ROLES):
        bench.append(_make_player(rng, id, 12 + j, next(numbers), role, strength - 3))
    return Club(id, name, short, colors, formation, tactics, strength, starters, bench)


CLUBS: dict[str, Club] = {
    c.id: c
    for c in [
        _build_club("kestrel", "Kestrel City", "KES", ("#5BB8F0", "#FFFFFF"), "4-3-3",
                    Tactics(press=0.65, directness=0.25, tempo=0.6), strength=80),
        _build_club("marlowe", "Marlowe United", "MAR", ("#C8102E", "#111111"), "4-2-3-1",
                    Tactics(press=0.85, directness=0.45, tempo=0.85), strength=78),
        _build_club("portellis", "Port Ellis Rovers", "PER", ("#1F3A93", "#F2C500"), "4-4-2",
                    Tactics(press=0.4, directness=0.8, tempo=0.55), strength=72),
        _build_club("ashvale", "Ashvale Athletic", "ASH", ("#2E7D32", "#FFFFFF"), "5-3-2",
                    Tactics(press=0.2, directness=0.6, tempo=0.4), strength=70),
    ]
}


def get_club(club_id: str) -> Club:
    try:
        return CLUBS[club_id]
    except KeyError:
        raise SystemExit(f"Unknown club '{club_id}'. Options: {', '.join(CLUBS)}")
