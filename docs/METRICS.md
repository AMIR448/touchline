# Metrics and moments

The stats engine (`analytics/`) reads the event feed one event at a time and keeps a
live picture of the match. Every number here comes from a simple formula, so an agent
(or a person) can always explain *why* it is what it is.

## Team and player metrics

| Metric | What it means | How it's computed |
|---|---|---|
| **xG** (expected goals) | How likely an average player scores that shot | Logistic model on distance and angle; headers ×0.6, under pressure ×0.8, penalty = 0.76 |
| **Big chance** | A shot that *should* be scored | xG ≥ 0.30 |
| **Pass difficulty** (0–10) | How hard the pass was, regardless of who made it | `10 × (1 − completion probability) / 0.8`; probability drops with length, pressure, final-third / box targets; crosses ≈ 30% |
| **Progressive pass** | A pass that really moves the team forward | Ball ends ≥ 25% closer to goal, and at least 10 m closer |
| **Final-third / box entries** | Getting into dangerous areas | Pass or carry that starts outside and ends inside the zone |
| **Field tilt** | Who is camped in the other's half | Share of final-third touches |
| **Possession %** | Share of time on the ball | Time between events, credited to the team on the ball; gaps capped at 8 s so stoppages don't count |
| **PPDA** | Pressing intensity: lower = more aggressive press | Opponent passes in their own 60% ÷ our tackles, interceptions, fouls (and recoveries forced by pressure) in that zone. PL range is roughly 8 (intense) to 15+ (deep block) |
| **Impact** | Simple contribution score for top performers | goals + 0.7·assists + 0.5·xG + 0.2·key passes + small credit for progression and defending |

## Live indices (one value per minute)

| Index | Range | Meaning |
|---|---|---|
| **Momentum** | −1 … +1 | Who is threatening *right now*. "Threat" from final-third entries, box entries, corners and shots (weighted by xG), over the last ~10 minutes with recent minutes counting more. +1 = home dominating |
| **Control vs chaos** | 0 … 100 | The *character* of the game over the last 5 minutes: turnovers per minute, share of actions under pressure, misplaced passes. Below 35 = controlled build-up; above 60 = end-to-end chaos. A typical game sits around 50 |

## Moments

A moment is something a broadcast would mention. Each one carries `facts` (structured
data) and `evidence` (the event IDs behind the facts). It has no prose: the agents write the words.

| Type | Fires when | Typical per match |
|---|---|---|
| `goal` | A goal: scorer, assist, game state (opener / equaliser / takes lead / completes comeback / …), shot xG, build-up | 2–3 |
| `big_chance_missed` | xG ≥ 0.3 shot off target or blocked | ~0.6 |
| `great_save` | A save from a shot with xG ≥ 0.2 | ~0.4 |
| `key_pass` | A hard pass (difficulty ≥ 6) creating a decent chance | ~2 |
| `counter_attack` | Ball won in own half, shot of xG ≥ 0.05 within 15 s | ~1 |
| `sustained_possession` | A possession of 15+ completed passes | ~4 |
| `momentum_swing` | A different team takes clear control (\|momentum\| > 0.4) | ~4 |
| `pressing_spell` | A team's PPDA ≤ 6.5 over 8 minutes with 6+ high ball wins | ~2 |
| `chaos_spike` | Control-vs-chaos crosses 80 | ~3 |
| `thunderbolt` | Shot at 105 km/h or faster | ~1 |
| `card`, `substitution`, `milestone` (brace, hat-trick, 50 passes at 90%) | As named | |
| `half_time`, `full_time` | With full team stats | 1 each |

`importance` (0–1) ranks moments so the UI and agents can focus on what matters
(goals ≈ 0.9–1.0, red cards 0.85, routine substitutions 0.15).
