import asyncio

import pytest

from analytics.analyzer import MatchAnalyzer
from broadcast.agents import FakeAgents
from broadcast.crew import BroadcastCrew
from broadcast.factcheck import allowed_numbers, check, check_names_kept
from broadcast.models import ViewerProfile
from broadcast.relevance import wants_commentary
from broadcast.service import BroadcastService
from simulator.clubs import CLUBS
from simulator.engine import simulate


@pytest.fixture(scope="module")
def goal():
    """The first goal of a known match, with the state at that moment."""
    a = MatchAnalyzer()
    for ev in simulate(CLUBS["kestrel"], CLUBS["marlowe"], 21):
        for m in a.process(ev):
            if m.type == "goal":
                return m.to_dict(), a.snapshot(), {p: a.player(p) for p in a.players}


def cover(agents, goal, profiles=None):
    moment, snap, players = goal
    return asyncio.run(BroadcastCrew(agents).cover(moment, snap, players, profiles or [ViewerProfile()]))


# ---------------------------------------------------------------- fact-checker

def test_factcheck_accepts_supported_numbers():
    ctx = {"xg": 3.89, "possession_pct": 63, "clock": "55:37", "score": "Kestrel 1-1 Marlowe"}
    assert check(["63% of the ball", "xG of 3.9", "nearly 4 expected goals", "a 1-1 draw", "56th minute"], [ctx]).ok


def test_factcheck_rejects_invented_numbers():
    result = check(["They had 72% possession"], [{"possession_pct": 63}])
    assert not result.ok and "72" in result.problems[0]


def test_factcheck_checks_scorelines_as_a_pair():
    # 3 and 1 both appear in the facts, but 3-1 was never the score
    ctx = {"score": "Kestrel 2-1 Marlowe", "shots": 3}
    result = check(["Kestrel lead 3-1"], [ctx])
    assert not result.ok and "3-1" in result.problems[0]
    assert check(["Kestrel lead 2-1", "Marlowe trail 1–2", "from 0-0 at the break"], [ctx]).ok
    assert check(["Kestrel's 4-3-3 against Marlowe's 4-2-3-1"], [{"shape": "4-3-3 v 4-2-3-1"}]).ok


def test_factcheck_rejects_invented_names():
    ctx = {"scorer": "Theo Marchetti", "team": "Port Ellis Rovers"}
    result = check(["Theo Makuke puts Port Ellis Rovers ahead."], [ctx], squad_names=["Theo Marchetti"])
    assert not result.ok and "Theo Makuke" in result.problems[0]
    assert check(["Despite the pressure, Theo Marchetti puts Port Ellis Rovers ahead."], [ctx]).ok
    assert check(["Aunque Port Ellis sufre, marca Theo Marchetti."], [ctx]).ok  # sentence-initial words aren't names
    # headlines capitalise ordinary words: only sentences get the invented-name check
    assert check(["Marchetti scores."], [ctx], headlines=["Marchetti Levels with Clinical Finish"]).ok


def test_factcheck_percentages_from_probabilities():
    assert 19 in allowed_numbers({"shot_xg": 0.19})
    assert check(["a 19% chance"], [{"shot_xg": 0.19}]).ok


def test_factcheck_rejects_players_outside_the_facts():
    result = check(["Theo Varga scores"], [{"scorer": "Felix Castell"}], squad_names=["Theo Varga", "Felix Castell"])
    assert not result.ok


def test_names_must_survive_translation():
    assert check_names_kept(["¡Gol de Felix Castell!"], "Felix Castell scores", ["Felix Castell"]).ok
    assert not check_names_kept(["¡Gol de Félix Castel!"], "Felix Castell scores", ["Felix Castell"]).ok


def test_factcheck_rejects_mixed_alphabets():
    assert check(["¡Gol del Kestrel City!"], [{}]).ok
    result = check(["¡Gol del Kestrел City!"], [{}])  # Cyrillic "ел" sneaked in
    assert not result.ok and "non-Latin" in result.problems[0]


def test_factcheck_accepts_negative_numbers():
    assert check(["Momentum sits at -0.51, all Marlowe."], [{"momentum": -0.51}]).ok


# ---------------------------------------------------------------- crew workflow

def test_happy_path_runs_every_agent(goal):
    agents = FakeAgents()
    [c] = cover(agents, goal)
    assert c.source == "ai"
    assert agents.calls == ["analyst", "commentator", "producer"]
    assert [s.agent for s in c.trace] == ["analyst", "commentator", "fact_checker", "producer", "fact_checker"]


def test_fact_check_failure_loops_back_to_commentator(goal):
    agents = FakeAgents(bad_drafts={"commentator": 1})
    [c] = cover(agents, goal)
    assert c.source == "ai" and "999" not in c.line
    assert agents.calls.count("commentator") == 2
    assert any("rejected" in s.note for s in c.trace)


def test_gives_up_after_three_bad_drafts_and_falls_back(goal):
    agents = FakeAgents(bad_drafts={"commentator": 99})
    [c] = cover(agents, goal)
    assert c.source == "fallback" and c.line == goal[0]["title"] + "."
    assert agents.calls.count("commentator") == 3
    assert "producer" not in agents.calls  # nothing to personalise from an unverified draft


