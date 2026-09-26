"""Create or refresh the 2026 Road Worlds startlist from its CSV export.

Unlike ``import_startlist_csv.py``, this command can be used against an empty
database: it creates the event, riders and their event entries in one pass.

    python -m scripts.load_road_worlds_2026_csv exports/road-worlds-2026-startlist.csv
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Event, EventRider, Rider


EVENT_SLUG = "road-worlds-2026"
DEFAULT_CSV = Path("exports/road-worlds-2026-startlist.csv")
UNRANKED = 999999


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise SystemExit(f"{path} is empty.")
        fieldnames = {(name or "").strip() for name in reader.fieldnames}
        required = {
            "first_name",
            "last_name",
            "country_code",
            "uci_rank",
            "uci_points",
            "pcs_rider_path",
            "is_starter",
        }
        missing = required - fieldnames
        if missing:
            raise SystemExit(f"{path} is missing column(s): {', '.join(sorted(missing))}")
        return [{(key or "").strip(): (value or "").strip() for key, value in row.items()} for row in reader]


def as_rank(value: str) -> int:
    return int(value) if value else UNRANKED


def as_points(value: str) -> float | None:
    return float(value.replace(",", "")) if value else None


def as_bool(value: str) -> bool:
    return value.casefold() in {"1", "true", "yes", "y"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path, nargs="?", default=DEFAULT_CSV)
    args = parser.parse_args()
    if not args.csv_path.exists():
        raise SystemExit(f"No such file: {args.csv_path}")

    rows = read_rows(args.csv_path)
    if not rows:
        raise SystemExit(f"{args.csv_path} has no riders.")
    slugs = [row["pcs_rider_path"] for row in rows]
    if any(not slug for slug in slugs) or len(set(slugs)) != len(slugs):
        raise SystemExit("Every row needs a unique pcs_rider_path.")

    init_db()
    created, updated = 0, 0
    with SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.slug == EVENT_SLUG))
        if event is None:
            event = Event(
                slug=EVENT_SLUG,
                name="2026 Road World Championship — Men Elite Road Race",
                starts_at=datetime(2026, 9, 27, 13, 0),
                prediction_deadline=datetime(2026, 9, 27, 12, 30),
                # 21:40 CEST, the estimated finish time for the elite men's race.
                results_expected_at=datetime(2026, 9, 27, 19, 40),
                status="open",
            )
            session.add(event)
            session.flush()
        # A row created before `results_expected_at` existed would otherwise be
        # stuck on NULL forever, since only new rows set it above.
        if event.results_expected_at is None:
            event.results_expected_at = datetime(2026, 9, 27, 19, 40)
        event.source_name = "Local 2026 Road Worlds CSV export"
        event.source_updated_at = datetime.now()

        for row in rows:
            external_id = f"pcs:{row['pcs_rider_path']}"
            rider = session.scalar(select(Rider).where(Rider.external_id == external_id))
            if rider is None:
                rider = Rider(
                    external_id=external_id,
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    nation=row["country_code"].upper(),
                )
                session.add(rider)
                session.flush()
            else:
                rider.first_name = row["first_name"]
                rider.last_name = row["last_name"]
                rider.nation = row["country_code"].upper()

            event_rider = session.scalar(
                select(EventRider).where(
                    EventRider.event_id == event.id, EventRider.rider_id == rider.id
                )
            )
            values = {
                "uci_rank": as_rank(row["uci_rank"]),
                "uci_points": as_points(row["uci_points"]),
                "is_starter": as_bool(row["is_starter"]),
            }
            if event_rider is None:
                session.add(EventRider(event_id=event.id, rider_id=rider.id, **values))
                created += 1
            else:
                for key, value in values.items():
                    setattr(event_rider, key, value)
                updated += 1
        session.commit()

    print(f"Loaded {len(rows)} riders into {EVENT_SLUG}: {created} created, {updated} refreshed.")


if __name__ == "__main__":
    main()
