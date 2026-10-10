"""Fact-checker: every number and player name in AI-written text must come from the facts.

This is deliberately plain code, not another model: it cannot hallucinate and it cannot be
argued with. When it rejects a draft, its list of problems goes back to the agent that
wrote it, which rewrites (see crew.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)")
NON_LATIN = re.compile(r"[\u0370-\u03FF\u0400-\u052F\u0590-\u05FF\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\u4E00-\u9FFF]")
ALWAYS_ALLOWED = {45.0, 90.0}  # "first 45 minutes", "the 90 minutes"


@dataclass
class CheckResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def _numbers_in(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, str):
        for match in NUMBER.findall(value):
            yield float(match.replace(",", "."))
    elif isinstance(value, dict):
        for v in value.values():
            yield from _numbers_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _numbers_in(v)


def allowed_numbers(*contexts: Any) -> set[float]:
    """Every number in the contexts, plus the forms a writer may naturally use."""
    out = set(ALWAYS_ALLOWED)
    for ctx in contexts:
        for v in _numbers_in(ctx):
            out.add(v)
            if 0 < v < 1:
                out.add(round(v * 100))  # xG 0.19 -> "a 19% chance"
    clocks = re.findall(r"\b(\d{1,3}):\d\d\b", json.dumps(contexts, ensure_ascii=False))
    for minute in clocks:
        out.update({float(minute), float(minute) + 1})  # 55:37 is "the 56th minute" on TV
    return out


def _supported(token: str, allowed: set[float]) -> bool:
    """'3.9' is supported by 3.89 (rounding), '4' by 3.89 too; '5' is not."""
    token = token.replace(",", ".")
    decimals = len(token.split(".")[1]) if "." in token else 0
    value = float(token)
    return any(round(a, decimals) == value for a in allowed)


def check(texts: list[str], contexts: list[Any], squad_names: Iterable[str] = ()) -> CheckResult:
    allowed = allowed_numbers(*contexts)
    context_text = json.dumps(contexts, ensure_ascii=False)
    problems = []
    for text in texts:
        stray = NON_LATIN.findall(text)
        if stray:  # models occasionally leak letters from another alphabet ("Kestrел")
            problems.append(f"the text contains non-Latin characters ({''.join(sorted(set(stray)))[:10]}); use the target language's normal alphabet")
        for token in NUMBER.findall(text):
            if not _supported(token, allowed):
                problems.append(f"the number {token} is not in the facts")
        for name in squad_names:
            if name in text and name not in context_text:
                problems.append(f"{name} is not part of this moment's facts")
    return CheckResult(ok=not problems, problems=sorted(set(problems)))


def check_names_kept(texts: list[str], source_text: str, squad_names: Iterable[str]) -> CheckResult:
    """For translations: every player named in the source must appear verbatim (Latin letters)."""
    joined = " ".join(texts)
    missing = [n for n in squad_names if n in source_text and n not in joined]
    return CheckResult(ok=not missing, problems=[f"keep the player name '{n}' exactly as written, in Latin letters"
                                                 for n in missing])
