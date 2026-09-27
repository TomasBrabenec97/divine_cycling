#!/usr/bin/env python3
"""Fetch a race result from ProCyclingStats and publish it to the game.

    python -m scripts.upload_results --local
    python -m scripts.upload_results --local --year 2025 --dry-run
    python -m scripts.upload_results --prod --admin-key "$ADMIN_API_KEY"

What it does, in order:

1. Fetches the classification for `--race`/`--year` from PCS (cached on disk
   like every other `scripts.pcs` fetcher -- see `scripts/pcs/client.py`).
2. Reads the target event's startlist straight from the database (read-only)
   and matches PCS finishers onto it by `riders.external_id`, so the payload
   only ever contains riders our event actually knows about.
3. Sends the matched, ranked results to `POST /api/admin/events/{id}/results`,
   the same endpoint the admin page uses, so scoring and the leaderboard are
   computed exactly the way a manual entry would.

`--local` and `--prod` each pick a database (for step 2) and a base URL (for
step 3) together, so the read and the write always land on the same side:

* `--local` reads the database `DATABASE_URL` (or app default) points at, and
  posts to `http://127.0.0.1:8000` -- run `uvicorn app.main:app` first.
* `--prod` reads `DATABASE_URL_UNPOOLED` out of `.env.local` (read-only; never
  printed) and posts to the live Render API. It needs the admin key Render
  generated for `ADMIN_API_KEY` -- pass `--admin-key` or set it in the
  environment; it is never read from `.env.local`.

`--dry-run` fetches and matches but never posts, so it is safe to run against
`--prod` to sanity-check a match before publishing anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Event, EventRider, Rider
from scripts.pcs.cli import add_client_arguments, client_from
from scripts.pcs.races import RACES_BY_KEY, fetch_edition

ROOT = Path(__file__).resolve().parent.parent
LOCAL_BASE_URL = "http://127.0.0.1:8000"
PROD_BASE_URL = "https://divine-cycling-api.onrender.com"
DEFAULT_EVENT_SLUG = "road-worlds-2026"
DEFAULT_RACE = "world-championship"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--local", action="store_true", help="read and publish locally")
    target.add_argument("--prod", action="store_true", help="read and publish to production")
    parser.add_argument("--event-slug", default=DEFAULT_EVENT_SLUG, help="events.slug to publish onto")
    parser.add_argument("--race", default=DEFAULT_RACE, choices=sorted(RACES_BY_KEY), help="PCS race key")
    parser.add_argument("--year", type=int, help="race season (default: the event's own year)")
    parser.add_argument("--admin-key", default=os.environ.get("ADMIN_API_KEY", ""), help="X-Admin-Key (--prod only; default: $ADMIN_API_KEY)")
    parser.add_argument("--base-url", help="override the base URL this target posts to")
    parser.add_argument("--dry-run", action="store_true", help="fetch and match, but do not publish")
    add_client_arguments(parser)
    return parser.parse_args()


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _pg_url(url: str) -> str:
    # Match app/db.py's driver selection so this script and the app agree on
    # how to talk to the same hosted Postgres.
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def make_session(is_prod: bool) -> Session:
    if is_prod:
        env = _read_env_file(ROOT / ".env.local")
        database_url = env.get("DATABASE_URL_UNPOOLED")
        if not database_url:
            raise SystemExit("DATABASE_URL_UNPOOLED is not set in .env.local")
        engine = create_engine(_pg_url(database_url), pool_pre_ping=True)
        return Session(bind=engine)

    from app.db import SessionLocal  # local: reuse the app's own configured engine

    return SessionLocal()


def eligible_riders(session: Session, event_slug: str) -> tuple[Event, dict[str, tuple[int, str]]]:
    """`{pcs_rider_path: (rider_id, name)}` for everyone on the event's startlist."""
    event = session.scalar(select(Event).where(Event.slug == event_slug))
    if event is None:
        raise SystemExit(f"No event with slug {event_slug!r}")
    rows = session.execute(
        select(Rider.external_id, Rider.id, Rider.first_name, Rider.last_name)
        .join(EventRider, EventRider.rider_id == Rider.id)
        .where(EventRider.event_id == event.id)
    ).all()
    by_slug: dict[str, tuple[int, str]] = {}
    for external_id, rider_id, first_name, last_name in rows:
        if external_id and external_id.startswith("pcs:"):
            by_slug[external_id.removeprefix("pcs:")] = (rider_id, f"{first_name} {last_name}")
    return event, by_slug


def main() -> int:
    # Rider names carry accents PCS serves as UTF-8; Windows consoles default to
    # a narrower codepage that would otherwise crash on the first non-ASCII one.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    is_prod = args.prod
    base_url = (args.base_url or (PROD_BASE_URL if is_prod else LOCAL_BASE_URL)).rstrip("/")
    if is_prod and not args.dry_run and not args.admin_key:
        raise SystemExit("--prod needs --admin-key (or $ADMIN_API_KEY) to publish; add --dry-run to skip that")

    session = make_session(is_prod)
    try:
        event, riders_by_slug = eligible_riders(session, args.event_slug)
    finally:
        session.close()
    if not riders_by_slug:
        raise SystemExit(f"{args.event_slug!r} has no startlist to match against")
    year = args.year or event.starts_at.year

    race = RACES_BY_KEY[args.race]
    client = client_from(args)
    print(f"Fetching {race.label} {year} from PCS ({'production' if is_prod else 'local'} startlist, {len(riders_by_slug)} riders)…")
    edition = fetch_edition(client, race, year)
    print(f"  {client.summary()}")
    if edition.note:
        raise SystemExit(f"No result to publish: {edition.note} ({edition.url})")

    results: list[dict[str, int]] = []
    unmatched: list[str] = []
    for row in edition.rows:
        if row.rank is None or row.rank > 25:
            continue  # DNF/DNS/OTL/DSQ and anything outside the scored depth
        matched = riders_by_slug.get(row.slug)
        if matched is None:
            unmatched.append(f"{row.rank}. {row.name}")
            continue
        rider_id, _name = matched
        results.append({"position": row.rank, "rider_id": rider_id})

    print(f"Matched {len(results)} of the top {min(25, len([r for r in edition.rows if r.rank]))} PCS finishers to the startlist.")
    if unmatched:
        print(f"  not on the startlist, skipped: {', '.join(unmatched)}")
    if not results:
        raise SystemExit("No PCS finisher matched the startlist; nothing to publish.")

    if args.dry_run:
        print("--dry-run: not publishing. Payload would be:")
        for item in sorted(results, key=lambda r: r["position"]):
            print(f"  {item['position']:>2}. rider_id={item['rider_id']}")
        return 0

    headers = {"X-Admin-Key": args.admin_key} if args.admin_key else {}
    response = httpx.post(
        f"{base_url}/api/admin/events/{event.id}/results",
        json={"results": results},
        headers=headers,
        timeout=30,
    )
    if response.status_code >= 400:
        print(f"Publish failed: {response.status_code} {response.text}", file=sys.stderr)
        return 1
    board = response.json()
    print(f"Published to {base_url}. Leaderboard has {len(board.get('entries', []))} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
