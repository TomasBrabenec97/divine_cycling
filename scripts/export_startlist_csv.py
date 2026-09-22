"""Export the currently imported Road Worlds startlist as a CSV file."""

import csv
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Event, EventRider, Rider


EVENT_SLUG = "road-worlds-2026"
OUTPUT_PATH = Path("exports/road-worlds-2026-startlist.csv")


def main() -> None:
    init_db()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.slug == EVENT_SLUG))
        if event is None:
            raise SystemExit(f"No event with slug {EVENT_SLUG!r} exists in the database.")

        rows = session.execute(
            select(Rider, EventRider)
            .join(EventRider, EventRider.rider_id == Rider.id)
            .where(EventRider.event_id == event.id)
            .order_by(Rider.nation, Rider.last_name, Rider.first_name)
        ).all()

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "first_name",
                "last_name",
                "country_code",
                "uci_rank",
                "uci_points",
                "pcs_rider_path",
                "is_starter",
            ],
        )
        writer.writeheader()
        for rider, event_rider in rows:
            writer.writerow(
                {
                    "first_name": rider.first_name,
                    "last_name": rider.last_name,
                    "country_code": rider.nation,
                    "uci_rank": "" if event_rider.uci_rank == 999999 else event_rider.uci_rank,
                    "uci_points": event_rider.uci_points or "",
                    "pcs_rider_path": rider.external_id.removeprefix("pcs:"),
                    "is_starter": event_rider.is_starter,
                }
            )

    print(f"Exported {len(rows)} riders to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
