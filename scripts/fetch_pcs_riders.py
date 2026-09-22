#!/usr/bin/env python3
"""Visit every startlist rider's ProCyclingStats profile and record the basics.

For each rider in the startlist CSV this reads the PCS rider page (age, date of
birth, trade team) and the season-statistics table (wins in the target season
and over the whole career), then writes them out keyed on the rider slug.

    python -m scripts.fetch_pcs_riders
    python -m scripts.fetch_pcs_riders --season 2026
    python -m scripts.fetch_pcs_riders --riders tadej-pogacar remco-evenepoel

Outputs:

    exports/pcs/riders.csv         one row per rider: age, wins, season totals
    exports/pcs/rider-seasons.csv  long form, one row per rider and season

Two pages are fetched per rider, so a cold run over a 190-rider startlist takes
roughly fifteen minutes at the default delay. Pages are cached, so a second run
costs nothing; pass --refresh when you want the numbers re-read.

Run from your own machine. PCS disallows automated access in robots.txt, so
the delay between requests is deliberate -- please leave it in.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from scripts.pcs.cli import (
    OUTPUT_DIR,
    STARTLIST_CSV,
    add_client_arguments,
    client_from,
    read_startlist,
    write_csv,
)
from scripts.pcs.riders import RiderProfile, fetch_rider

SEASON = 2026

RIDER_COLUMNS = [
    "pcs_rider_path",
    "first_name",
    "last_name",
    "country_code",
    "pcs_name",
    "team",
    "age",
    "date_of_birth",
    "wins_total",
    "wins_season",
    "racedays_season",
    "top3s_season",
    "top10s_season",
    "pcs_points_season",
    "season",
    "profile_url",
    "fetched_at",
]

SEASON_COLUMNS = [
    "pcs_rider_path",
    "season",
    "pcs_points",
    "racedays",
    "kms",
    "wins",
    "top3s",
    "top10s",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("startlist_csv", type=Path, nargs="?", default=STARTLIST_CSV)
    parser.add_argument(
        "--season", type=int, default=SEASON, help="season the win count is for (default: %(default)s)"
    )
    parser.add_argument("--riders", nargs="+", metavar="SLUG", help="only these rider slugs")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    add_client_arguments(parser)
    return parser.parse_args()


def rider_row(
    entry: dict[str, str], profile: RiderProfile | None, season: int, url: str, now: str
) -> dict[str, object]:
    row: dict[str, object] = {
        "pcs_rider_path": entry["pcs_rider_path"],
        "first_name": entry.get("first_name", ""),
        "last_name": entry.get("last_name", ""),
        "country_code": entry.get("country_code", ""),
        "season": season,
        "profile_url": url,
        "fetched_at": now,
    }
    if profile is None:
        return {**{column: "" for column in RIDER_COLUMNS}, **row}

    stats = profile.season(season)
    row.update(
        {
            "pcs_name": profile.name,
            "team": profile.team,
            "age": profile.age if profile.age is not None else "",
            "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else "",
            "wins_total": profile.wins_total if profile.wins_total is not None else "",
            "wins_season": profile.wins_in(season),
            "racedays_season": (stats.racedays or 0) if stats else 0,
            "top3s_season": (stats.top3s or 0) if stats else 0,
            "top10s_season": (stats.top10s or 0) if stats else 0,
            "pcs_points_season": (stats.points or 0) if stats else 0,
        }
    )
    return row


def main() -> None:
    args = parse_args()
    riders = read_startlist(args.startlist_csv)
    if args.riders:
        picked = set(args.riders)
        riders = [row for row in riders if row["pcs_rider_path"] in picked]
        unknown = picked - {row["pcs_rider_path"] for row in riders}
        if unknown:
            raise SystemExit(f"Not on the startlist: {', '.join(sorted(unknown))}")

    client = client_from(args)
    now = datetime.now().isoformat(timespec="seconds")
    rider_rows: list[dict[str, object]] = []
    season_rows: list[dict[str, object]] = []
    failed: list[str] = []

    for index, entry in enumerate(riders, start=1):
        slug = entry["pcs_rider_path"]
        print(f"[{index:>3}/{len(riders)}] {slug}", file=sys.stderr)
        profile = fetch_rider(client, slug)
        if profile is None:
            failed.append(slug)
        else:
            for stats in profile.seasons:
                if stats.season is None:  # the career-total row, kept as wins_total
                    continue
                season_rows.append(
                    {
                        "pcs_rider_path": slug,
                        "season": stats.season,
                        "pcs_points": stats.points if stats.points is not None else "",
                        "racedays": stats.racedays or 0,
                        "kms": stats.kms or 0,
                        "wins": stats.wins or 0,
                        "top3s": stats.top3s or 0,
                        "top10s": stats.top10s or 0,
                    }
                )
        rider_rows.append(
            rider_row(entry, profile, args.season, client.url(f"rider/{slug}"), now)
        )

    riders_path = args.output_dir / "riders.csv"
    seasons_path = args.output_dir / "rider-seasons.csv"
    write_csv(riders_path, RIDER_COLUMNS, rider_rows)
    write_csv(seasons_path, SEASON_COLUMNS, season_rows)

    with_age = sum(1 for row in rider_rows if row["age"] != "")
    with_wins = sum(1 for row in rider_rows if row["wins_total"] != "")
    print(f"\n{client.summary()}", file=sys.stderr)
    print(
        f"{len(rider_rows)} riders -> {riders_path} "
        f"({with_age} with an age, {with_wins} with a win count)",
        file=sys.stderr,
    )
    print(f"{len(season_rows)} season rows -> {seasons_path}", file=sys.stderr)
    if failed:
        print(f"\n{len(failed)} profiles PCS did not serve:", file=sys.stderr)
        for slug in failed:
            print(f"  {slug}", file=sys.stderr)


if __name__ == "__main__":
    main()
