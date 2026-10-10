# Event format

A match is a stream of JSON objects, one per line (JSON Lines), in time order.
Events contain **raw facts only**. Anything derived (xG, pass difficulty, momentum,
possession %) is computed downstream by the stats engine.

## Example

```json
{"event_id": "kes-mar-7-00412", "match_id": "kes-mar-7", "seq": 412, "type": "pass",
 "period": 1, "clock": "31:08", "ts_ms": 1868250, "team_id": "kestrel", "player_id": "kestrel-07",
 "possession": 57, "x": 61.2, "y": 22.4, "end_x": 84.0, "end_y": 30.1, "outcome": "complete",
 "under_pressure": true,
 "details": {"recipient_id": "kestrel-10", "length_m": 24.1, "ball_speed_kmh": 55.3, "height": "low"}}
```

## Common fields

| Field | Meaning |
|---|---|
| `event_id` | Unique ID, `<match_id>-<seq>`. Agents cite these to back up their explanations. |
| `seq` | 1, 2, 3, … in order |
| `type` | See the list below |
| `period` | 1 or 2 |
| `clock` | `mm:ss` match clock. Keeps running in added time (`47:10` in period 1 = 45+2:10) |
| `ts_ms` | Milliseconds of play since kick-off, half-time excluded. Drives live replay timing |
| `team_id`, `player_id` | Who acted. Squads are listed in `match_start` |
| `possession` | Possession number. Increments whenever the ball changes team (and at each kick-off) |
| `x`, `y` | Where it happened, in metres (pitch 105 × 68) |
| `end_x`, `end_y` | Where the ball ended up (passes, carries, shots, clearances) |
| `outcome` | Type-specific, see below |
| `under_pressure` | `true` if an opponent was pressing the player |
| `related` | IDs of events this one responds to (interception → the pass it cut out, goal → its shot, shot → the assisting pass) |
| `details` | Type-specific extras |

**Coordinates are from the acting team's point of view, attacking toward x = 105.**
So for every team x = 105 is the goal they attack. To place an opponent's event on
the same picture, mirror it: `(105 − x, 68 − y)`.

## Event types

| Type | Outcome | Notable `details` |
|---|---|---|
| `match_start` | | `home`, `away` (club, formation, tactics, starters with positions, bench), `venue`, `scenario`, `seed` |
| `period_start` / `period_end` | | `score`, `added_minutes` (on end) |
| `match_end` | | `score`, `winner` (null for a draw) |
| `pass` | `complete` / `incomplete` / `out` | `recipient_id` or `intended_recipient_id`, `length_m`, `ball_speed_kmh`, `height` (`ground`/`low`/`high`), `subtype` (`kick_off`, `throw_in`, `goal_kick`, `corner`, `free_kick`, `cross`) |
| `carry` | | `distance_m` |
| `dribble` | `complete` / `incomplete` | `opponent_id` |
| `shot` | `goal` / `saved` / `blocked` / `off_target` | `body_part`, `shot_speed_kmh`, `subtype` (`penalty`, `free_kick`) |
| `goal` | | `score` (after the goal), `assist_player_id` |
| `pressure` | | |
| `tackle` | `won` | |
| `interception` | | |
| `ball_recovery` | | Picking up a loose or misplaced ball |
| `clearance` | | |
| `goalkeeper_save` | | |
| `foul` | | |
| `card` | | `card`: `yellow` / `second_yellow` / `red` |
| `substitution` | | `player_off_id`, `player_on_id`, `position` |

## Realism

`tests/test_simulator.py` checks that averages over many matches sit near Premier League
per-team norms. A recent 100-match run:

| Per team, per match | Simulator | PL typical |
|---|---|---|
| Goals | 1.43 | ~1.4 |
| Shots / on target | 11.6 / 4.4 | ~12.5 / ~4.3 |
| xG | 1.54 | ~1.4 |
| Passes / accuracy | 514 / 80% | ~450–500 / ~82% |
| Corners | 6.0 | ~5 |
| Fouls | 11.0 | ~10.5 |
| Yellows / reds (per match) | 3.3 / 0.17 | ~3.7 / ~0.1 |
