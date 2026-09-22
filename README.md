# divine.

A friends-only cycling prediction game: pick and order the riders you expect in
the top ten, then compare the result with the league after the race.

## Local development

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m app.db init --seed-mock-data
python -m uvicorn app.main:app --reload
```

The API is then available at `http://127.0.0.1:8000`; interactive docs are at `/docs`. The database is local SQLite at `data/game.sqlite3` and is intentionally ignored by git.

## Deployment

The public deployment is split into three free services:

- GitHub Pages serves the static files from `frontend/` using
  `.github/workflows/pages.yml`.
- Render runs the FastAPI service described by `render.yaml`.
- Neon stores the persistent PostgreSQL database. Local development continues
  to use SQLite.

Set `DATABASE_URL` on Render to Neon's pooled connection string. Keep
`ADMIN_API_KEY` secret; the public admin page asks for it and stores it only for
the browser session. `CORS_ORIGINS` must contain the GitHub Pages origin.

The Render build imports the checked-in 2026 startlist and rider reference
exports idempotently, so a fresh database is ready when the service starts.

### One-off 2026 startlist import

The importer consumes a saved PCS startlist page and fetches the current official UCI Road Men Elite ranking once; it does not schedule refreshes.

```powershell
python -m app.import_road_worlds_2026 "C:\path\to\PCS startlist.html"
```

It creates the real 2026 event, uses PCS rider slugs as stable import identifiers, and adds UCI rank/points where the name match is unambiguous. Riders outside the fetched ranking are marked `Unranked` rather than guessed.

### Rebuild the local database from the checked-in CSV

When you want a clean local game populated from the current CSV snapshot, stop
the server, remove only `data/game.sqlite3`, then run:

```powershell
Remove-Item -LiteralPath .\data\game.sqlite3
python -m scripts.load_road_worlds_2026_csv .\exports\road-worlds-2026-startlist.csv
python -m uvicorn app.main:app --reload
```

This creates the 2026 event and all riders directly from the CSV, including
their UCI rank, UCI points, country and starter status. It intentionally does
not add the fictional mock event.

### ProCyclingStats data fetchers

Three commands read ProCyclingStats and write to `exports/pcs/`. They share one
HTTP client (`scripts/pcs/`) that pauses between requests, backs off when PCS
throttles it, and caches every page under `.cache/pcs/`, so re-running a fetcher
downloads nothing unless you pass `--refresh`.

```powershell
# UCI ranking: rank, points and trade team now, plus the move since a reference date
python -m scripts.fill_uci_points .\exports\road-worlds-2026-startlist.csv .\exports\pcs\startlist-enriched.csv

# rider profiles: age, career wins, wins this season
python -m scripts.fetch_pcs_riders

# past results of the tracked one-day races, matched to the startlist
python -m scripts.fetch_pcs_races
```

`fill_uci_points` reads the ranking twice -- `--date` (2026-09-22) and
`--compare-date` (2025-12-30) -- and adds `team`, `uci_rank_prev`,
`uci_points_prev` and the absolute and relative movement of both rank and
points. Positive always means "better": `uci_rank_change` counts places gained.
Pass `--no-compare` for a single snapshot.

`fetch_pcs_riders` visits two pages per rider and writes `riders.csv` (age, date
of birth, team, `wins_total`, `wins_season`) plus `rider-seasons.csv` with the
full per-season table. A cold run over the 190-rider startlist takes about
fifteen minutes; use `--season` for a year other than 2026.

`fetch_pcs_races` fetches the Worlds, Strade Bianche, San Sebastián, GP Québec,
GP Montréal, Paris-Roubaix, the Ronde, Milano-Sanremo, Liège-Bastogne-Liège and
Il Lombardia for 2023-2026, plus the 2024 Olympic road race. It writes
`race-editions.csv` (one row per edition, including why an empty one is empty),
`race-results.csv` (long form) and `rider-race-results.json`, which is the
per-rider pivot the frontend can render directly:

