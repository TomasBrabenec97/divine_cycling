#!/usr/bin/env python3
"""Load the ProCyclingStats exports into the local database.

Reads everything the fetchers wrote to `exports/pcs/` and stores it as
read-only reference data the frontend can query: rider profiles and their
season totals, UCI ranking snapshots, and the results of the tracked one-day
races.

    python -m scripts.load_pcs_data
    python -m scripts.load_pcs_data --dry-run
    python -m scripts.load_pcs_data --input-dir exports/pcs

Riders are matched on `pcs_rider_path` against `riders.external_id`, so run
`python -m scripts.load_pcs_data` after the startlist itself is loaded. Rows
for riders the database does not know are reported and skipped, never invented.

This deliberately leaves `event_riders` alone. The live UCI rank and points the
game scores on stay the job of `scripts/import_startlist_csv.py`, which can be
pointed at the same enriched CSV:

    python -m scripts.import_startlist_csv exports/pcs/startlist-enriched.csv \
        --ranking-date 2026-09-22

Re-running is safe: every row is matched on its natural key and updated in
place, so the tables converge on whatever the exports currently say.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.models import (
    Race,
    RaceEdition,
    RaceResult,
    Rider,
    RiderProfile,
    RiderRanking,
    RiderSeason,
)

INPUT_DIR = Path("exports/pcs")
STARTLIST_CSV = Path("exports/pcs/startlist-enriched.csv")

RIDERS_FILE = "riders.csv"
SEASONS_FILE = "rider-seasons.csv"
EDITIONS_FILE = "race-editions.csv"
RESULTS_FILE = "race-results.csv"


class Report:
    """Counts per table, so a run says exactly what it did."""

    def __init__(self) -> None:
        self.counts: dict[str, Counter] = {}
        self.skipped: dict[str, list[str]] = {}

    def count(self, table: str, outcome: str) -> None:
        self.counts.setdefault(table, Counter())[outcome] += 1

    def skip(self, table: str, reason: str) -> None:
        self.skipped.setdefault(table, []).append(reason)

    def print(self, dry_run: bool) -> None:
        verb = "would write" if dry_run else "wrote"
        for table, counts in self.counts.items():
            parts = [
                f"{counts[key]} {key}" for key in ("created", "updated", "unchanged") if counts[key]
            ]
            print(f"  {table:<16} {verb} {', '.join(parts) or 'nothing'}")
        for table, reasons in self.skipped.items():
            unique = sorted(set(reasons))
            print(
                f"  {table:<16} skipped {len(reasons)} row(s) for {len(unique)} unknown rider(s):"
            )
            for slug in unique[:10]:
                print(f"    {slug}")
            if len(unique) > 10:
                print(f"    ... and {len(unique) - 10} more")
        if dry_run:
            print("  (dry run -- nothing written)")


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"No such file: {path}. Run the PCS fetchers first.")
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise SystemExit(f"{path} is empty.")
        return [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in reader
        ]


def as_int(value: str) -> int | None:
    value = value.replace(",", "")
    try:
        return int(float(value))
    except ValueError:
        return None


def as_float(value: str) -> float | None:
    value = value.replace(",", "")
    try:
        return float(value)
    except ValueError:
        return None


def as_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def apply(
    record: object, values: dict[str, object], table: str, report: Report, fresh: bool
) -> None:
    """Write `values` onto `record` and record whether anything moved."""
    changed = any(getattr(record, key) != value for key, value in values.items())
    for key, value in values.items():
        setattr(record, key, value)
    report.count(table, "created" if fresh else "updated" if changed else "unchanged")


def rider_ids(session: Session) -> dict[str, int]:
    return {
        rider.external_id.removeprefix("pcs:"): rider.id for rider in session.scalars(select(Rider))
    }


def load_profiles(session: Session, riders: dict[str, int], path: Path, report: Report) -> None:
    existing = {profile.rider_id: profile for profile in session.scalars(select(RiderProfile))}
    for row in read_rows(path):
        rider_id = riders.get(row["pcs_rider_path"])
        if rider_id is None:
            report.skip("rider_profiles", row["pcs_rider_path"])
            continue

        profile = existing.get(rider_id)
        fresh = profile is None
        if fresh:
            profile = RiderProfile(rider_id=rider_id)
            session.add(profile)
        apply(
            profile,
            {
                "pcs_name": row.get("pcs_name", ""),
                "team": row.get("team", ""),
                "age": as_int(row.get("age", "")),
                "date_of_birth": as_date(row.get("date_of_birth", "")),
                "wins_total": as_int(row.get("wins_total", "")),
                "profile_url": row.get("profile_url", ""),
                "source_updated_at": _fetched_at(row.get("fetched_at", "")),
            },
            "rider_profiles",
            report,
            fresh,
        )


def load_seasons(session: Session, riders: dict[str, int], path: Path, report: Report) -> None:
    existing = {
        (season.rider_id, season.season): season for season in session.scalars(select(RiderSeason))
    }
    for row in read_rows(path):
        rider_id = riders.get(row["pcs_rider_path"])
        year = as_int(row.get("season", ""))
        if rider_id is None:
            report.skip("rider_seasons", row["pcs_rider_path"])
            continue
        if year is None:
            continue

        season = existing.get((rider_id, year))
        fresh = season is None
        if fresh:
            season = RiderSeason(rider_id=rider_id, season=year)
            session.add(season)
        apply(
            season,
            {
                "pcs_points": as_float(row.get("pcs_points", "")),
                "racedays": as_int(row.get("racedays", "")) or 0,
                "kms": as_int(row.get("kms", "")) or 0,
                "wins": as_int(row.get("wins", "")) or 0,
                "top3s": as_int(row.get("top3s", "")) or 0,
                "top10s": as_int(row.get("top10s", "")) or 0,
            },
            "rider_seasons",
            report,
            fresh,
        )


def load_rankings(session: Session, riders: dict[str, int], path: Path, report: Report) -> None:
    """Store both snapshots of the enriched startlist as ranking rows.

    The movement columns are not stored: they are the difference between these
    two rows, and keeping only the snapshots lets any pair of dates be compared.
    """
    existing = {
        (ranking.rider_id, ranking.ranking_date): ranking
        for ranking in session.scalars(select(RiderRanking))
    }
    for row in read_rows(path):
        rider_id = riders.get(row["pcs_rider_path"])
        if rider_id is None:
            report.skip("rider_rankings", row["pcs_rider_path"])
            continue

        snapshots = [
            (
                as_date(row.get("uci_date", "")),
                row.get("uci_rank", ""),
                row.get("uci_points", ""),
                row.get("team", ""),
            ),
            (
                as_date(row.get("uci_compare_date", "")),
                row.get("uci_rank_prev", ""),
                row.get("uci_points_prev", ""),
                "",
            ),
        ]
        for when, rank, points, team in snapshots:
            # A rider outside a snapshot's ranking has no row for that date.
            if when is None or (not rank and not points):
                continue
            ranking = existing.get((rider_id, when))
            fresh = ranking is None
            if fresh:
                ranking = RiderRanking(rider_id=rider_id, ranking_date=when)
                session.add(ranking)
            apply(
                ranking,
                {"uci_rank": as_int(rank), "uci_points": as_float(points), "team": team},
                "rider_rankings",
                report,
                fresh,
            )


def load_races(session: Session, riders: dict[str, int], input_dir: Path, report: Report) -> None:
    rows = read_rows(input_dir / EDITIONS_FILE)

    # The catalogue first: one Race per key, however many editions mention it.
    races = {race.key: race for race in session.scalars(select(Race))}
    for key in dict.fromkeys(row["race_key"] for row in rows if row["race_key"]):
        row = next(row for row in rows if row["race_key"] == key)
        race = races.get(key)
        fresh = race is None
        if fresh:
            race = Race(key=key)
            races[key] = race
            session.add(race)
        apply(
            race,
            {"label": row.get("race_label", ""), "pcs_slug": _slug_from_url(row.get("url", ""))},
            "races",
            report,
            fresh,
        )
    session.flush()  # the editions below need race.id

    stored = {
        (edition.race_id, edition.year): edition for edition in session.scalars(select(RaceEdition))
    }
    editions: dict[tuple[str, int], RaceEdition] = {}
    for row in rows:
        race, year = races.get(row["race_key"]), as_int(row.get("year", ""))
        if race is None or year is None:
            continue

        edition = stored.get((race.id, year))
        fresh = edition is None
        if fresh:
            edition = RaceEdition(race_id=race.id, year=year)
            session.add(edition)
        editions[(race.key, year)] = edition
        apply(
            edition,
            {
                "name": row.get("race_name", ""),
                "race_date": as_date(row.get("race_date", "")),
                "classification": row.get("classification", ""),
                "category": row.get("category", ""),
                "distance": row.get("distance", ""),
                "note": row.get("note", ""),
                "url": row.get("url", ""),
            },
            "race_editions",
            report,
            fresh,
        )
    session.flush()  # the results below need race_edition.id

    results = {
        (result.race_edition_id, result.rider_id): result
        for result in session.scalars(select(RaceResult))
    }
    for row in read_rows(input_dir / RESULTS_FILE):
        rider_id = riders.get(row["pcs_rider_path"])
        edition = editions.get((row["race_key"], as_int(row.get("year", ""))))
        if rider_id is None:
            report.skip("race_results", row["pcs_rider_path"])
            continue
        if edition is None:
            continue

        result = results.get((edition.id, rider_id))
        fresh = result is None
        if fresh:
            result = RaceResult(race_edition_id=edition.id, rider_id=rider_id)
            session.add(result)
        apply(
            result,
            {
                "position": as_int(row.get("rank", "")),
                "status": row.get("status", ""),
                "team": row.get("team", ""),
                "age": as_int(row.get("age", "")),
                "uci_points": as_float(row.get("uci_points", "")),
                "pcs_points": as_float(row.get("pcs_points", "")),
                "time": row.get("time", ""),
            },
            "race_results",
            report,
            fresh,
        )


def _slug_from_url(url: str) -> str:
    return url.split("/race/", 1)[1].split("/")[0] if "/race/" in url else ""


def _fetched_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument(
        "--startlist",
        type=Path,
        default=STARTLIST_CSV,
        help="enriched startlist CSV holding the two UCI ranking snapshots",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()
    report = Report()

    with SessionLocal() as session:
        riders = rider_ids(session)
        if not riders:
            raise SystemExit(
                "The database holds no riders. Load the startlist first:\n"
                "  python -m scripts.load_road_worlds_2026_csv "
                "exports/road-worlds-2026-startlist.csv"
            )

        load_profiles(session, riders, args.input_dir / RIDERS_FILE, report)
        load_seasons(session, riders, args.input_dir / SEASONS_FILE, report)
        if args.startlist.exists():
            load_rankings(session, riders, args.startlist, report)
        else:
            print(f"  no {args.startlist}, skipping the UCI ranking snapshots")
        load_races(session, riders, args.input_dir, report)

        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    print(f"PCS reference data from {args.input_dir}")
    report.print(args.dry_run)


if __name__ == "__main__":
    main()
