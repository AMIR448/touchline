# Streaming protocol

Each viewer opens a WebSocket and gets their own live playback of a match, with their
own speed, pause and seek. Under the hood, every event passes through the stats engine
before it reaches the viewer, so stats and moments arrive in step with the action.

```
ws://localhost:8000/ws/matches/{match_id}?speed=10&autoplay=true
```

## Server → viewer

| `type` | When | Payload |
|---|---|---|
| `hello` | On connect | `match` (clubs, lineups, tactics, scenario), `match_id`, `duration_ms`, `duration_minutes` |
| `event` | Each event as its time comes | `event`: the raw event plus `display` coordinates (see below) |
| `moment` | Right after the event that triggered it | `moment`: type, title, importance, facts, evidence |
| `state` | After moments, and at most once a second otherwise | `state`: the full snapshot (score, team stats, last 10 min, momentum and chaos timelines, top players, recent moments) |
| `status` | On connect and after every command | `playing`, `speed`, `ts_ms`, `clock`, `finished` |
| `reset` | After a seek or restart | `event` (the last event before the target) and `state` rebuilt up to that point |
| `end` | Final whistle | |
| `error` | Bad command or unknown match | `message` |

`display.start` / `display.end` are `[x, y]` in one shared frame where **home attacks left
to right**, so the UI can draw every event without worrying about whose perspective it was
recorded from.

## Viewer → server

```json
{"cmd": "play"}
{"cmd": "pause"}
{"cmd": "speed", "value": 60}     // 0.25 – 600x real time
{"cmd": "seek", "minute": 75}      // match-clock minute; 45+ means the second half
{"cmd": "restart"}
```

## Why per-viewer playback

Judges (and fans) each want to jump to the interesting part at their own pace. The
match itself is a fixed event list, and re-running the stats engine is cheap (a full
match takes well under a second), so each connection simply gets its own playback and
stats engine.

## Going to Azure (step 6)

Locally, events come from the simulator. In the deployed version the same `Playback`
consumes events published to **Azure Event Hubs** by the simulator running as a separate
service, which is the "ingest" stage of the pipeline. The message protocol above stays the same.
