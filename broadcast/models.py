"""Data passed between the broadcast crew's agents.

Agents return *structured* output (validated JSON, not free text), so each step can
rely on exactly what the previous one produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

# Latin-script languages only: player names stay verbatim and the fact-checker can verify them.
# Adding one is a one-line change here plus an <option> in the UI.
LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese"}


class ViewerProfile(BaseModel):
    """Who is watching. The same match intelligence is shaped differently per viewer."""

    persona: Literal["analyst", "casual"] = "casual"
    language: Literal["en", "es", "fr", "pt"] = "en"
    favorite_team: str | None = None  # club id, e.g. "kestrel"
    favorite_player: str | None = None  # player id, e.g. "kestrel-10"

    def key(self) -> str:
        return f"{self.persona}|{self.language}|{self.favorite_team or '-'}|{self.favorite_player or '-'}"

    def is_broadcast(self) -> bool:
        """The neutral English studio feed: the commentator already writes for it, no tailoring needed."""
        return self.persona == "analyst" and self.language == "en" and not self.favorite_team and not self.favorite_player


# ---------------------------------------------------------------- agent outputs


class SupportingNumber(BaseModel):
    label: str = Field(description="what the number is, e.g. 'Marlowe xG last 10 min'")
    value: str = Field(description="the number exactly as it appears in the facts")


class Insight(BaseModel):
    """Analyst output: what happened and why it matters."""

    headline: str = Field(description="what happened, plain and factual, max 12 words")
    why_it_matters: str = Field(description="1-2 sentences on the tactical cause or consequence")
    supporting_numbers: list[SupportingNumber] = Field(description="2-4 numbers from the facts that back the insight")
    angle: Literal["drama", "tactical", "individual", "turning_point", "routine"]


class Commentary(BaseModel):
    """Commentator output: broadcast-ready words."""

    caption: str = Field(description="on-screen lower-third, max 10 words")
    line: str = Field(description="what the commentator says, 1-2 sentences")


class PersonalizedLine(BaseModel):
    """Producer output: the commentary reshaped for one viewer."""

    caption: str = Field(description="on-screen lower-third in the viewer's language, max 10 words")
    line: str = Field(description="1-2 sentences in the viewer's language and style")


class Recap(BaseModel):
    """Recap writer output, at half-time and full-time."""

    headline: str = Field(description="newspaper-style headline, max 12 words")
    summary: list[str] = Field(description="2-3 short paragraphs telling the story of the match")
    key_numbers: list[SupportingNumber] = Field(description="3-5 numbers that explain the result")
    player_of_the_match: str = Field(description="full name of one player from the facts")
    player_reason: str = Field(description="one sentence on why")


# ---------------------------------------------------------------- workflow results


@dataclass
class Step:
    agent: str
    ms: int
    note: str = ""


@dataclass
class Coverage:
    """Final output for one moment and one viewer, plus how it was made."""

    moment_id: str
    profile_key: str
    caption: str
    line: str
    language: str
    source: Literal["ai", "fallback"]
    insight: dict[str, Any] | None = None
    trace: list[Step] = field(default_factory=list)  # which agents ran, how long, retries

    def to_dict(self) -> dict[str, Any]:
        return {
            "moment_id": self.moment_id, "profile_key": self.profile_key, "caption": self.caption,
            "line": self.line, "language": self.language, "source": self.source, "insight": self.insight,
            "trace": [vars(s) for s in self.trace],
        }
