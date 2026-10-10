"""Runs the crew for the server: caching, de-duplication and a concurrency limit.

Several viewers with the same profile watching the same match share one AI result, and a
replay costs nothing. Results are cached in memory and, optionally, in a JSON file so a
restart (or a judge's visit) doesn't re-generate them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .agents import AgentPool
from .crew import BroadcastCrew
from .models import ViewerProfile

log = logging.getLogger("touchline.broadcast")


class BroadcastService:
    def __init__(self, agents: AgentPool, max_concurrent: int = 6, cache_file: str | Path | None = None):
        self.crew = BroadcastCrew(agents)
        self.limit = asyncio.Semaphore(max_concurrent)
        self.cache: dict[str, Any] = {}
        self.inflight: dict[str, asyncio.Future] = {}
        self.cache_file = Path(cache_file) if cache_file else None
        if self.cache_file and self.cache_file.exists():
            self.cache = json.loads(self.cache_file.read_text())

    async def _once(self, key: str, make) -> Any:
        if key in self.cache:
            return self.cache[key]
        if key in self.inflight:
            return await asyncio.shield(self.inflight[key])
        future = asyncio.get_running_loop().create_future()
        self.inflight[key] = future
        try:
            async with self.limit:
                result = await make()
            self.cache[key] = result
            self._persist()
            future.set_result(result)
            return result
        except BaseException as e:
            future.set_exception(e)
            future.exception()  # mark retrieved so an unawaited future doesn't log noise
            raise
        finally:
            self.inflight.pop(key, None)

    def _persist(self) -> None:
        if self.cache_file:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, ensure_ascii=False))
            tmp.replace(self.cache_file)

    async def cover(self, match_id: str, moment: dict, snapshot: dict, players: dict[str, dict],
                    profile: ViewerProfile) -> dict:
        async def make():
            results = await self.crew.cover(moment, snapshot, players, [profile])
            return results[0].to_dict()
        return await self._once(f"cover|{match_id}|{moment['id']}|{profile.key()}", make)

    async def recap(self, match_id: str, moment: dict, snapshot: dict, moments: list[dict],
                    players: dict[str, dict], profile: ViewerProfile) -> dict:
        async def make():
            return await self.crew.recap(snapshot, moments, players, profile)
        return await self._once(f"recap|{match_id}|{moment['id']}|{profile.key()}", make)
