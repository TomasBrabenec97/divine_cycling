# PCS reference data — a guide for the frontend

Rider profiles and past race results are imported from ProCyclingStats and live
in SQLite next to the game tables. This is what is there, how to reach it, and
what is worth building with it.

**Read this first:** the API does not expose any of it yet. `/api/events/active`
still returns only `id, name, nation, uci_rank, uci_points`. Adding an endpoint
is step one — see [Getting at it](#getting-at-it).

## What is in the database

All six tables are read-only background. Scoring never touches them; it reads
`event_riders` and `event_results` only, so you cannot break a game from here.

| table | rows | one row per | holds |
| --- | --- | --- | --- |
| `rider_profiles` | 190 | rider | `pcs_name`, `team`, `age`, `date_of_birth`, `wins_total`, `profile_url` |
| `rider_seasons` | 1770 | rider + season | `pcs_points`, `racedays`, `kms`, `wins`, `top3s`, `top10s` (2006-2026) |
| `rider_rankings` | 370 | rider + date | `uci_rank`, `uci_points`, `team` — dates `2025-12-30` and `2026-09-22` |
| `races` | 11 | tracked race | `key`, `label`, `pcs_slug` |
| `race_editions` | 41 | race + year | `name`, `race_date`, `classification`, `category`, `distance`, `note`, `url` |
| `race_results` | 1529 | rider + edition | `position`, `status`, `team`, `age`, `uci_points`, `pcs_points`, `time` |

Tracked races, 2023-2026: Worlds RR, Strade Bianche, San Sebastián, GP Québec,
GP Montréal, Paris-Roubaix, Ronde van Vlaanderen, Milano-Sanremo,
Liège-Bastogne-Liège, Il Lombardia, plus the 2024 Olympic road race.

Everything joins on `riders.id`. The PCS slug is `riders.external_id` with the
`pcs:` prefix stripped (`pcs:wout-van-aert` → `wout-van-aert`).

## Getting at it

The whole reference set is 308 KB of JSON, 29 KB gzipped. Send it in **one
payload and join on the client**. Do not build a per-rider endpoint and fire
190 requests.

Suggested: `GET /api/riders/reference` returning `{riders: [...], races: [...]}`,
where each rider carries their profile, current season, both ranking snapshots
and a flat list of results. One query per table, zipped in Python:

```python
from sqlalchemy.orm import selectinload

riders = db.scalars(
    select(Rider)
    .options(
        selectinload(Rider.profile),
        selectinload(Rider.seasons),
        selectinload(Rider.rankings),
        selectinload(Rider.race_results).selectinload(RaceResult.edition).selectinload(RaceEdition.race),
    )
    .order_by(Rider.last_name)
).unique().all()
```

The relationships already exist on the models (`Rider.profile`, `.seasons`,
`.rankings`, `.race_results`), so no manual joins are needed.

**Alternative:** `exports/pcs/rider-race-results.json` is the same race data,
already pivoted as `{rider: {race: {year: result}}}`. Serving it as a static file
next to `app.js` needs no backend at all and survives the API being down — but it
goes stale unless the fetchers are re-run and the file redeployed. Use the
endpoint if the data should track the database; use the file if you want the
prediction page to work offline.

## Reading the data correctly

These are the cases that will otherwise produce wrong or blank UI.

1. **A missing result row means the rider did not start.** 406 of the 1529 rows
   have `position = None` and a `status` of `DNF`, `DNS` or `OTL` — those riders
   *did* start. "Did not start" and "abandoned" must look different; a blank cell
   for both is the easy mistake. Render `position ?? status`.
2. **27 of 190 riders have no tracked result at all.** Mostly riders from smaller
   federations. Their race grid is legitimately empty — show "no tracked starts",
   not a spinner or a zero.
3. **Two editions have no classification.** The 2026 Worlds and 2026 Il Lombardia
   have not been ridden. They carry the reason in `race_editions.note`
   (`"not ridden yet"`) and zero results. Keep the column, mark it as upcoming.
4. **Ranking rows are sparse.** 186 riders have a `2026-09-22` row, 184 have
   `2025-12-30`. A rider outside the ranking that week simply has no row — treat
   a missing row as "unranked", never as rank 0.
5. **`event_riders.uci_rank` uses `999999` as the unranked sentinel**, but
   `rider_rankings.uci_rank` uses `NULL`. Two different conventions in the same
   app; `app.js` already handles the sentinel in `rankingLabel()`.
6. **7 riders have an empty `team`.** No trade team registered on PCS. Fall back
   to the nation, or omit the line.
7. **Two spellings of the same name.** `riders.first_name/last_name` is the
   startlist spelling ("Mathieu Van Der Poel"); `rider_profiles.pcs_name` is PCS's
   ("Mathieu van der Poel"). They differ for 5 riders. Pick one and be consistent
   — the startlist spelling is what the prediction UI already shows.
8. **`rider_seasons` is sparse too.** A rider only has rows for seasons he raced.
   Missing season ≠ zero wins displayed as a gap in a chart; fill the gaps
   explicitly if you plot a timeline.

## Transformations worth building

Ordered roughly by payoff for a prediction game.

**Ranking momentum.** Subtract the two `rider_rankings` rows: `uci_points` now
minus then, and the same as a percentage. This is the single most legible "is he
flying or fading" number in the dataset — Evenepoel is +92%, Van der Poel -27%
over the same nine months. Show it as an arrow next to the UCI rank.

**Head-to-head.** Two riders who appear in the same `race_edition_id` were on the
same road on the same day. Count how often each finished ahead across all shared
editions. This is the most interesting thing the schema enables and no other
source in the app can answer it — good for a compare-two-riders panel.

**Parcours affinity.** Split the tracked races by terrain and average each
rider's finishes per group. A rider's record on Worlds-like terrain predicts far
more than his overall average; weight recent years higher.

```js
const HILLY = ["world-championship", "il-lombardia", "liege-bastogne-liege",
               "strade-bianche", "san-sebastian", "gp-montreal", "olympic-games"];
const FLAT  = ["paris-roubaix", "ronde-van-vlaanderen", "milano-sanremo", "gp-quebec"];
```

**Consistency vs. ceiling.** Two numbers from the same rows: best-ever finish
(the ceiling) and share of starts finishing top-10 (the floor). Plot them against
each other and the field separates into stars, metronomes and lottery tickets.
Decide up front whether a DNF counts as a bad result or is excluded — it changes
the picture, so make the choice visible in the UI.

**Punching above the ranking.** Compare `uci_rank` with best tracked finish.
Riders whose one-day results beat their stage-race-inflated ranking are exactly
the value picks a scoring system with a difficulty bonus rewards.

**Career arc sparkline.** `rider_seasons` goes back to 2006. Points per season as
a 40px sparkline on the rider card shows a trajectory instantly — rising, peaked,
declining — which a single age number cannot.

**Workload.** `racedays` in the current season against `wins`. A rider with 70
race days by late September is deep into his season; one with 30 may be fresh or
may have been injured. Pair it with `age` for a freshness read.

**Worlds experience.** Count each rider's `world-championship` starts and best
finish. A separate, prominent stat — it is the race being predicted.

## Refreshing

```powershell
python -m scripts.fetch_pcs_riders          # profiles, season totals
python -m scripts.fetch_pcs_races           # race results
python -m scripts.load_pcs_data             # exports -> database
```

All three are idempotent and cache their HTTP requests, so re-running is cheap.
`load_pcs_data --dry-run` reports what would change without writing. Full detail
in the README under "ProCyclingStats data fetchers".

A running API keeps the reference set in memory for five minutes and tells
browsers to keep it for five more, so after a loader run against a live server
the new data shows up within ten minutes, or at once after a restart. A deploy
restarts the server anyway.
