"""Touchline web server.

    uvicorn server.app:app --reload        # or: touchline serve

REST:
    GET  /api/health
    GET  /api/clubs                        fictional clubs (for pickers in the UI)
    GET  /api/scenarios
    GET  /api/matches                      matches available to watch
    POST /api/matches                      simulate a new match {home, away, seed?, scenario?}
    GET  /api/matches/{id}/snapshot        final stats of a match
WebSocket:
    /ws/matches/{id}?speed=10              live playback; protocol in docs/STREAMING.md
"""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from analytics.analyzer import MatchAnalyzer
from simulator.clubs import CLUBS
from simulator.engine import simulate
from simulator.scenarios import SCENARIOS

from .playback import Playback

STATIC = Path(__file__).parent / "static"

# Matches held in memory: match_id -> events. Small enough (~1 MB each) for a demo.
MATCHES: dict[str, list[dict]] = {}


class NewMatch(BaseModel):
    home: str = "kestrel"
    away: str = "marlowe"
    seed: int | None = None
    scenario: str = "standard"


def _summary(match_id: str) -> dict:
    events = MATCHES[match_id]
    d = events[0]["details"]
    return {"match_id": match_id, "home": d["home"]["name"], "away": d["away"]["name"],
            "scenario": d["scenario"], "seed": d["seed"], "final_score": events[-1]["details"]["score"]}


def _create(req: NewMatch) -> str:
    if req.home not in CLUBS or req.away not in CLUBS or req.home == req.away:
        raise HTTPException(400, f"pick two different clubs from: {', '.join(CLUBS)}")
    if req.scenario not in SCENARIOS:
        raise HTTPException(400, f"scenario must be one of: {', '.join(SCENARIOS)}")
    seed = req.seed if req.seed is not None else random.randint(1, 99_999)
    events = simulate(CLUBS[req.home], CLUBS[req.away], seed, req.scenario)
    match_id = f"{events[0]['match_id']}-{req.scenario}"
    MATCHES[match_id] = events
    return match_id


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """A few ready-made matches so the app has something to show immediately."""
    for home, away, seed, scenario in [("kestrel", "marlowe", 21, "standard"),
                                       ("marlowe", "portellis", 21, "late_comeback"),
                                       ("portellis", "ashvale", 8, "red_card")]:
        _create(NewMatch(home=home, away=away, seed=seed, scenario=scenario))
    yield


app = FastAPI(title="Touchline", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "matches": len(MATCHES)}


@app.get("/api/clubs")
def clubs() -> list[dict]:
    return [c.to_dict() for c in CLUBS.values()]


@app.get("/api/scenarios")
def scenarios() -> list[dict]:
    return [{"id": k, "description": (f.__doc__ or "").strip().split("\n")[0]} for k, f in SCENARIOS.items()]


@app.get("/api/matches")
def list_matches() -> list[dict]:
    return [_summary(m) for m in MATCHES]


@app.post("/api/matches", status_code=201)
def create_match(req: NewMatch) -> dict:
    return _summary(_create(req))


@app.get("/api/matches/{match_id}/snapshot")
def snapshot(match_id: str) -> dict:
    if match_id not in MATCHES:
        raise HTTPException(404, "no such match")
    a = MatchAnalyzer()
    for ev in MATCHES[match_id]:
        a.process(ev)
    return a.snapshot(max_moments=1000)


@app.websocket("/ws/matches/{match_id}")
async def watch(ws: WebSocket, match_id: str, speed: float = 10.0, autoplay: bool = True) -> None:
    await ws.accept()
    if match_id not in MATCHES:
        await ws.send_json({"type": "error", "message": "no such match"})
        await ws.close(code=4404)
        return

    async def send(msg: dict) -> None:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))

    playback = Playback(MATCHES[match_id], send, speed=speed, autoplay=autoplay)
    runner = asyncio.create_task(playback.run())
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                await send({"type": "error", "message": "commands must be JSON"})
                continue
            await playback.command(msg)
    except WebSocketDisconnect:
        pass
    finally:
        runner.cancel()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
