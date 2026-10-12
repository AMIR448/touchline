"""Warm-up: write the demo matches' commentary, recaps and audio before anyone asks.

A judge's first visit should feel instant, not wait 5 seconds per moment. On startup (when
TOUCHLINE_WARM=on) this replays each demo match through the stats engine exactly as a viewer's
playback would, and asks the crew for every moment the default viewers get. Results land in the
same cache the playback reads, persisted to disk, so this costs AI money once, not per restart.
"""

from __future__ import annotations

import asyncio
import logging

from analytics.analyzer import MatchAnalyzer
from broadcast.models import ViewerProfile
from broadcast.relevance import wants_commentary, wants_recap

log = logging.getLogger("touchline.warm")

PROFILES = [ViewerProfile(persona="analyst", language="en"),  # Studio
            ViewerProfile(persona="casual", language="en")]   # Fan mode before choosing a club


async def warm_up(matches: dict[str, list[dict]], keys: list[str], broadcast, voice=None) -> int:
    jobs = []
    for key in keys:
        events = matches.get(key)
        if not events:
            continue
        feed_id = events[0]["match_id"]  # the id playback caches under
        a = MatchAnalyzer()
        for ev in events:
            for m in a.process(ev):
                moment = m.to_dict()
                players = {pid: a.player(pid) for pid in a.players}
                for profile in PROFILES:
                    if wants_commentary(profile, moment):
                        jobs.append(_cover(broadcast, voice, feed_id, moment, a.snapshot(), players, profile))
                    elif wants_recap(moment):
                        snap = a.snapshot(max_moments=1000)
                        jobs.append(broadcast.recap(feed_id, moment, snap, snap["moments"], players, profile))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    failed = sum(isinstance(r, Exception) for r in results)
    log.info("warm-up: %d AI results ready (%d failed)", len(results) - failed, failed)
    return len(results) - failed


async def _cover(broadcast, voice, feed_id, moment, snapshot, players, profile):
    coverage = await broadcast.cover(feed_id, moment, snapshot, players, profile)
    if voice and coverage["source"] == "ai":
        excited = (coverage.get("insight") or {}).get("angle") in ("drama", "turning_point")
        await voice.speak(coverage["line"], coverage["language"], excited)
    return coverage
