"""Load UCI ranking columns from a startlist CSV back into the local database.

Mirror image of export_startlist_csv.py: that script writes the startlist out,
this one reads the `uci_rank` / `uci_points` columns back in after they have
been filled from ProCyclingStats by fill_uci_points.py.

    python -m scripts.import_startlist_csv scripts/output.csv

Riders are matched on `pcs_rider_path` against `riders.external_id`, scoped to
one event, so the mock development event is never touched. Only the two UCI
columns are written -- names, nation and is_starter are left as they are.
"""

import argparse
import csv
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Event, EventRider, Rider

EVENT_SLUG = "road-worlds-2026"
DEFAULT_CSV = Path("scripts/output.csv")
# app/main.py orders the startlist by uci_rank and frontend/app.js renders this
# value as "UCI unranked", so unranked riders must keep the sentinel, not NULL.
UNRANKED = 999999


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read the CSV, tolerating a BOM and stray whitespace in the header."""
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise SystemExit(f"{path} is empty.")
        names = [(name or "").strip() for name in reader.fieldnames]
        missing = {"pcs_rider_path", "uci_rank", "uci_points"} - set(names)
        if missing:
            raise SystemExit(f"{path} is missing column(s): {', '.join(sorted(missing))}")
        return [dict(zip(names, row.values())) for row in reader]


def parse_rank(value: str) -> int:
    value = (value or "").strip()
    return int(value) if value else UNRANKED


def parse_points(value: str) -> float | None:
    value = (value or "").strip().replace(",", "")
    return float(value) if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path, nargs="?", default=DEFAULT_CSV)
    parser.add_argument("--event", default=EVENT_SLUG, help="Event slug to update")
    parser.add_argument(
        "--ranking-date",
        default=date.today().isoformat(),
        help="Ranking snapshot date recorded on the event (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would change, write nothing"
    )
    args = parser.parse_args()

    if not args.csv_path.exists():
        raise SystemExit(f"No such file: {args.csv_path}")
    rows = read_rows(args.csv_path)
    snapshot = datetime.fromisoformat(args.ranking_date)

    init_db()
    with SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.slug == args.event))
        if event is None:
            raise SystemExit(f"No event with slug {args.event!r} exists in the database.")

        # One lookup of the event's riders, keyed by the CSV's join column.
        by_slug = {
            rider.external_id.removeprefix("pcs:"): event_rider
            for rider, event_rider in session.execute(
                select(Rider, EventRider)
                .join(EventRider, EventRider.rider_id == Rider.id)
                .where(EventRider.event_id == event.id)
            ).all()
        }

        changed, unchanged, ranked, unknown, seen = 0, 0, 0, [], set()
        for row in rows:
            slug = (row.get("pcs_rider_path") or "").strip()
            if not slug:
                continue
            if slug in seen:
                print(f"  duplicate row for {slug}, using the first")
                continue
            seen.add(slug)

            event_rider = by_slug.get(slug)
            if event_rider is None:
                unknown.append(slug)
                continue

            rank, points = parse_rank(row["uci_rank"]), parse_points(row["uci_points"])
            if rank != UNRANKED:
                ranked += 1
            if (event_rider.uci_rank, event_rider.uci_points) == (rank, points):
                unchanged += 1
                continue
            event_rider.uci_rank, event_rider.uci_points = rank, points
            changed += 1

        missing_from_csv = sorted(set(by_slug) - seen)
        # Read before commit: committing expires the instance, and it is
        # detached once the session closes.
        event_name = event.name

        if not args.dry_run:
            event.source_name = "PCS preliminary startlist + PCS UCI World Ranking"
            event.source_updated_at = snapshot
            session.commit()

    verb = "would update" if args.dry_run else "updated"
    print(f"{event_name}")
    print(f"  {verb} {changed} rider(s), {unchanged} already current")
    print(f"  {ranked}/{len(seen)} riders carry a UCI rank; {len(seen) - ranked} unranked")
    if unknown:
        print(f"  {len(unknown)} CSV row(s) not on this event's startlist:")
        for slug in unknown:
            print(f"    {slug}")
    if missing_from_csv:
        print(f"  {len(missing_from_csv)} startlist rider(s) absent from the CSV:")
        for slug in missing_from_csv:
            print(f"    {slug}")
    if args.dry_run:
        print("  (dry run -- nothing written)")


if __name__ == "__main__":
    main()
