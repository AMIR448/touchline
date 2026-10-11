# The broadcast crew (AI agents)

Built with **Microsoft Agent Framework** on models deployed in **Microsoft Foundry**.
Every key moment the stats engine detects is handed to a crew of specialised agents,
the way a TV broadcast has an analyst, a commentator, a fact-checker and a producer.

```mermaid
flowchart TD
  analyst["analyst (Start)"];
  commentator["commentator"];
  fact_checker["fact_checker"];
  producer["producer"];
  analyst --> commentator;
  commentator --> fact_checker;
  fact_checker -. conditional .-> commentator;
  fact_checker -. conditional .-> producer;
```
*(Generated from the live workflow by `BroadcastCrew.diagram()`.)*

| Agent | Model | Job | Output |
|---|---|---|---|
| 🧠 **Analyst** | gpt-5-mini (reasoning, minimal effort) | The *why*: tactical cause or consequence, backed by numbers | `Insight`: headline, why it matters, supporting numbers, angle |
| 🎙 **Commentator** | gpt-4.1-mini | Broadcast words: a lower-third caption and an on-air line | `Commentary` |
| ✅ **Fact-checker** | *plain code* | Every number and player name must exist in the facts; no mixed alphabets | pass, or a list of problems sent back for a rewrite |
| 🎛 **Producer** | gpt-4.1-mini | One version per viewer: analyst or casual, favourite club and player, language | `PersonalizedLine`, fact-checked again |
| 📰 **Recap writer** | gpt-5-mini | Half-time and full-time story, with its own fact-check loop | `Recap` |

## Ask the match (Q&A agent)

Fans type a question during the match ("How is Castell playing?", "Why are Marlowe winning?").
Unlike the commentary crew, nobody hands this agent its facts: **it decides what to look up.**

```
question ──▶ MatchQA agent ──calls the tools it needs──▶ answer ──▶ Fact-checker ──▶ fan
                  ▲                                                     │
                  └──────────── one rewrite with the problems ◀─────────┘
```

- **Tools** (`broadcast/match_tools.py`): `match_situation`, `team_stats`, `player_stats`,
  `top_players`, `key_moments`. Plain code over the stats engine, passed to an Agent Framework
  `Agent` as function tools. The model (gpt-4.1-mini) picks which to call, often several.
- **No spoilers**: tools read a snapshot frozen at the minute the question was asked.
- **Fact-checked against what it looked up**: every number and name in the answer must appear in
  the tool results (or the current situation). Otherwise one rewrite, then an honest
  "I couldn't verify that" instead of a guess.
- **Stays on topic**: questions about real clubs, betting or anything else get a polite refusal.
- The UI shows which tools were called and opens the full trace from the "Fact-checked" chip.

## MCP server

The same tools are served over the **Model Context Protocol** at `/mcp/` (streamable HTTP,
`server/mcp_server.py`), with a `match_id` and an optional `minute`. Any MCP client (GitHub
Copilot, Claude, a Foundry agent, a club's own analyst bot) can query a match:

```json
{ "mcpServers": { "touchline": { "type": "http", "url": "http://localhost:8000/mcp/" } } }
```

## Design principles

- **Numbers come from code, words come from AI.** Agents only receive a slice of the stats
  engine's state (`broadcast/context.py`) and may only use numbers from it.
- **Structured output everywhere.** Agents return validated JSON (Pydantic models), so each
  step can rely on exactly what the previous one produced.
- **Verification is code, not another model.** The fact-checker cannot hallucinate or be
  persuaded. Rejections go back to the writer with the exact problems (up to 2 rewrites).
- **The show goes on.** If the analyst is down, the commentator works from raw facts. If the
  commentary can't be verified, a safe line from the stats engine is shown. If personalisation
  fails, the verified neutral line is used. Every path is covered by tests (`tests/test_broadcast.py`).
- **Transparent.** Each result carries a trace (agent, time, fact-check notes), shown in the UI
  under "behind the scenes".
- **Keyless.** Foundry is called with Microsoft Entra ID (`az login` locally, managed identity
  in Azure). There are no API keys in code or config.
- **Cheap and fast.** One crew run is about 4–8 s and well under a cent. Results are cached per
  (match, moment, viewer profile), so identical viewers and replays cost nothing.

## Who gets commentary

`broadcast/relevance.py` (code, not AI): casual fans get the big moments; analysts also get
tactical ones (pressing spells, momentum swings, long possessions); anything involving your
club or player is included at a lower threshold. Substitutions never; half-time and full-time
get a recap instead.

## Languages

English, Spanish, French and Portuguese. All are Latin-script, so player names stay exactly as
written and the fact-checker can verify them. Adding one is a one-line change in `broadcast/models.py`.
