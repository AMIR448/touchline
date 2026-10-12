"""Warm-up: write the demo matches' commentary, recaps and audio before anyone asks.

A judge's first visit should feel instant, and switching language or club mid-match should switch
the whole feed at once. On startup (when TOUCHLINE_WARM=on) this replays each demo match through
the stats engine exactly as a viewer's playback would, and has the crew write every moment for
every viewer combination the start screen offers (style x language x club). Per moment that is one
Analyst + Commentator pass, then the Producer tailors it for all viewers in parallel.

Results land in the cache the playback reads, persisted to disk, so this costs AI money once.
"""

from __future__ import annotations

import asyncio
import itertools
import logging

from analytics.analyzer import MatchAnalyzer
from broadcast.models import ViewerProfile
from broadcast.relevance import wants_commentary, wants_recap

log = logging.getLogger("touchline.warm")

MOMENTS_AT_ONCE = 2  # each fans out to up to 24 Producer calls: stay well inside the models' rate limits


def profiles_for(events: list[dict]) -> list[ViewerProfile]:
    d = events[0]["details"]
    return [ViewerProfile(persona=persona, language=lang, favorite_team=team)
            for persona, lang, team in itertools.product(("casual", "analyst"), ("en", "es", "fr", "pt"),
                                                         (None, d["home"]["id"], d["away"]["id"]))]


async def warm_up(matches: dict[str, list[dict]], keys: list[str], broadcast, voice=None) -> int:
    gate = asyncio.Semaphore(MOMENTS_AT_ONCE)
    jobs = []
    for key in keys:
        events = matches.get(key)
        if not events:
            continue
        feed_id = events[0]["match_id"]  # the id playback caches under
        profiles = profiles_for(events)
        a = MatchAnalyzer()
        for ev in events:
            for m in a.process(ev):
                moment = m.to_dict()
                players = {pid: a.player(pid) for pid in a.players}
                wanted = [p for p in profiles if wants_commentary(p, moment)]
                if wanted:
                    jobs.append(_cover(gate, broadcast, voice, feed_id, moment, a.snapshot(), players, wanted))
                elif wants_recap(moment):
                    snap = a.snapshot(max_moments=1000)
                    jobs += [_recap(gate, broadcast, feed_id, moment, snap, players, p) for p in profiles]
    results = await asyncio.gather(*jobs, return_exceptions=True)
    failed = [r for r in results if isinstance(r, Exception)]
    for r in failed[:3]:
        log.warning("warm-up job failed: %r", r)
    log.info("warm-up: %d jobs done, %d failed", len(results) - len(failed), len(failed))
    return len(results) - len(failed)


async def _cover(gate, broadcast, voice, feed_id, moment, snapshot, players, profiles):
    async with gate:
        coverages = await broadcast.cover_many(feed_id, moment, snapshot, players, profiles)
    if voice:  # English audio up front; other languages are synthesized on first listen (~2 s)
        for c in coverages:
            if c["source"] == "ai" and c["language"] == "en":
                excited = (c.get("insight") or {}).get("angle") in ("drama", "turning_point")
                await voice.speak(c["line"], c["language"], excited)
    return coverages


async def _recap(gate, broadcast, feed_id, moment, snapshot, players, profile):
    async with gate:
        return await broadcast.recap(feed_id, moment, snapshot, snapshot["moments"], players, profile)
