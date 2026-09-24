# Architecture

## Core flow

1. A player chooses a unique username; the API returns a player id and a short-lived client token or signed session in a later level.
2. The active championship contains the official event, deadline, riders, UCI rank snapshot, and verified startlist.
3. The player selects exactly 10 distinct starters and optionally assigns one conviction boost, subject to a server-side budget/rule.
4. After the deadline, an admin/import job records the official result and the scoring service computes an immutable score using a named ruleset version.
5. The leaderboard exposes totals and a transparent per-rider breakdown.

## Suggested domain model

`players`, `events`, `riders`, `event_riders`, `predictions`, `prediction_items`, `results`, `score_runs`, `score_items`.

Keep `riders` stable across events. Put ranking and startlist status in `event_riders`, because both change over time. A prediction must reference the event and the rider snapshot it was submitted against.

## Scoring direction

Scoring model v2 is specified in `SCORING_MODEL_V2.md`: placement points (base points by guessed position × distance factor × a capped UCI-rank multiplier), flat permutation bonuses for the top 3, top 5 and top 10, and three unpositioned wildcards with their own steeper rank multiplier. The earlier conviction boost is retired.

All scoring inputs and the ruleset version must be stored in `score_runs`; never recompute historical leaderboards from today's UCI ranking.

## Deployment shape

GitHub Pages can host the static frontend only. The FastAPI API and database need a separate reachable host. For a free prototype, use a small free-tier Python host and a managed SQLite-compatible or Postgres database; keep `DATABASE_URL` configurable. GitHub Actions should build/check the frontend and run backend tests, while secrets remain in deployment settings.

