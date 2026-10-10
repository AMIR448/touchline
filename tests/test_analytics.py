import pytest

from analytics.analyzer import MatchAnalyzer
from analytics.metrics import is_progressive, pass_difficulty, shot_xg
from simulator.clubs import CLUBS
from simulator.engine import simulate


def run(home="kestrel", away="marlowe", seed=21, scenario="standard"):
    events = simulate(CLUBS[home], CLUBS[away], seed, scenario)
    a = MatchAnalyzer()
    moments = [m for ev in events for m in a.process(ev)]
    return events, a, moments


@pytest.fixture(scope="module")
def match():
    return run()


def test_score_matches_the_feed(match):
    events, a, _ = match
    assert a.snapshot()["match"]["score"] == events[-1]["details"]["score"]


def test_one_goal_moment_per_goal_event(match):
    events, _, moments = match
    assert sum(e["type"] == "goal" for e in events) == sum(m.type == "goal" for m in moments)


def test_evidence_points_at_real_events(match):
    """Agents cite evidence IDs, so every one must exist in the feed."""
    events, _, moments = match
    ids = {e["event_id"] for e in events}
    for m in moments:
        assert set(m.evidence) <= ids, m


def test_snapshot_is_consistent(match):
    _, a, _ = match
    snap = a.snapshot()
    teams = snap["teams"].values()
    assert sum(t["possession_pct"] for t in teams) in (99, 100, 101)
    assert all(0 <= t["pass_accuracy_pct"] <= 100 for t in teams)
    assert all(-1 <= v <= 1 for v in snap["momentum"]["timeline"])
    assert all(0 <= v <= 100 for v in snap["control_chaos"]["timeline"])
    n = len(snap["minute_labels"])
    assert n == len(snap["momentum"]["timeline"]) == len(snap["xg_timeline"]) >= 90
    assert snap["match"]["status"] == "full_time"


def test_player_totals_add_up_to_team_totals(match):
    _, a, _ = match
    for t in a.teams:
        players = [p for p in a.players.values() if p.team_id == t]
        assert sum(p.passes for p in players) == a.team[t].passes
        assert sum(p.goals for p in players) == a.team[t].goals
        assert abs(sum(p.xg for p in players) - a.team[t].xg) < 1e-9


def test_game_state_labels():
    """Every goal is labelled correctly relative to the score before it."""
    for seed in range(15):
        _, a, moments = run(seed=seed, scenario="late_comeback")
        score = {name: 0 for name in a.team_names.values()}
        for m in (m for m in moments if m.type == "goal"):
            team = m.facts["team"]
            other = next(n for n in score if n != team)
            before, them = score[team], score[other]
            score[team] += 1
            expected = {
                "opener": before + them == 0,
                "equaliser": before + 1 == them,
                "pulls_one_back": before + 1 < them,
                "takes_lead": before == them,
                "completes_comeback": before == them,
                "extends_lead": before > them,
            }
            assert expected[m.facts["game_state"]], (seed, m.facts)


def test_high_press_team_presses_more():
    """Marlowe (press 0.85) should allow fewer passes per defensive action than Ashvale (0.2)."""
    ppda = {"marlowe": [], "ashvale": []}
    for seed in range(10):
        _, a, _ = run("marlowe", "ashvale", seed)
        snap = a.snapshot()
        for t in ppda:
            ppda[t].append(snap["teams"][t]["ppda"])
    assert sum(ppda["marlowe"]) < sum(ppda["ashvale"])


def test_metrics_basics():
    tap_in = {"x": 102, "y": 34, "details": {"body_part": "right_foot"}}
    long_range = {"x": 75, "y": 34, "details": {"body_part": "right_foot"}}
    assert shot_xg(tap_in) > 0.3 > shot_xg(long_range)
    assert shot_xg({"x": 94, "y": 34, "details": {"subtype": "penalty"}}) == 0.76

    short = {"x": 40, "y": 30, "end_x": 45, "end_y": 32, "details": {"length_m": 5.4}}
    risky = {"x": 60, "y": 30, "end_x": 95, "end_y": 34, "under_pressure": True, "details": {"length_m": 35.2}}
    assert pass_difficulty(short) < 1.5 < 4 < pass_difficulty(risky)
    assert is_progressive(risky) and not is_progressive(short)