```json
"remco-evenepoel": {
  "name": "Remco Evenepoel",
  "results": {
    "world-championship": {"2023": {"rank": 25, "status": ""}, "2025": {"rank": 2, "status": ""}}
  }
}
```

A missing year means the rider was not on that startlist; a `null` rank with a
status means he started and did not finish classified. Editions still to be
ridden -- the 2026 Worlds and Il Lombardia -- are reported and skipped, never
guessed. The race list lives in `scripts/pcs/races.py` and `--races` narrows it.

PCS disallows automated access in robots.txt. Run these from your own machine,
not from a server or CI, and leave the request delay alone.

### Load the PCS data into the database

The fetchers write files; this puts them in SQLite so the API and frontend can
query them. Two commands, because they touch different things:

```powershell
# reference data: profiles, season totals, ranking snapshots, race results
python -m scripts.load_pcs_data

# the live UCI rank and points the game scores on
python -m scripts.import_startlist_csv exports/pcs/startlist-enriched.csv --ranking-date 2026-09-22
```

`load_pcs_data` fills six tables and never touches `event_riders`, so it cannot
disturb an open game. Both commands take `--dry-run`, and both are idempotent:
rows are matched on their natural key and updated in place, so re-running after
a fresh fetch converges on whatever the exports currently say.

| table | one row per | holds |
| --- | --- | --- |
| `rider_profiles` | rider | PCS name, team, age, date of birth, career wins |
| `rider_seasons` | rider and season | points, racedays, kms, wins, top-3s, top-10s |
| `rider_rankings` | rider and ranking date | UCI rank, points, trade team |
| `races` | tracked race | key, display label, PCS slug |
| `race_editions` | race and year | name, date, classification, category, distance |
| `race_results` | rider and edition | position, status, team, age, points, time |

Ranking movement is not stored. It is the difference between two `rider_rankings`
rows, which keeps any pair of snapshot dates comparable:

```python
now = select(RiderRanking).where(RiderRanking.ranking_date == date(2026, 9, 22))
was = select(RiderRanking).where(RiderRanking.ranking_date == date(2025, 12, 30))
```

A rider missing from a snapshot has no row for that date, and a `race_results`
row with a null `position` carries the PCS marker in `status` (DNF, DNS, OTL).
Editions with nothing to show keep the reason in `race_editions.note`.

New tables appear through `init_db()`, so a running `uvicorn --reload` picks
them up by itself and an existing database needs no reset.

`docs/FRONTEND_DATA.md` is the guide for building UI on top of these tables:
what each one holds, how to serve it in one payload, the edge cases that
otherwise render wrong, and transformations worth showing.

## Architecture at a glance

- **Frontend:** static HTML/CSS/JS, deployable to GitHub Pages. It talks to the API using JSON REST calls.
- **Backend:** FastAPI application exposing auth-lite player registration, championship/event data, predictions, scoring, and leaderboard endpoints.
- **Database:** SQLite locally and managed PostgreSQL in the public deployment,
  using the same SQLAlchemy models.
- **Data ingestion:** isolated PCS fetchers in `scripts/pcs/` normalize rider, ranking, and race data. Raw pages are cached on disk, and the imported reference tables stay separate from scoring, which reads only `event_riders` and `event_results`.
- **Scoring:** deterministic, versioned server-side rules. The client displays scores but never calculates the authoritative result.

The mock seed remains available for isolated local testing; the public build
loads the checked-in 2026 Road Worlds data instead.

## Product guardrails

- This is a social prediction game, not real-money gambling: no payments, odds, deposits, or withdrawals.
- A username is an identifier only; do not collect passwords or personal data in the first version.
- Entering an existing username is the deliberate "sign in" flow. It is convenient for a trusted friend group, but it is not authentication: anyone who knows a username can edit that player's prediction.
- Lock submissions at the configured event start time and retain an audit-friendly scoring version.
- Treat external UCI data as unreliable input: validate, timestamp, cache, and expose the source timestamp to users.

