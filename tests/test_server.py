import pytest
from fastapi.testclient import TestClient

from broadcast.agents import FakeAgents
from broadcast.service import BroadcastService
from server.app import app


class FakeVoice:
    def __init__(self):
        self.spoken = []

    async def speak(self, text, language, excited=False):
        self.spoken.append((text, language, excited))
        return b"ID3-fake-mp3"


def recv(ws, kind):
    """Next message of this kind; skips others (e.g. commentary re-sent after a jump)."""
    while (msg := ws.receive_json())["type"] != kind:
        pass
    return msg


@pytest.fixture(scope="module")
def client():
    app.state.broadcast = BroadcastService(FakeAgents())  # never call real models from tests
    app.state.voice = FakeVoice()
    with TestClient(app) as c:  # `with` runs startup, which seeds demo matches
        yield c


def test_rest_endpoints(client):
    assert client.get("/api/health").json()["ok"]
    assert {c["id"] for c in client.get("/api/clubs").json()} == {"kestrel", "marlowe", "portellis", "ashvale"}
    assert "late_comeback" in {s["id"] for s in client.get("/api/scenarios").json()}
    assert len(client.get("/api/matches").json()) >= 3


def test_create_match_and_snapshot(client):
    r = client.post("/api/matches", json={"home": "ashvale", "away": "kestrel", "seed": 99, "scenario": "high_press"})
    assert r.status_code == 201
    match_id = r.json()["match_id"]
    snap = client.get(f"/api/matches/{match_id}/snapshot").json()
    assert snap["match"]["status"] == "full_time"
    assert snap["match"]["score"] == r.json()["final_score"]


def test_create_match_validates_input(client):
    assert client.post("/api/matches", json={"home": "kestrel", "away": "kestrel"}).status_code == 400
    assert client.post("/api/matches", json={"home": "nope", "away": "kestrel"}).status_code == 400
    assert client.post("/api/matches", json={"scenario": "nope"}).status_code == 400


def test_stream_full_match(client):
    """Stream a whole match at max speed: every event arrives once, in order, with display coords."""
    match_id = client.get("/api/matches").json()[0]["match_id"]
    seen, moments, states, commentary, recaps, ended = [], 0, 0, 0, 0, False
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=600") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["match_id"]
        while True:
            msg = ws.receive_json()
            if msg["type"] == "event":
                seen.append(msg["event"])
            elif msg["type"] == "moment":
                moments += 1
            elif msg["type"] == "state":
                states += 1
            elif msg["type"] == "commentary":
                commentary += 1
                assert msg["coverage"]["line"] and msg["coverage"]["trace"]
            elif msg["type"] == "recap":
                recaps += 1
            elif msg["type"] == "end":
                ended = True
            if ended and recaps == 2:  # the full-time recap may land just after the final whistle
                break
    assert [e["seq"] for e in seen] == list(range(2, len(seen) + 2))  # seq 1 (match_start) is sent as hello
    assert seen[-1]["type"] == "match_end"
    assert moments > 10 and states > 0
    assert commentary > 0, "the AI crew should have covered some moments"
    assert recaps == 2, "half-time and full-time recaps"
    passes = [e for e in seen if e["type"] == "pass"]
    assert all(0 <= e["display"]["start"][0] <= 105 for e in passes)


