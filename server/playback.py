"""Live playback of one match for one viewer.

A virtual match clock runs at `speed` x real time. Whenever the clock passes an
event's `ts_ms`, the event goes through the stats engine and out to the viewer,
followed by any key moments it triggered and (at most once a second) the full
match state. The viewer can pause, change speed, jump to a minute or restart.

When a broadcast service is attached, moments this viewer cares about are sent to the
AI crew in the background; their tailored commentary (and the half/full-time recap)
arrives as separate `commentary` / `recap` messages a few seconds later. The game never
waits for the AI.

Message protocol: see docs/STREAMING.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from analytics.analyzer import MatchAnalyzer
from analytics.graphics import GraphicsDirector
from broadcast.models import ViewerProfile
from broadcast.relevance import wants_commentary, wants_recap
from touchline.pitch import to_display

log = logging.getLogger("touchline.playback")

Send = Callable[[dict[str, Any]], Awaitable[None]]

STATE_INTERVAL_S = 1.0
MAX_SPEED = 600.0


class Playback:
    def __init__(self, events: list[dict], send: Send, speed: float = 10.0, autoplay: bool = True,
                 broadcast=None, profile: ViewerProfile | None = None):
        self.events = events
        self.match_id = events[0]["match_id"]
        self.broadcast = broadcast  # BroadcastService or None (AI off)
        self.profile = profile or ViewerProfile()
        self.ai_tasks: set[asyncio.Task] = set()
        self.send = send
        self.home_id = events[0]["details"]["home"]["id"]
        self.speed = speed
        self.playing = autoplay
        self.analyzer = MatchAnalyzer()
        self.director = GraphicsDirector(events[0])
        self.idx = 0
        self.base_ts = 0  # match ms at the last (re)base
        self.base_wall = time.monotonic()
        self.last_state = 0.0
        self.changed = asyncio.Event()  # set by commands to wake the loop

    # ------------------------------------------------------------------ clock

    def match_ts(self) -> float:
        if not self.playing:
            return self.base_ts
        return self.base_ts + (time.monotonic() - self.base_wall) * 1000 * self.speed

    def _rebase(self, ts: float | None = None) -> None:
        self.base_ts = self.match_ts() if ts is None else ts
        self.base_wall = time.monotonic()

    # ------------------------------------------------------------------ commands

    async def command(self, msg: dict[str, Any]) -> None:
        cmd = msg.get("cmd")
        if cmd == "play":
            self._rebase()
            self.playing = True
        elif cmd == "pause":
            self._rebase()
            self.playing = False
        elif cmd == "speed":
            self._rebase()
            self.speed = min(max(float(msg.get("value", 10)), 0.25), MAX_SPEED)
        elif cmd == "seek":
            await self._seek(self._ts_for_minute(int(msg.get("minute", 0))))
        elif cmd == "restart":
            await self._seek(0)
            self.playing = True
        elif cmd == "profile":
            try:
                self.profile = ViewerProfile(**{k: v for k, v in msg.items() if k != "cmd"})
            except ValidationError as e:
                await self.send({"type": "error", "message": f"bad profile: {e.errors()[0]['msg']}"})
                return
            await self.send({"type": "profile", "profile": self.profile.model_dump()})
        else:
            await self.send({"type": "error", "message": f"unknown command: {cmd!r}"})
            return
        self.changed.set()
        await self.send_status()

    def _ts_for_minute(self, minute: int) -> float:
        """Match-clock minute -> play time. Minutes 45+ mean the second half, so
        "60'" lands on 60:00 on the clock, not 60 minutes of play."""
        period = 1 if minute < 45 else 2
        for ev in self.events[1:]:
            if ev["period"] > period or (ev["period"] == period and int(ev["clock"].split(":")[0]) >= minute):
                return max(ev["ts_ms"] - 1, 0)
        return self.events[-1]["ts_ms"]

    async def _seek(self, target_ms: float) -> None:
        """Rebuild the state up to `target_ms` instantly, then carry on live from there."""
        self.cancel_ai()  # commentary for moments we jumped away from is no longer wanted
        self.analyzer = MatchAnalyzer()
        self.director = GraphicsDirector(self.events[0])
        self.idx = 0
        while self.idx < len(self.events) and (self.idx == 0 or self.events[self.idx]["ts_ms"] <= target_ms):
            ev = self.events[self.idx]
            self.analyzer.process(ev)
            if ev["type"] != "match_start":
                self.director.on_event(ev)  # keep score/holder in sync, cues not sent
            self.idx += 1
        self._rebase(target_ms)
        last = self.events[self.idx - 1]
        await self.send({"type": "reset", "event": self._with_display(last), "state": self.analyzer.snapshot()})

    # ------------------------------------------------------------------ output

    def _with_display(self, ev: dict) -> dict:
        disp = to_display(ev, self.home_id)
        return {**ev, "display": disp} if disp else ev

    async def send_status(self) -> None:
        last = self.events[max(self.idx - 1, 0)]
        await self.send({"type": "status", "playing": self.playing, "speed": self.speed,
                         "ts_ms": int(self.match_ts()), "clock": last.get("clock", "00:00"),
                         "finished": self.idx >= len(self.events)})

    async def _emit(self, ev: dict) -> None:
        moments = self.analyzer.process(ev)
        await self.send({"type": "event", "event": self._with_display(ev)})
        for cue in self.director.on_event(ev):
            await self.send({"type": "cue", "cue": cue})
        for m in moments:
            moment = m.to_dict()
            await self.send({"type": "moment", "moment": moment})
            for cue in self.director.on_moment(moment, ev):
                await self.send({"type": "cue", "cue": cue})
            self._maybe_commentate(moment)
        now = time.monotonic()
        if moments or now - self.last_state >= STATE_INTERVAL_S or ev["type"] == "match_end":
            self.last_state = now
            await self.send({"type": "state", "state": self.analyzer.snapshot()})

    # ------------------------------------------------------------------ main loop

    async def run(self) -> None:
        start = self.events[0]
        self.analyzer.process(start)  # match_start goes out as `hello`, not as an event
        self.idx = 1
        await self.send({"type": "hello", "match": start["details"], "match_id": start["match_id"],
                         "duration_ms": self.events[-1]["ts_ms"],
                         "duration_minutes": int(self.events[-1]["clock"].split(":")[0]) + 1})
        await self.send_status()
        while True:
            if self.idx >= len(self.events):
                if self.playing:
                    self.playing = False
                    await self.send({"type": "end"})
                    await self.send_status()
                await self._wait(None)
                continue
            if not self.playing:
                await self._wait(None)
                continue
            ev = self.events[self.idx]
            ahead_ms = ev["ts_ms"] - self.match_ts()
            if ahead_ms > 0:
                await self._wait(ahead_ms / 1000 / self.speed)
                continue
            self.idx += 1
            await self._emit(ev)

    async def _wait(self, timeout: float | None) -> None:
        """Sleep until `timeout` passes or a command changes something."""
        try:
            await asyncio.wait_for(self.changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        self.changed.clear()

    # ------------------------------------------------------------------ AI crew

    def _maybe_commentate(self, moment: dict) -> None:
        if not self.broadcast:
            return
        if wants_commentary(self.profile, moment):
            self._spawn(self._commentate(moment, self.analyzer.snapshot(), self._players(), self.profile))
        elif wants_recap(moment):
            snapshot = self.analyzer.snapshot(max_moments=1000)
            self._spawn(self._recap(moment, snapshot, self._players(), self.profile))

    def _players(self) -> dict[str, dict]:
        return {pid: self.analyzer.player(pid) for pid in self.analyzer.players}

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.ai_tasks.add(task)
        task.add_done_callback(self.ai_tasks.discard)

    def cancel_ai(self) -> None:
        for task in list(self.ai_tasks):
            task.cancel()

    async def _commentate(self, moment: dict, snapshot: dict, players: dict, profile: ViewerProfile) -> None:
        try:
            coverage = await self.broadcast.cover(self.match_id, moment, snapshot, players, profile)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("commentary failed for %s", moment["id"])
            return
        await self.send({"type": "commentary", "moment": {k: moment[k] for k in ("id", "type", "clock", "title", "importance")},
                         "coverage": coverage})
        now = {"ts_ms": int(self.match_ts()), "clock": self.events[max(self.idx - 1, 0)].get("clock", moment["clock"])}
        cue = self.director.lower_third(now, moment, coverage["caption"], coverage["line"], coverage["source"],
                                        coverage["language"], coverage["profile_key"])
        await self.send({"type": "cue", "cue": cue})

    async def _recap(self, moment: dict, snapshot: dict, players: dict, profile: ViewerProfile) -> None:
        try:
            recap = await self.broadcast.recap(self.match_id, moment, snapshot, snapshot["moments"], players, profile)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("recap failed for %s", moment["id"])
            return
        await self.send({"type": "recap", "moment_id": moment["id"], **recap})
