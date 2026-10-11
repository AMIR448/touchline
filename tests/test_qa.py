import asyncio

import pytest

from analytics.analyzer import MatchAnalyzer
from broadcast.agents import FakeAgents
from broadcast.match_tools import MatchTools
from broadcast.models import ViewerProfile
from broadcast.qa import MatchQA
from simulator.clubs import CLUBS
from simulator.engine import simulate


@pytest.fixture(scope="module")
def half_time():
    """A known match, frozen at half-time."""
    a = MatchAnalyzer()
    for ev in simulate(CLUBS["kestrel"], CLUBS["marlowe"], 21):
        a.process(ev)
        if ev["type"] == "period_end":
            break
    return a


def tools(a):
    return MatchTools(a.snapshot(max_moments=1000), {p: a.player(p) for p in a.players})


def test_tools_answer_from_the_stats_engine(half_time):
    t = tools(half_time)
    sit = t.match_situation()
    assert sit["period"] == 1 and "-" in sit["score"]
    assert set(t.team_stats()["whole_match"]) == {"Kestrel City", "Marlowe United"}
    scorer = t.top_players(by="goals", limit=1)["players"][0]
    assert t.player_stats(scorer["name"])["goals"] == scorer["goals"]
    assert "several_players_match" in t.player_stats("Pryce")  # two Pryces: the tool asks, never guesses
    assert all(m["type"] == "goal" for m in t.key_moments(kind="goal")["moments"])


def test_tools_handle_bad_input(half_time):
    t = tools(half_time)
    assert "error" in t.player_stats("Nobody McNoname")
    assert "options" in t.top_players(by="vibes")
    assert "several_players_match" in t.player_stats("1")  # both goalkeepers wear 1


def test_tools_never_see_the_future(half_time):
    """Questions are answered from a snapshot: later events don't leak in."""
    t = tools(half_time)
    assert all(m["clock"] < "48" for m in t.key_moments(limit=15)["moments"])


def ask(agents, a, profile=None):
    return asyncio.run(MatchQA(agents).ask("What's the score?", tools(a), profile or ViewerProfile()))


def test_answer_is_fact_checked_against_tool_results(half_time):
    out = ask(FakeAgents(), half_time)
    assert out["source"] == "ai" and out["tools"][0]["tool"] == "match_situation"
    assert [s["agent"] for s in out["trace"]] == ["match_qa", "fact_checker"]


def test_invented_number_triggers_a_rewrite(half_time):
    agents = FakeAgents(bad_drafts={"qa": 1})
    out = ask(agents, half_time)
    assert out["source"] == "ai" and "999" not in out["answer"] and agents.calls.count("qa") == 2


def test_gives_an_honest_fallback(half_time):
    out = ask(FakeAgents(bad_drafts={"qa": 9}), half_time, ViewerProfile(language="es"))
    assert out["source"] == "fallback" and "adivinar" in out["answer"]
    assert ask(FakeAgents(fail={"qa"}), half_time)["source"] == "fallback"
