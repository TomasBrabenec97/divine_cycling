# Implementation plan

These levels are ordered so later agents can implement and verify one coherent slice at a time.

## Level 1 — Foundation and local boot

Goal: establish a repeatable developer environment and prove the API/database boundary.

Deliverables: virtualenv instructions, dependency lock/update strategy, settings, SQLite initialization, SQLAlchemy base, health endpoint, basic test, and a clean README.

Acceptance criteria:

- `python -m app.db init` creates `data/game.sqlite3` without errors.
- `python -m uvicorn app.main:app` starts and `GET /api/health` returns HTTP 200 with `status=ok`.
- `pytest` passes from a fresh activated environment.
- No credentials or generated database files are committed.

## Level 2 — Event, rider, and username flow

Goal: make one championship playable with verified, minimal domain data.

Deliverables: event/rider/startlist models and migrations, username registration and lookup, event read endpoints, validation for exactly 10 distinct starters, and a static frontend shell.

Acceptance criteria:

- A user can register a unique username; duplicate names receive a clear 409 response.
- An event endpoint returns deadline, status, data timestamp, and only eligible starters.
- Prediction submission rejects duplicates, non-starters, wrong count, and submissions after lock time.
- The frontend can load the event and submit a valid prediction without hand-editing JSON.

## Level 3 — Official data adapter and submission UX

Goal: replace fixtures with a safe, refreshable data pipeline and finish the core game experience.

Deliverables: UCI adapter interface, normalized import job, raw-response cache, source timestamps, startlist validation/reporting, client session persistence, prediction editing until lock, and a clear confirmation screen.

Acceptance criteria:

- A fixture-based adapter test proves rider names, ids, UCI ranks, and startlist flags normalize deterministically.
- Failed or incomplete imports do not delete the last known valid dataset.
- The UI visibly shows the data timestamp and submission lock deadline.
- A player can reload the page and see their saved prediction for the active event.

## Level 4 — Scoring, boosts, and leaderboard

Goal: make results deterministic, explainable, and fun without letting modifiers overwhelm accuracy.

Deliverables: versioned scoring rules, exact/near-position points, one or more constrained boost mechanics, capped UCI-rank difficulty margin, result import, score breakdown endpoint, leaderboard, and tests for edge cases.

Acceptance criteria:

- Given a frozen result and prediction fixture, scores are byte-for-byte deterministic.
- Every score line explains base points, boost effect, difficulty margin, and final points.
- Boost budget, rank multiplier cap, ties, DNS/DNF, and fewer-than-10 finishers are covered by tests.
- Historical score runs remain unchanged after a later UCI refresh or ruleset change.

## Level 5 — Friend-group polish and deployment

Goal: deliver a reliable, lightweight game that friends can use from a shared link.

Deliverables: invite/group code or private room, standings and shareable result view, responsive visual polish, rate limits and input limits, CORS policy for GitHub Pages, CI, deployment configuration, backups, and observability.

Acceptance criteria:

- Two browsers can join the same room with usernames and see the same locked leaderboard.
- The frontend is deployable to GitHub Pages and the API is configurable by environment variable.
- CI runs linting, unit/integration tests, and a production frontend build on every push.
- A documented restore procedure can recover the database and score history.
- The app contains no real-money wagering, payment, or secret-dependent client behavior.

