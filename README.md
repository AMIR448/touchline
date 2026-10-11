# Touchline

**Explainable, personalized match intelligence from live football events.**
Built for *Inside the Game: Developer Hackathon* (Microsoft + Premier League), Oct 2026.

## The end goal

A web app where you press **Play** on a (synthetic) match and watch, in real time:

- a 2D pitch with the ball moving event by event,
- broadcast-style overlays: stats, key moments, *why* a moment mattered,
- a live narrative and an automatic full-time recap,
- all of it **personalized**: analyst vs casual fan, favourite club, favourite player, language.

Judges watch a demo of under 2 minutes, then click a public link to try it themselves.

## The pipeline (the 5 stages the hackathon asks for)

```
 ┌────────────┐   ┌──────────┐   ┌─────────────┐   ┌─────────────┐   ┌──────────┐   ┌──────────────┐
 │ Simulator  │──▶│  INGEST  │──▶│  INTERPRET  │──▶│   EXPLAIN   │──▶│  RENDER  │──▶│ PERSONALIZE  │
 │ (synthetic │   │ event    │   │ stats engine│   │ AI agents   │   │ overlays │   │ per-viewer   │
 │  events)   │   │ stream   │   │ (plain code)│   │ (Foundry)   │   │ + recap  │   │ agent        │
 └────────────┘   └──────────┘   └─────────────┘   └─────────────┘   └──────────┘   └──────────────┘
```

Rule of thumb: **numbers come from code, words come from AI.** Agents never invent a stat;
they explain stats the engine computed, and cite the event IDs behind them.

## Build steps

| # | Step | Status |
|---|------|--------|
| 1 | **Simulator**: fictional clubs + a match engine that emits realistic events | ✅ done |
| 2 | **Stats engine**: possession, pass difficulty, xG, PPDA, momentum, "control vs chaos", key moments | ✅ done |
| 3 | **Streaming**: live playback over WebSocket with play/pause/speed/seek + dev viewer (Event Hubs at deploy) | ✅ done |
| 4 | **Agents**: Analyst, Commentator, Fact-checker, Producer, Recap writer (Agent Framework on Foundry) | ✅ done |
| 5 | **Web app**: Studio mode (broadcast overlays, graphics cue feed, analyst desk) and Fan mode (personalized feed, compare viewers) | ✅ done |
| 5b | **Ask the match**: a tool-using Q&A agent (fact-checked), and the same match tools served over **MCP** at `/mcp/` | ✅ done |
| 5c | **Voice**: the commentator speaks each verified line (Azure AI Speech HD voices, EN/ES/FR/PT) | ✅ done |
| 6 | **Deploy** to Azure Container Apps, public repo, architecture diagram | ⏳ |
| 7 | **Demo video** (< 2 min) + submission | ⏳ |

Deadline: **Oct 27, 2026, 11:59 PM PT** (Oct 28, ~10:00 Israel).

## Step 1: the simulator

All data is synthetic. Clubs, players and matches are invented, so no licensing or trademark issues.

Setup once per terminal: `source .venv/bin/activate`

```bash
touchline clubs                                    # list the fictional clubs
touchline generate --home kestrel --away marlowe --seed 21 --out data/match.jsonl
touchline summary data/match.jsonl                 # sanity-check the stats
touchline replay data/match.jsonl --speed 30       # stream it "live" at 30x
touchline generate --scenario late_comeback ...    # scenarios: see `--help`
```

The event format is documented in [docs/EVENTS.md](docs/EVENTS.md).

## Step 2: the stats engine

Reads the feed one event at a time (like live) and produces the match state plus **key moments**,
each with structured facts and the event IDs that prove them.

```bash
touchline summary data/match.jsonl                   # box score + top performers
touchline analyze data/match.jsonl                   # timeline of key moments
touchline analyze data/match.jsonl --facts           # ...with the facts the agents will receive
touchline analyze data/match.jsonl --json snap.json  # full state: stats, timelines, players
```

Every metric and moment is explained in [docs/METRICS.md](docs/METRICS.md).

## Step 3: live streaming

```bash
touchline serve          # then open http://localhost:8000
```

A FastAPI server plays matches "live" over WebSocket. Every event goes through the stats
engine on the way, so viewers receive events, key moments and match state in sync. Each
viewer controls their own playback (play/pause, 1x–600x, jump to minute). The page at `/`
is a dev viewer; the real UI comes in step 5. Protocol: [docs/STREAMING.md](docs/STREAMING.md).

## Step 4: the broadcast crew (AI agents)

Key moments go to a crew of agents built with **Microsoft Agent Framework** on **Microsoft Foundry**:
an Analyst works out *why* it matters, a Commentator writes the line, a code-based Fact-checker
verifies every number and name (sending failures back for a rewrite), and a Producer tailors it per
viewer: analyst or casual fan, favourite club and player, English / Spanish / French / Portuguese.
Half-time and full-time get a recap. Details and the workflow diagram: [docs/AGENTS.md](docs/AGENTS.md).

```bash
source scripts/env.sh    # project Azure login + .env (Foundry endpoint)
touchline serve          # AI commentary appears in the viewer; "behind the scenes" shows each agent
```

Without a Foundry endpoint the app still runs; viewers see the stats engine's moment titles.

## Step 5: the app

Open http://localhost:8000 after `touchline serve`.

- **Studio mode** (for broadcasters): a broadcast frame with score bug, player tags, threshold badges
  (shot speed, pass difficulty, long passes), goal banners and AI lower-thirds; a **Graphics out** feed
  of timed, machine-readable overlay cues for a graphics renderer (`/api/matches/{id}/cues`); an analyst
  desk with "why it matters", momentum wave, xG race and control-vs-chaos gauge; and **Behind the scenes**,
  showing each agent's work and every fact-check.
- **Fan mode** (second screen): pick your club, player, style and language, and get a personalized
  commentary feed and recaps. **"See it through other fans' eyes"** shows the same moment for an analyst
  and for a fan of each side, side by side, in parallel.
- **Generate match**: simulate a new fixture with a scenario (late comeback, red card, high press, …).

All clubs, players and crests are fictional and generated; no third-party trademarks are used.
