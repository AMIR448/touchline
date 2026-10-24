"""Touchline web server.

    uvicorn server.app:app --reload        # or: touchline serve

REST:
    GET  /api/health
    GET  /api/clubs                        fictional clubs (for pickers in the UI)
    GET  /api/scenarios
    GET  /api/matches                      matches available to watch
    POST /api/matches                      simulate a new match {home, away, seed?, scenario?}
    GET  /api/matches/{id}/snapshot        final stats of a match
    GET  /api/matches/{id}/cues            timed overlay cues for a graphics renderer
    POST /api/matches/{id}/compare         one moment, tailored for several viewers {moment_id, profiles}
WebSocket:
    /ws/matches/{id}?speed=10              live playback; protocol in docs/STREAMING.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from analytics.analyzer import MatchAnalyzer
from analytics.graphics import all_cues
from broadcast.limits import DailyBudget, RateLimiter
from broadcast.models import ViewerProfile
from broadcast.service import BroadcastService
from simulator.clubs import CLUBS
from simulator.engine import simulate
from simulator.scenarios import SCENARIOS

from . import mcp_server, telemetry
from .playback import Playback

STATIC = Path(__file__).parent / "static"

# Matches held in memory: match_id -> events. Small enough (~1 MB each) for a demo.
MATCHES: dict[str, list[dict]] = {}
log = logging.getLogger("touchline")


def _writable(folder: Path) -> Path:
    """`folder` if we can write there (e.g. App Service's persistent /home), else the local data/ folder."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return folder
    except OSError:
        log.warning("can't write to %s; using data/ (not persistent)", folder)
        fallback = Path("data")
        fallback.mkdir(exist_ok=True)
        return fallback


def _broadcast_service() -> BroadcastService | None:
    """The AI crew, if configured. Without a Foundry endpoint (or with TOUCHLINE_AI=off) the app
    still works: viewers just get the stats engine's moment titles instead of AI commentary."""
    if os.getenv("TOUCHLINE_AI", "on") == "off" or not os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
        log.warning("AI commentary disabled (set FOUNDRY_PROJECT_ENDPOINT to enable)")
        return None
    from broadcast.agents import FoundryAgents
    cache_file = _writable(Path(os.getenv("TOUCHLINE_CACHE_FILE", "data/ai-cache.json")).parent) / "ai-cache.json"
    return BroadcastService(FoundryAgents(), cache_file=cache_file,
                            budget=DailyBudget(int(os.getenv("TOUCHLINE_DAILY_AI_RUNS", "1500"))))


ASK_LIMIT = RateLimiter(limit=int(os.getenv("TOUCHLINE_ASK_PER_10MIN", "10")), window_s=600)


def _client_ip(ws: WebSocket) -> str:
    """The visitor's address. Behind Azure's front end it's the first X-Forwarded-For entry (may carry a port)."""
    forwarded = ws.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded.count(":") == 1:  # "1.2.3.4:5678"; IPv6 addresses have more colons
        forwarded = forwarded.split(":")[0]
    return forwarded or (ws.client.host if ws.client else "unknown")


def _voice_service(broadcast: BroadcastService | None):
    """Spoken commentary (Azure AI Speech), only when the AI crew is on: it speaks the crew's lines."""
    from broadcast.voice import VoiceService, speech_endpoint
    endpoint = speech_endpoint()
    if broadcast is None or not endpoint or os.getenv("TOUCHLINE_VOICE", "on") == "off":
        return None
    voice_dir = _writable(Path(os.getenv("TOUCHLINE_VOICE_DIR", "data/voice")))
    return VoiceService(endpoint, broadcast.crew.agents.credential, cache_dir=voice_dir)


class CompareRequest(BaseModel):
    moment_id: str
    profiles: list[ViewerProfile] | None = None


class NewMatch(BaseModel):
    home: str = "kestrel"
    away: str = "marlowe"
    seed: int | None = None
    scenario: str = "standard"


def _summary(match_id: str) -> dict:
    events = MATCHES[match_id]
    d = events[0]["details"]
    return {"match_id": match_id, "home": d["home"]["name"], "away": d["away"]["name"],
            "home_id": d["home"]["id"], "away_id": d["away"]["id"],
            "scenario": d["scenario"], "seed": d["seed"], "final_score": events[-1]["details"]["score"]}


INGEST = None  # EventHubIngest when EVENTHUB_NAMESPACE is set; None = simulate in-process


def _ingested(match_key: str, events: list[dict]) -> None:
    MATCHES[match_key] = events


async def _create(req: NewMatch) -> str:
    if req.home not in CLUBS or req.away not in CLUBS or req.home == req.away:
        raise HTTPException(400, f"pick two different clubs from: {', '.join(CLUBS)}")
    if req.scenario not in SCENARIOS:
        raise HTTPException(400, f"scenario must be one of: {', '.join(SCENARIOS)}")
    seed = req.seed if req.seed is not None else random.randint(1, 99_999)
    events = simulate(CLUBS[req.home], CLUBS[req.away], seed, req.scenario)
    match_id = f"{events[0]['match_id']}-{req.scenario}"
    if INGEST is None:
        MATCHES[match_id] = events
    else:  # through the stream: the app knows the match once it has read it back from Event Hubs
        await INGEST.publish(match_id, events)
    return match_id


async def _start_ingest() -> None:
    """Use Event Hubs as the ingest stage if configured; if it can't start, keep serving locally."""
    global INGEST
    namespace, hub = os.getenv("EVENTHUB_NAMESPACE"), os.getenv("EVENTHUB_NAME", "match-events")
    if not namespace:
        return
    from broadcast.agents import _default_credential
    from ingest.hub import EventHubIngest
    try:
        ingest = EventHubIngest(namespace, hub, _default_credential(), _ingested)
        await ingest.start()
        INGEST = ingest
        log.info("ingest: Event Hubs %s/%s", namespace, hub)
    except Exception:
        log.exception("Event Hubs ingest failed to start; simulating in-process")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the ingest stage, then a few ready-made matches so the app has something to show immediately."""
    global INGEST
    await _start_ingest()
    demo = []
    for home, away, seed, scenario in [("kestrel", "marlowe", 21, "standard"),
                                       ("marlowe", "portellis", 21, "late_comeback"),
                                       ("portellis", "ashvale", 8, "red_card")]:
        req = NewMatch(home=home, away=away, seed=seed, scenario=scenario)
        try:
            demo.append(await _create(req))
        except Exception:
            log.exception("demo match via Event Hubs failed; falling back to in-process")
            INGEST = None
            demo.append(await _create(req))
    if not hasattr(_app.state, "broadcast"):  # tests may inject their own
        Path("data").mkdir(exist_ok=True)
        _app.state.broadcast = _broadcast_service()
    if not hasattr(_app.state, "voice"):
        _app.state.voice = _voice_service(_app.state.broadcast)
    warming = None
    if _app.state.broadcast and os.getenv("TOUCHLINE_WARM", "off") == "on":  # on in Azure: see server/warm.py
        from .warm import warm_up
        warming = asyncio.create_task(warm_up(MATCHES, demo, _app.state.broadcast, _app.state.voice))
    async with mcp.session_manager.run():  # the MCP endpoint's own lifecycle
        yield
    if warming:
        warming.cancel()
    if INGEST:
        await INGEST.close()


telemetry.setup()  # before the app exists, so its requests are traced too
app = FastAPI(title="Touchline", version="0.1.0", lifespan=lifespan)
mcp = mcp_server.build(MATCHES)
mcp_app = mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


@app.middleware("http")
async def revalidate_app_files(request, call_next):
    """The app has no build step, so make browsers revalidate its files (cheap 304s) after each deploy."""
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/dev") or path.startswith("/static/app/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "matches": len(MATCHES), "ai": app.state.broadcast is not None,
            "ingest": "event-hubs" if INGEST else "in-process",
            "voice": getattr(app.state, "voice", None) is not None,
            **(app.state.broadcast.budget.status() if app.state.broadcast and app.state.broadcast.budget else {})}


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
async def create_match(req: NewMatch) -> dict:
    return _summary(await _create(req))


@app.get("/api/matches/{match_id}/snapshot")
def snapshot(match_id: str) -> dict:
    if match_id not in MATCHES:
        raise HTTPException(404, "no such match")
    a = MatchAnalyzer()
    for ev in MATCHES[match_id]:
        a.process(ev)
    return a.snapshot(max_moments=1000)


@app.get("/api/matches/{match_id}/cues")
def cues(match_id: str) -> list[dict]:
    if match_id not in MATCHES:
        raise HTTPException(404, "no such match")
    return all_cues(MATCHES[match_id])


def _feed_id(match_id: str) -> str:
    """The id inside the event feed, which the AI cache is keyed by (playback uses it too)."""
    return MATCHES[match_id][0]["match_id"] if match_id in MATCHES else match_id


def _state_at_moment(events: list[dict], moment_id: str):
    """Replay the stats engine until `moment_id` fires; return (moment, snapshot, players) at that instant."""
    a = MatchAnalyzer()
    for ev in events:
        for m in a.process(ev):
            if m.id == moment_id:
                return m.to_dict(), a.snapshot(), {pid: a.player(pid) for pid in a.players}
    raise HTTPException(404, "no such moment")


@app.post("/api/matches/{match_id}/compare")
async def compare(match_id: str, req: CompareRequest) -> dict:
    """The same moment for several viewers side by side (default: an analyst and a fan of each side)."""
    if match_id not in MATCHES:
        raise HTTPException(404, "no such match")
    if app.state.broadcast is None:
        raise HTTPException(503, "AI commentary is not configured")
    moment, snap, players = _state_at_moment(MATCHES[match_id], req.moment_id)
    profiles = req.profiles
    if not profiles:
        teams = list(snap["teams"])
        mine = moment.get("team_id") or teams[0]
        other = next(t for t in teams if t != mine)
        profiles = [ViewerProfile(persona="analyst", language="en"),
                    ViewerProfile(persona="casual", language="es", favorite_team=mine),
                    ViewerProfile(persona="casual", language="fr", favorite_team=other)]
    coverages = await app.state.broadcast.cover_many(_feed_id(match_id), moment, snap, players, profiles[:4])
    return {"moment": moment, "profiles": [p.model_dump() for p in profiles[:4]], "coverage": coverages}


@app.get("/api/matches/{match_id}/voice/{moment_id}")
async def voice(match_id: str, moment_id: str, profile: str = "analyst|en|-|-") -> Response:
    """The commentator speaking one line the crew already wrote (and fact-checked) for this viewer."""
    if getattr(app.state, "voice", None) is None:
        raise HTTPException(503, "voice is not configured")
    coverage = app.state.broadcast.cache.get(f"cover|{_feed_id(match_id)}|{moment_id}|{profile}")
    if coverage is None:
        raise HTTPException(404, "no commentary for this moment and viewer yet")
    excited = (coverage.get("insight") or {}).get("angle") in ("drama", "turning_point")
    try:
        audio = await app.state.voice.speak(coverage["line"], coverage["language"], excited)
    except Exception:
        log.exception("speech synthesis failed")
        raise HTTPException(502, "speech synthesis failed")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.websocket("/ws/matches/{match_id}")
async def watch(ws: WebSocket, match_id: str, speed: float = 10.0, autoplay: bool = True,
                persona: str = "casual", language: str = "en", team: str | None = None,
                player: str | None = None) -> None:
    await ws.accept()
    if match_id not in MATCHES:
        await ws.send_json({"type": "error", "message": "no such match"})
        await ws.close(code=4404)
        return

    async def send(msg: dict) -> None:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))

    try:
        profile = ViewerProfile(persona=persona, language=language, favorite_team=team, favorite_player=player)
    except ValueError:
        profile = ViewerProfile()
    playback = Playback(MATCHES[match_id], send, speed=speed, autoplay=autoplay,
                        broadcast=app.state.broadcast, profile=profile, voice=getattr(app.state, "voice", None),
                        allow_question=lambda ip=_client_ip(ws): ASK_LIMIT.allow(ip))
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
        playback.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/dev")
def dev_viewer() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/mcp", mcp_app)  # the match tools over MCP: see server/mcp_server.py
