"""Touchline as an MCP server: the match tools for any MCP client.

The Q&A agent inside Touchline uses these same tools (broadcast/match_tools.py). Exposing them over
MCP means GitHub Copilot, Claude, a Foundry agent or any other MCP client can ask about a live
match too, for example a club's own analyst bot. Served at /mcp/ (streamable HTTP).

Every tool takes a `match_id` (from `list_matches`) and an optional `minute`: the match as it stood
at that minute of the match clock, so a client following a replay never gets spoilers.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from analytics.analyzer import MatchAnalyzer
from broadcast.match_tools import MatchTools


def tools_at(events: list[dict], minute: int | None) -> MatchTools:
    """Replay the stats engine up to `minute` (45+ means the second half); None = the whole match."""
    a = MatchAnalyzer()
    period = 1 if minute is None or minute < 45 else 2
    for ev in events:
        clock_min = int(ev["clock"].split(":")[0])
        if minute is not None and ev["type"] != "match_start" and (
                ev["period"] > period or (ev["period"] == period and clock_min >= minute)):
            break
        a.process(ev)
    return MatchTools(a.snapshot(max_moments=1000), {p: a.player(p) for p in a.players})


def build(matches: dict[str, list[dict]]) -> MCPServer:
    mcp = MCPServer("touchline", instructions=(
        "Football match intelligence for synthetic matches between fictional clubs. Call list_matches first, "
        "then pass its match_id to the other tools. `minute` (optional) gives the match as it stood then."))

    def tools(match_id: str, minute: int | None) -> MatchTools:
        if match_id not in matches:
            raise ValueError(f"unknown match_id {match_id!r}; call list_matches")
        return tools_at(matches[match_id], minute)

    @mcp.tool()
    def list_matches() -> list[dict[str, Any]]:
        """Matches available to query, with clubs, scenario and final score."""
        out = []
        for match_id, events in matches.items():
            d = events[0]["details"]
            out.append({"match_id": match_id, "home": d["home"]["name"], "away": d["away"]["name"],
                        "scenario": d.get("scenario"), "minutes": int(events[-1]["clock"].split(":")[0]) + 1})
        return out

    @mcp.tool()
    def match_situation(match_id: str, minute: int | None = None) -> dict[str, Any]:
        """Score, clock, who is winning, momentum and how chaotic the game is."""
        return tools(match_id, minute).match_situation()

    @mcp.tool()
    def team_stats(match_id: str, minute: int | None = None) -> dict[str, Any]:
        """Both teams' stats for the match so far and for the last 10 minutes."""
        return tools(match_id, minute).team_stats()

    @mcp.tool()
    def player_stats(match_id: str, name: str, minute: int | None = None) -> dict[str, Any]:
        """One player's match so far, by full name, surname or shirt number."""
        return tools(match_id, minute).player_stats(name)

    @mcp.tool()
    def top_players(match_id: str, by: str = "impact", team: str | None = None, limit: int = 5,
                    minute: int | None = None) -> dict[str, Any]:
        """Best players so far ranked by one stat (impact, goals, xg, key_passes, tackles, ...)."""
        return tools(match_id, minute).top_players(by, team, limit)

    @mcp.tool()
    def key_moments(match_id: str, kind: str | None = None, team: str | None = None, limit: int = 8,
                    minute: int | None = None) -> dict[str, Any]:
        """Key moments so far, newest first, with the facts behind each."""
        return tools(match_id, minute).key_moments(kind, team, limit)

    return mcp