def test_producer_failure_falls_back_to_verified_neutral_line(goal):
    agents = FakeAgents(bad_drafts={"producer": 99})
    [c] = cover(agents, goal, [ViewerProfile(language="es")])
    assert c.source == "ai" and c.line == "What a moment!" and c.language == "en"


def test_studio_feed_skips_the_producer(goal):
    agents = FakeAgents()
    [c] = cover(agents, goal, [ViewerProfile(persona="analyst")])
    assert c.source == "ai" and "producer" not in agents.calls


def test_analyst_outage_does_not_stop_the_show(goal):
    [c] = cover(FakeAgents(fail={"analyst"}), goal)
    assert c.source == "ai" and "failed" in c.trace[0].note


def test_one_version_per_profile(goal):
    profiles = [ViewerProfile(), ViewerProfile(persona="analyst", language="es"), ViewerProfile(favorite_team="kestrel")]
    out = cover(FakeAgents(), goal, profiles)
    assert sorted(c.profile_key for c in out) == sorted(p.key() for p in profiles)


def test_recap_workflow(goal):
    _, snap, players = goal
    agents = FakeAgents(bad_drafts={"recap": 1})
    out = asyncio.run(BroadcastCrew(agents).recap(snap, snap["moments"], players, ViewerProfile()))
    assert out["recap"]["headline"] and agents.calls.count("recap") == 2


def test_workflow_diagram():
    mermaid = BroadcastCrew(FakeAgents()).diagram()
    for node in ("analyst", "commentator", "fact_checker", "producer"):
        assert node in mermaid


# ---------------------------------------------------------------- relevance and service

def test_relevance():
    goal = {"type": "goal", "importance": 0.9, "team_id": "kestrel", "player_ids": []}
    press = {"type": "pressing_spell", "importance": 0.4, "team_id": "marlowe", "player_ids": []}
    sub = {"type": "substitution", "importance": 0.15, "team_id": "kestrel", "player_ids": []}
    assert wants_commentary(ViewerProfile(), goal)
    assert not wants_commentary(ViewerProfile(), press)
    assert wants_commentary(ViewerProfile(persona="analyst"), press)
    assert wants_commentary(ViewerProfile(favorite_team="marlowe"), press)
    assert not wants_commentary(ViewerProfile(favorite_team="kestrel"), sub)


def test_service_caches_and_dedupes(goal):
    moment, snap, players = goal
    agents = FakeAgents()
    service = BroadcastService(agents)

    async def main():
        same = [service.cover("m1", moment, snap, players, ViewerProfile()) for _ in range(3)]
        await asyncio.gather(*same)  # three viewers at once -> one crew run
        await service.cover("m1", moment, snap, players, ViewerProfile())  # replay -> cached
        await service.cover("m1", moment, snap, players, ViewerProfile(language="es"))  # new profile -> new run

    asyncio.run(main())
    assert agents.calls.count("analyst") == 2


def test_context_says_who_is_winning(goal):
    from broadcast.context import moment_context
    moment, snap, players = goal
    situation = moment_context(moment, snap, players)["match"]["situation"]
    assert situation == "level" or " lead by " in situation


def test_voice_ssml_is_escaped_and_native():
    from broadcast.voice import ssml
    doc = ssml("Kestrel <3 & Marlowe", "fr", excited=True)
    assert "fr-FR-Remy" in doc and "&lt;3 &amp;" in doc and "prosody" in doc


def test_daily_budget_caps_fresh_runs_but_not_cached_ones(goal):
    from datetime import datetime, timedelta, timezone
    from broadcast.limits import DailyBudget
    moment, snap, players = goal
    day = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    agents = FakeAgents()
    service = BroadcastService(agents, budget=DailyBudget(1, now=lambda: day[0]))

    async def run():
        first = await service.cover("m1", moment, snap, players, ViewerProfile())
        again = await service.cover("m1", moment, snap, players, ViewerProfile())  # cached: free
        other = await service.cover("m1", moment, snap, players, ViewerProfile(language="es"))  # over budget
        day[0] += timedelta(days=1)
        tomorrow = await service.cover("m1", moment, snap, players, ViewerProfile(language="es"))
        return first, again, other, tomorrow
    first, again, other, tomorrow = asyncio.run(run())
    assert first["source"] == "ai" and again == first
    assert other["source"] == "fallback" and other["trace"][0]["agent"] == "budget"
    assert tomorrow["source"] == "ai"


def test_rate_limiter_window():
    from broadcast.limits import RateLimiter
    t = [0.0]
    rl = RateLimiter(2, 600, clock=lambda: t[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")
    t[0] = 601
    assert rl.allow("a")


def test_factcheck_rejects_crediting_the_goal_to_the_wrong_team():
    ctx = {"moment": {"type": "goal", "team": "Marlowe United"},
           "match": {"home": "Kestrel City", "away": "Marlowe United"}}
    assert not check(["Felix Castell scored."], [ctx], headlines=["But pour Kestrel City tôt dans le match"]).ok
    assert not check(["What a goal for Kestrel!"], [ctx]).ok
    for ok in ["But de Felix Castell pour Marlowe", "Gol temprano de Marlowe United",
               "Kestrel City trails after early Marlowe goal", "Port Ellis pierde tras build-up y gol de Marlowe"]:
        assert check([], [ctx], headlines=[ok]).ok, ok
