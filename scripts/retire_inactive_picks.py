"""Clear picks that point at a rider who has since left the startlist.

When `load_road_worlds_2026_csv` flips a rider's `is_starter` to False (a
late withdrawal), that rider's `Rider`/`EventRider` rows are kept -- only
flagged -- so any pick already made against them survives untouched at the
database level. This script is the deliberate, separate step that clears
just the specific slot(s) that picked a since-withdrawn rider, leaving every
other position, wildcard, and template picked by that player exactly as it
was.

    python -m scripts.retire_inactive_picks --local
    python -m scripts.retire_inactive_picks --local --dry-run
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventRider, Player, PredictionItem, PredictionTemplate, PredictionWildcard, Rider

DEFAULT_EVENT_SLUG = "road-worlds-2026"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local", action="store_true", required=True, help="operate on the local database")
    parser.add_argument("--event-slug", default=DEFAULT_EVENT_SLUG)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, but do not write")
    return parser.parse_args()


def inactive_rider_ids(session: Session, event_id: int) -> set[int]:
    return set(
        session.scalars(
            select(EventRider.rider_id).where(
                EventRider.event_id == event_id, EventRider.is_starter.is_(False)
            )
        ).all()
    )


def clean_templates(session: Session, event_id: int, inactive_ids: set[int], dry_run: bool) -> int:
    touched = 0
    templates = session.scalars(select(PredictionTemplate).where(PredictionTemplate.event_id == event_id)).all()
    for template in templates:
        payload = json.loads(template.picks_json)
        selections = payload.get("selections", [])
        wildcards = payload.get("wildcards", [])
        kept_selections = [item for item in selections if item.get("rider_id") not in inactive_ids]
        kept_wildcards = [rider_id for rider_id in wildcards if rider_id not in inactive_ids]
        if len(kept_selections) != len(selections) or len(kept_wildcards) != len(wildcards):
            touched += 1
            print(
                f"  template {template.id!r} {template.name!r} (player {template.player_id}): "
                f"{len(selections) - len(kept_selections)} selection(s), "
                f"{len(wildcards) - len(kept_wildcards)} wildcard(s) cleared"
            )
            if not dry_run:
                payload["selections"] = kept_selections
                payload["wildcards"] = kept_wildcards
                template.picks_json = json.dumps(payload)
    return touched


def main() -> int:
    args = parse_args()
    from app.db import SessionLocal

    with SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.slug == args.event_slug))
        if event is None:
            raise SystemExit(f"No event with slug {args.event_slug!r}")

        inactive_ids = inactive_rider_ids(session, event.id)
        if not inactive_ids:
            print("No inactive (withdrawn) riders on this event's startlist. Nothing to do.")
            return 0

        withdrawn = session.scalars(select(Rider).where(Rider.id.in_(inactive_ids))).all()
        print(f"{len(withdrawn)} withdrawn rider(s): " + ", ".join(f"{r.first_name} {r.last_name}" for r in withdrawn))

        items = session.scalars(
            select(PredictionItem)
            .join(PredictionItem.prediction)
            .where(PredictionItem.rider_id.in_(inactive_ids))
        ).all()
        wildcards = session.scalars(
            select(PredictionWildcard)
            .join(PredictionWildcard.prediction)
            .where(PredictionWildcard.rider_id.in_(inactive_ids))
        ).all()

        players_by_id = {player.id: player for player in session.scalars(select(Player)).all()}
        riders_by_id = {rider.id: rider for rider in withdrawn}

        print(f"\n{len(items)} Top 10 slot(s) to clear:")
        for item in items:
            player = players_by_id.get(item.prediction.player_id)
            rider = riders_by_id.get(item.rider_id)
            print(f"  {player.username if player else item.prediction.player_id}: position {item.position} ({rider.first_name} {rider.last_name if rider else item.rider_id})")

        print(f"\n{len(wildcards)} wildcard slot(s) to clear:")
        for wildcard in wildcards:
            player = players_by_id.get(wildcard.prediction.player_id)
            rider = riders_by_id.get(wildcard.rider_id)
            print(f"  {player.username if player else wildcard.prediction.player_id}: wildcard slot {wildcard.slot} ({rider.first_name} {rider.last_name if rider else wildcard.rider_id})")

        print("\nTemplates:")
        touched_templates = clean_templates(session, event.id, inactive_ids, args.dry_run)
        if not touched_templates:
            print("  none affected")

        if args.dry_run:
            print("\n--dry-run: no changes written.")
            return 0

        for item in items:
            session.delete(item)
        for wildcard in wildcards:
            session.delete(wildcard)
        session.commit()
        print(f"\nCleared {len(items)} Top 10 slot(s), {len(wildcards)} wildcard slot(s), {touched_templates} template(s). Every other pick is untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
