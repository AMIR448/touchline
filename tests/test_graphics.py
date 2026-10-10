from analytics.graphics import GraphicsDirector, all_cues
from simulator.clubs import CLUBS
from simulator.engine import simulate


def test_cues_for_a_match():
    events = simulate(CLUBS["kestrel"], CLUBS["marlowe"], 21)
    cues = all_cues(events)
    goals = sum(e["type"] == "goal" for e in events)
    banners = [c for c in cues if c["type"] == "moment_banner" and c["payload"]["label"] == "GOAL"]
    assert len(banners) == goals
    final_bug = [c for c in cues if c["type"] == "score_bug"][-1]["payload"]
    assert {final_bug["home"]["id"]: final_bug["home"]["goals"], final_bug["away"]["id"]: final_bug["away"]["goals"]} \
        == events[-1]["details"]["score"]
    badges = [c for c in cues if c["type"] == "stat_badge"]
    assert 10 <= len(badges) <= 80, "badges should be occasional, not constant"


def test_badge_thresholds():
    start = simulate(CLUBS["kestrel"], CLUBS["marlowe"], 1)[0]
    d = GraphicsDirector(start)
    base = {"ts_ms": 1, "clock": "10:00", "team_id": "kestrel", "player_id": "kestrel-10", "x": 85, "y": 34}
    fast = d.on_event({**base, "type": "shot", "outcome": "saved", "details": {"shot_speed_kmh": 112}})
    assert any(c["type"] == "stat_badge" and c["payload"]["label"] == "Shot speed" for c in fast)
    slow = d.on_event({**base, "type": "shot", "x": 70, "outcome": "off_target", "details": {"shot_speed_kmh": 60}})
    assert not any(c["type"] == "stat_badge" for c in slow)
    tag = [c for c in fast if c["type"] == "player_tag"][0]["payload"]
    assert tag["name"] and tag["number"] and tag["color"].startswith("#")
