import statistics

import pytest

from analytics.analyzer import MatchAnalyzer
from simulator.clubs import CLUBS
from simulator.engine import simulate
from touchline.pitch import PITCH_LENGTH, PITCH_WIDTH
from simulator.scenarios import SCENARIOS
from simulator.schema import EVENT_TYPES


@pytest.fixture(scope="module")
def match():
    return simulate(CLUBS["kestrel"], CLUBS["marlowe"], seed=11)


def test_same_seed_same_match():
    a = simulate(CLUBS["portellis"], CLUBS["ashvale"], seed=5)
    b = simulate(CLUBS["portellis"], CLUBS["ashvale"], seed=5)
    assert a == b


def test_event_stream_is_well_formed(match):
    assert match[0]["type"] == "match_start"
    assert match[-1]["type"] == "match_end"
    assert [e["seq"] for e in match] == list(range(1, len(match) + 1))
    assert len({e["event_id"] for e in match}) == len(match)
    assert all(e["type"] in EVENT_TYPES for e in match)
    ts = [e["ts_ms"] for e in match]
    assert ts == sorted(ts), "events must be in time order for live replay"
    assert 90 * 60_000 <= ts[-1] <= 102 * 60_000


def test_coordinates_on_pitch(match):
    for e in match:
        for k, limit in (("x", PITCH_LENGTH), ("end_x", PITCH_LENGTH), ("y", PITCH_WIDTH), ("end_y", PITCH_WIDTH)):
            if k in e:
                assert 0 <= e[k] <= limit, e


def test_players_belong_to_their_team(match):
    meta = match[0]["details"]
    squads = {side["id"]: {p["id"] for p in side["starters"] + side["bench"]} for side in (meta["home"], meta["away"])}
    for e in match:
        if "player_id" in e:
            assert e["player_id"] in squads[e["team_id"]], e


def test_final_score_matches_goal_events(match):
    final = match[-1]["details"]["score"]
    goals = {team: 0 for team in final}
    for e in match:
        if e["type"] == "goal":
            goals[e["team_id"]] += 1
    assert goals == final


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_runs(scenario):
    events = simulate(CLUBS["kestrel"], CLUBS["ashvale"], seed=2, scenario=scenario)
    assert events[-1]["type"] == "match_end"


def test_red_card_scenario_sends_someone_off():
    events = simulate(CLUBS["kestrel"], CLUBS["marlowe"], seed=4, scenario="red_card")
    assert any(e["type"] == "card" and e["details"]["card"] == "red" and e["team_id"] == "marlowe" for e in events)


def test_stats_look_like_real_football():
    """Averages over many matches should sit near Premier League per-team norms."""
    rows = []
    ids = list(CLUBS)
    for seed in range(40):
        events = simulate(CLUBS[ids[seed % 4]], CLUBS[ids[(seed + 1 + seed // 4) % 4 or 1]], seed)
        a = MatchAnalyzer()
        for ev in events:
            a.process(ev)
        rows.extend(a.snapshot()["teams"].values())

    def avg(key):
        return statistics.mean(r[key] for r in rows)

    assert 1.0 <= avg("goals") <= 1.9
    assert 6 <= statistics.mean(r["ppda"] for r in rows) <= 16
    assert 9 <= avg("shots") <= 15
    assert 1.0 <= avg("xg") <= 2.0
    assert 77 <= avg("pass_accuracy_pct") <= 86
    assert 380 <= avg("passes") <= 600
    assert 3.5 <= avg("corners") <= 7.5
    assert 8 <= avg("fouls") <= 14
    assert 12 <= avg("tackles") <= 24
