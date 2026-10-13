# Touchline: notes for GitHub Copilot

Touchline turns synthetic football match events into explainable, personalized match intelligence.
Numbers come from code (the stats engine in `analytics/`); words come from AI agents (`broadcast/`),
and a code fact-checker (`broadcast/factcheck.py`) verifies every number and name an agent writes.

## Asking about a match
This repo's `.vscode/mcp.json` connects Copilot to Touchline's MCP server. In agent mode, use its
tools instead of guessing: call `list_matches` first, then `match_situation`, `team_stats`,
`player_stats`, `top_players` or `key_moments` with that `match_id` (and optionally a `minute`).
Clubs and players are fictional; quote numbers exactly as the tools return them.

## Working on the code
- Python 3.13, `pip install -e ".[dev]"`, run `pytest -q` (no cloud calls: tests use fake agents).
- Keep "numbers from code, words from AI": agents never compute stats; give them facts.
- Every agent output is structured (Pydantic models in `broadcast/models.py`) and fact-checked.
- The web app (`server/static/app/`) is Preact + htm with no build step.
