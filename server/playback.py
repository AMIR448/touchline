"""Live playback of one match for one viewer.

A virtual match clock runs at `speed` x real time. Whenever the clock passes an
event's `ts_ms`, the event goes through the stats engine and out to the viewer,
followed by any key moments it triggered and (at most once a second) the full
match state. The viewer can pause, change speed, jump to a minute or restart.

Message protocol: see docs/STREAMING.md.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from analytics.analyzer import MatchAnalyzer
from touchline.pitch import to_display

Send = Callable[[dict[str, Any]], Awaitable[None]]

STATE_INTERVAL_S = 1.0
MAX_SPEED = 600.0


class Playback:
    def __init__(self, events: list[dict], send: Send, speed: float = 10.0, autoplay: bool = True):
        self.events = events
        self.send = send
        self.home_id = events[0]["details"]["home"]["id"]
        self.speed = speed
        self.playing = autoplay
        self.analyzer = MatchAnalyzer()
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
        self.analyzer = MatchAnalyzer()
        self.idx = 0
        while self.idx < len(self.events) and (self.idx == 0 or self.events[self.idx]["ts_ms"] <= target_ms):
            self.analyzer.process(self.events[self.idx])
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
        for m in moments:
            await self.send({"type": "moment", "moment": m.to_dict()})
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
