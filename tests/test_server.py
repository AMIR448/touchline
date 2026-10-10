import pytest
from fastapi.testclient import TestClient

from server.app import app


@pytest.fixture(scope="module")
def client():
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
    seen, moments, states = [], 0, 0
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
            elif msg["type"] == "end":
                break
    assert [e["seq"] for e in seen] == list(range(2, len(seen) + 2))  # seq 1 (match_start) is sent as hello
    assert seen[-1]["type"] == "match_end"
    assert moments > 10 and states > 0
    passes = [e for e in seen if e["type"] == "pass"]
    assert all(0 <= e["display"]["start"][0] <= 105 for e in passes)


def test_pause_and_seek(client):
    match_id = client.get("/api/matches").json()[0]["match_id"]
    with client.websocket_connect(f"/ws/matches/{match_id}?speed=1&autoplay=false") as ws:
        assert ws.receive_json()["type"] == "hello"
        status = ws.receive_json()
        assert status["type"] == "status" and status["playing"] is False

        ws.send_json({"cmd": "seek", "minute": 60})
        reset = ws.receive_json()
        assert reset["type"] == "reset"
        assert reset["state"]["match"]["clock"].startswith("59:")  # state rebuilt up to the hour mark
        status = ws.receive_json()
        assert status["playing"] is False

        ws.send_json({"cmd": "seek", "minute": 10})
        assert ws.receive_json()["state"]["match"]["clock"].startswith(("09:", "10:"))
        ws.receive_json()

        ws.send_json({"cmd": "speed", "value": 30})
        assert ws.receive_json()["speed"] == 30

        ws.send_json({"cmd": "nonsense"})
        assert ws.receive_json()["type"] == "error"


def test_unknown_match(client):
    with client.websocket_connect("/ws/matches/does-not-exist") as ws:
        assert ws.receive_json()["type"] == "error"
