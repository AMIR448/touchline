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
# A scoreline ("2-1", "2–1"), but not part of a formation like 4-3-3 or 4-2-3-1.
# Two capitalised words in a row: how a person's name looks ("Theo Makuke"). Overlapping, so every pair is seen.
NAME_PAIR = re.compile(r"(?=(?<![\w'’-])([A-ZÀ-Þ][a-zß-ÿ'’-]+)\s+([A-ZÀ-Þ][a-zß-ÿ'’-]+))")
SCORELINE = re.compile(r"(?<![\d\-–])(\d{1,2})\s?[-–]\s?(\d{1,2})(?![\d\-–])")


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
            v = abs(v)  # the regex reads "-0.51" as "0.51", so compare magnitudes
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


def _scorelines_in(text: str):
    """(match, a, b) for each scoreline; a "50-50 ball" is not a score."""
    for m in SCORELINE.finditer(text):
        a, b = m.groups()
        if int(a) <= 15 and int(b) <= 15:
            yield m, a, b


def _without_scores(text: str) -> str:
    """The text with its scorelines blanked out, same length so character offsets still line up."""
    for m, _, _ in _scorelines_in(text):
        text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    return text


def scorelines(text: str) -> set[tuple[int, int]]:
    """Scorelines in either order: "2-1 up" and "1-2 down" are the same score."""
    return {tuple(sorted((int(a), int(b)))) for _, a, b in _scorelines_in(text)}


def _title_case(text: str) -> bool:
    """Headlines ("Kestrel City Controls Play") capitalise most words, so name-shaped pairs mean nothing there."""
    words = [w for w in re.findall(r"[^\W\d_]+", text) if len(w) >= 4]  # short words ("e", "de", "to") stay lower case
    return len(words) >= 2 and all(w[0].isupper() for w in words)


def invented_names(text: str, context_text: str) -> list[tuple[int, int, str]]:
    """Name-shaped word pairs that aren't in the facts: catches a player the writer made up, not just a wrong one.
    A capitalised first word that only starts the sentence ("Despite Marlowe ...") is not part of a name."""
    if _title_case(text):
        return []
    known = context_text.lower()
    out = []
    for m in NAME_PAIR.finditer(text):
        first, second = (re.sub(r"['’]s?$", "", w) for w in m.groups())
        start = m.start() == 0 or re.search(r"[.!?:;¡¿«\"“(]\s*$", text[:m.start()])
        if second.lower() not in known or (first.lower() not in known and not start):
            out.append((m.start(), m.start() + len(m.group(1)) + 1 + len(m.group(2)), f"{m.group(1)} {m.group(2)}"))
    return out


def check(texts: list[str], contexts: list[Any], squad_names: Iterable[str] = (),
          headlines: Iterable[str] = ()) -> CheckResult:
    """`texts` are sentences; `headlines` get every check except invented names, since a headline
    capitalises words that are not names ("Ferraro Levels with Clinical Penalty")."""
    headlines = list(headlines)
    allowed = allowed_numbers(*contexts)
    context_text = json.dumps(contexts, ensure_ascii=False)
    real_scores = scorelines(context_text) | {(0, 0)}  # "from 0-0" is always true
    problems = []
    for text in [*headlines, *texts]:
        # each number of a wrong scoreline may exist elsewhere in the facts ("3 shots"), so check the pair
        for _, a, b in _scorelines_in(text):
            if tuple(sorted((int(a), int(b)))) not in real_scores:
                problems.append(f"the score {a}-{b} is not a score from this match")
        stray = NON_LATIN.findall(text)
        if stray:  # models occasionally leak letters from another alphabet ("Kestrел")
            problems.append(f"the text contains non-Latin characters ({''.join(sorted(set(stray)))[:10]}); use the target language's normal alphabet")
        for token in NUMBER.findall(_without_scores(text)):  # scorelines were checked as pairs above
            if not _supported(token, allowed):
                problems.append(f"the number {token} is not in the facts")
        for name in squad_names:
            if name in text and name not in context_text:
                problems.append(f"{name} is not part of this moment's facts")
        for _, _, name in ([] if text in headlines else invented_names(text, context_text)):
            problems.append(f"{name} is not a name in the facts")
    return CheckResult(ok=not problems, problems=sorted(set(problems)))


def check_names_kept(texts: list[str], source_text: str, squad_names: Iterable[str]) -> CheckResult:
    """For translations: every player named in the source must appear verbatim (Latin letters)."""
    joined = " ".join(texts)
    missing = [n for n in squad_names if n in source_text and n not in joined]
    return CheckResult(ok=not missing, problems=[f"keep the player name '{n}' exactly as written, in Latin letters"
                                                 for n in missing])


def marks(text: str, contexts: list[Any], squad_names: Iterable[str] = ()) -> list[dict]:
    """Every fact in `text` and whether the facts back it up, as character spans for the UI to highlight.
    Same rules as check(); used by the "try to fool the fact-checker" box."""
    allowed = allowed_numbers(*contexts)
    context_text = json.dumps(contexts, ensure_ascii=False)
    real_scores = scorelines(context_text) | {(0, 0)}
    out = []
    for m, a, b in _scorelines_in(text):
        out.append({"start": m.start(), "end": m.end(), "kind": "score", "ok": tuple(sorted((int(a), int(b)))) in real_scores})
    for m in NUMBER.finditer(_without_scores(text)):
        out.append({"start": m.start(), "end": m.end(), "kind": "number", "ok": _supported(m.group(1), allowed)})
    for name in squad_names:
        for m in re.finditer(re.escape(name), text):
            out.append({"start": m.start(), "end": m.end(), "kind": "name", "ok": name in context_text})
    for start, end, _ in invented_names(text, context_text):
        out.append({"start": start, "end": end, "kind": "name", "ok": False})
    return sorted(out, key=lambda x: x["start"])