def test_pause_and_seek(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=1&autoplay=false") as ws:
        assert ws.receive_json()["type"] == "hello"
        status = ws.receive_json()
        assert status["type"] == "status" and status["playing"] is False

        ws.send_json({"cmd": "seek", "minute": 60})
        reset = recv(ws, "reset")
        assert reset["state"]["match"]["clock"].startswith("59:")  # state rebuilt up to the hour mark
        assert all(len(team) == 11 for team in reset["lineups"].values())  # for drawing players after a jump
        assert recv(ws, "status")["playing"] is False

        ws.send_json({"cmd": "seek", "minute": 10})
        assert recv(ws, "reset")["state"]["match"]["clock"].startswith(("09:", "10:"))
        recv(ws, "status")

        ws.send_json({"cmd": "speed", "value": 30})
        assert recv(ws, "status")["speed"] == 30

        ws.send_json({"cmd": "nonsense"})
        assert recv(ws, "error")["type"] == "error"


def test_unknown_match(client):
    with client.websocket_connect("/ws/matches/does-not-exist") as ws:
        assert ws.receive_json()["type"] == "error"


def test_profile_command(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    with client.websocket_connect(f"/ws/matches/{match_id}?autoplay=false") as ws:
        ws.receive_json(), ws.receive_json()
        ws.send_json({"cmd": "profile", "persona": "analyst", "language": "es", "favorite_team": "kestrel"})
        msg = ws.receive_json()
        assert msg["type"] == "profile" and msg["profile"]["language"] == "es"
        ws.receive_json()  # status
        ws.send_json({"cmd": "profile", "persona": "robot"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"cmd": "profile", "language": "he"})  # not a supported language
        assert ws.receive_json()["type"] == "error"


def test_app_pages(client):
    r = client.get("/")
    assert r.status_code == 200 and "app.js" in r.text and r.headers["cache-control"] == "no-cache"
    assert client.get("/dev").status_code == 200
    assert client.get("/static/app/app.js").status_code == 200
    assert client.get("/static/vendor/preact.module.js").status_code == 200


def test_cues_endpoint(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    cues = client.get(f"/api/matches/{match_id}/cues").json()
    types = {c["type"] for c in cues}
    assert {"player_tag", "stat_badge", "score_bug", "moment_banner", "lower_third"} <= types
    assert [c["ts_ms"] for c in cues] == sorted(c["ts_ms"] for c in cues)
    assert all({"cue_id", "type", "ts_ms", "clock", "duration_ms", "priority", "payload"} <= set(c) for c in cues)


def test_compare_endpoint(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    goal = next(m for m in client.get(f"/api/matches/{match_id}/snapshot").json()["moments"] if m["type"] == "goal")
    r = client.post(f"/api/matches/{match_id}/compare", json={"moment_id": goal["id"]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["profiles"]) == len(body["coverage"]) == 3
    teams = {p["favorite_team"] for p in body["profiles"]}
    assert len(teams - {None}) == 2, "default compare shows a fan of each side"
    assert client.post(f"/api/matches/{match_id}/compare", json={"moment_id": "nope"}).status_code == 404


def test_stream_carries_cues(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    kinds = set()
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=600") as ws:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "cue":
                kinds.add(msg["cue"]["type"])
            if msg["type"] == "end":
                break
    assert {"player_tag", "stat_badge", "score_bug", "moment_banner", "lower_third"} <= kinds


def test_ask_the_match(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=1&autoplay=false") as ws:
        ws.receive_json(), ws.receive_json()  # hello, status
        ws.send_json({"cmd": "seek", "minute": 30})
        ws.receive_json(), ws.receive_json()  # reset, status
        ws.send_json({"cmd": "ask", "id": "q1", "question": "What's the score?"})
        msg = recv(ws, "answer")
        assert msg["id"] == "q1" and msg["source"] == "ai"
        assert msg["clock"] < "31"  # answered as of the viewer's minute


def test_mcp_endpoint_lists_and_answers(client):
    """Speak MCP's JSON-RPC over HTTP, like an MCP client would."""
    headers = {"Accept": "application/json, text/event-stream"}
    def rpc(method, params, id=1):
        r = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": id, "method": method, "params": params})
        assert r.status_code == 200, r.text
        return r.json()["result"]
    rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}})
    names = {t["name"] for t in rpc("tools/list", {}, 2)["tools"]}
    assert {"list_matches", "match_situation", "player_stats", "top_players", "key_moments"} <= names
    match_id = client.get("/api/matches").json()[0]["match_id"]
    res = rpc("tools/call", {"name": "match_situation", "arguments": {"match_id": match_id, "minute": 30}}, 3)
    assert not res.get("isError") and "score" in res["content"][0]["text"]


def test_voice_speaks_only_lines_the_crew_wrote(client):
    key = "cover|m1|goal-1|casual|es|-|-"
    client.app.state.broadcast.cache[key] = {"line": "¡Gol!", "language": "es", "insight": {"angle": "drama"}}
    r = client.get("/api/matches/m1/voice/goal-1", params={"profile": "casual|es|-|-"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert client.app.state.voice.spoken[-1] == ("¡Gol!", "es", True)
    assert client.get("/api/matches/m1/voice/nope").status_code == 404  # no arbitrary text, ever


def test_warm_up_fills_the_cache_playback_reads():
    import asyncio
    from server.warm import warm_up
    from simulator.clubs import CLUBS
    from simulator.engine import simulate
    events = simulate(CLUBS["kestrel"], CLUBS["marlowe"], 21)
    service, voice = BroadcastService(FakeAgents()), FakeVoice()
    done = asyncio.run(warm_up({"demo": events}, ["demo"], service, voice))
    feed = events[0]["match_id"]
    covers = [k for k in service.cache if k.startswith(f"cover|{feed}|") and k.endswith("analyst|en|-|-")]
    recaps = [k for k in service.cache if k.startswith(f"recap|{feed}|")]
    assert done > 10 and covers and len(recaps) == 48  # HT + FT, for all 24 viewer combinations
    assert any(k.endswith("|casual|fr|kestrel|-") for k in service.cache)  # every language and club
    assert voice.spoken  # audio pre-generated too


def test_switching_profile_resends_what_is_already_written(client):
    from broadcast.models import ViewerProfile
    from broadcast.relevance import wants_commentary
    from server.app import MATCHES
    match_id = client.get("/api/matches").json()[0]["match_id"]
    feed = MATCHES[match_id][0]["match_id"]
    spanish = ViewerProfile(persona="casual", language="es")
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=1&autoplay=false") as ws:
        ws.receive_json(), ws.receive_json()  # hello, status
        ws.send_json({"cmd": "seek", "minute": 30})
        moments = recv(ws, "reset")["state"]["moments"]
        recv(ws, "status")
        m = next(x for x in moments if wants_commentary(spanish, x))
        client.app.state.broadcast.cache[f"cover|{feed}|{m['id']}|{spanish.key()}"] = {
            "moment_id": m["id"], "profile_key": spanish.key(), "caption": "¡Golazo!", "line": "¡Qué jugada!",
            "language": "es", "source": "ai", "insight": None, "trace": []}
        ws.send_json({"cmd": "profile", "persona": "casual", "language": "es"})
        recv(ws, "profile")
        while (msg := recv(ws, "commentary"))["moment"]["id"] != m["id"]:
            pass
        assert msg["coverage"]["line"] == "¡Qué jugada!"


def test_seek_keeps_written_commentary_and_player_cards(client):
    from broadcast.models import ViewerProfile
    from broadcast.relevance import wants_commentary
    from server.app import MATCHES
    match_id = client.get("/api/matches").json()[0]["match_id"]
    feed = MATCHES[match_id][0]["match_id"]
    viewer = ViewerProfile()
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=1&autoplay=false&persona=casual") as ws:
        ws.receive_json(), ws.receive_json()  # hello, status
        ws.send_json({"cmd": "seek", "minute": 30})
        reset = recv(ws, "reset")
        m = next(x for x in reset["state"]["moments"] if wants_commentary(viewer, x))
        client.app.state.broadcast.cache[f"cover|{feed}|{m['id']}|{viewer.key()}"] = {
            "moment_id": m["id"], "profile_key": viewer.key(), "caption": "Goal", "line": "What a goal.",
            "language": "en", "source": "ai", "insight": None, "trace": []}
        ws.send_json({"cmd": "seek", "minute": 31})
        recv(ws, "reset")
        while (msg := recv(ws, "commentary"))["moment"]["id"] != m["id"]:
            pass
        pid = reset["lineups"][next(iter(reset["lineups"]))][9]
        ws.send_json({"cmd": "player", "id": pid})
        msg = recv(ws, "player")
        assert msg["player"]["id"] == pid and "goals" in msg["player"]


def test_factcheck_endpoint_blocks_a_wrong_score(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    snap = client.get(f"/api/matches/{match_id}/snapshot").json()
    goal = next(m for m in snap["moments"] if m["type"] == "goal")
    score = next(t for t in goal["facts"]["score_after"].split() if "-" in t)  # "Kestrel City 0-1 Marlowe United"
    ok = client.post(f"/api/matches/{match_id}/factcheck", json={"moment_id": goal["id"], "text": f"It's {score} now."}).json()
    assert ok["ok"] and ok["marks"][0]["kind"] == "score" and ok["marks"][0]["ok"]
    bad = client.post(f"/api/matches/{match_id}/factcheck", json={"moment_id": goal["id"], "text": "It's 7-3 now."}).json()
    assert not bad["ok"] and "7-3" in bad["problems"][0] and not bad["marks"][0]["ok"]
