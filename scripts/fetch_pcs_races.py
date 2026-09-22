#!/usr/bin/env python3
"""Fetch past results of the tracked one-day races and match them to riders.

For every configured race and season this reads the full classification from
ProCyclingStats and pivots it onto the startlist, so a rider's history across
the tracked races and years can be shown in one place.

    python -m scripts.fetch_pcs_races
    python -m scripts.fetch_pcs_races --years 2024 2025 2026
    python -m scripts.fetch_pcs_races --races world-championship il-lombardia
    python -m scripts.fetch_pcs_races --all-riders      # keep the whole field

Outputs:

    exports/pcs/race-editions.csv        one row per race and season, with the
                                         date, category and why a result is empty
    exports/pcs/race-results.csv         long form, one row per rider and edition
    exports/pcs/rider-race-results.json  {rider: {race: {year: result}}}

The Olympic road race is only fetched for 2024; every other race is fetched for
each --years season. Editions that have not been ridden yet (the 2026 Worlds,
for one) and editions that never existed are reported and skipped, not guessed.

Run from your own machine. PCS disallows automated access in robots.txt, so
the delay between requests is deliberate -- please leave it in.
"""

from __future__ import annotations

import argparse
import json
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
from scripts.pcs.races import DEFAULT_RACES, MEN_ELITE, RACES_BY_KEY, RaceEdition, fetch_edition

YEARS = (2023, 2024, 2025, 2026)

EDITION_COLUMNS = [
    "race_key",
    "race_label",
    "year",
    "race_name",
    "race_date",
    "classification",
    "category",
    "distance",
    "finishers",
    "startlist_riders",
    "note",
    "url",
]

RESULT_COLUMNS = [
    "pcs_rider_path",
    "rider_name",
    "race_key",
    "race_label",
    "year",
    "race_date",
    "rank",
    "status",
    "team",
    "age",
    "uci_points",
    "pcs_points",
    "time",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("startlist_csv", type=Path, nargs="?", default=STARTLIST_CSV)
    parser.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    parser.add_argument(
        "--races",
        nargs="+",
        metavar="KEY",
        choices=sorted(RACES_BY_KEY),
        help="race keys to fetch (default: all of them)",
    )
    parser.add_argument(
        "--all-riders",
        action="store_true",
        help="keep every finisher, not just the riders on the startlist",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    add_client_arguments(parser)
    return parser.parse_args()


def full_name(row: dict[str, str]) -> str:
    first = row.get("first_name", "")
    last = row.get("last_name", "")
    return f"{first} {last}".strip()


def main() -> None:
    args = parse_args()
    startlist = read_startlist(args.startlist_csv)
    names = {row["pcs_rider_path"]: full_name(row) for row in startlist}
    races = [race for race in DEFAULT_RACES if not args.races or race.key in set(args.races)]
    years = sorted(set(args.years))

    client = client_from(args)
    editions: list[RaceEdition] = []
    for race in races:
        wanted = race.years if race.years is not None else tuple(years)
        if race.years is not None:
            print(f"{race.label}: only tracked for {', '.join(map(str, wanted))}", file=sys.stderr)
        for year in wanted:
            edition = fetch_edition(client, race, year)
            editions.append(edition)
            found = f"{len(edition.rows)} riders" if edition.has_result else edition.note
            print(f"  {race.label} {year}: {found}", file=sys.stderr)
            if edition.category and edition.category != MEN_ELITE:
                print(
                    f"    warning: {edition.name} is {edition.category}, not {MEN_ELITE}",
                    file=sys.stderr,
                )

    edition_rows: list[dict[str, object]] = []
    result_rows: list[dict[str, object]] = []
    pivot: dict[str, dict[str, dict[str, object]]] = {}

    # --all-riders keeps the whole field, so remember how PCS spells the names
    # of riders the startlist does not cover.
    pcs_names: dict[str, str] = {}

    for edition in editions:
        race_date = edition.race_date.isoformat() if edition.race_date else ""
        for row in edition.rows:
            if not args.all_riders and row.slug not in names:
                continue
            pcs_names.setdefault(row.slug, row.name)
            result_rows.append(
                {
                    "pcs_rider_path": row.slug,
                    "rider_name": names.get(row.slug) or row.name,
                    "race_key": edition.key,
                    "race_label": edition.label,
                    "year": edition.year,
                    "race_date": race_date,
                    "rank": row.rank if row.rank is not None else "",
                    "status": row.status,
                    "team": row.team,
                    "age": row.age if row.age is not None else "",
                    "uci_points": row.uci_points if row.uci_points is not None else "",
                    "pcs_points": row.pcs_points if row.pcs_points is not None else "",
                    "time": row.time,
                }
            )
            per_race = pivot.setdefault(row.slug, {}).setdefault(edition.key, {})
            per_race[str(edition.year)] = {"rank": row.rank, "status": row.status}

        edition_rows.append(
            {
                "race_key": edition.key,
                "race_label": edition.label,
                "year": edition.year,
                "race_name": edition.name,
                "race_date": race_date,
                "classification": edition.classification,
                "category": edition.category,
                "distance": edition.distance,
                "finishers": sum(1 for row in edition.rows if row.rank is not None),
                "startlist_riders": sum(1 for row in edition.rows if row.slug in names),
                "note": edition.note,
                "url": edition.url,
            }
        )

    document = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "years": years,
        "races": [
            {
                "key": race.key,
                "label": race.label,
                "editions": {
                    str(edition.year): {
                        "name": edition.name,
                        "date": edition.race_date.isoformat() if edition.race_date else None,
                        "url": edition.url,
                        "has_result": edition.has_result,
                        "note": edition.note,
                    }
                    for edition in editions
                    if edition.key == race.key
                },
            }
            for race in races
        ],
        # A missing year means the rider was not on that startlist; a null rank
        # with a status means he started and did not finish classified.
        "riders": {
            slug: {"name": names.get(slug) or pcs_names.get(slug, ""), "results": results}
            for slug, results in sorted(pivot.items())
        },
    }

    editions_path = args.output_dir / "race-editions.csv"
    results_path = args.output_dir / "race-results.csv"
    json_path = args.output_dir / "rider-race-results.json"
    write_csv(editions_path, EDITION_COLUMNS, edition_rows)
    write_csv(results_path, RESULT_COLUMNS, result_rows)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    ridden = sum(1 for edition in editions if edition.has_result)
    print(f"\n{client.summary()}", file=sys.stderr)
    print(f"{ridden}/{len(editions)} editions with a classification -> {editions_path}")
    print(f"{len(result_rows)} result rows -> {results_path}")
    on_startlist = sum(1 for slug in pivot if slug in names)
    print(
        f"{len(pivot)} riders with a result, {on_startlist}/{len(names)} of them "
        f"on the startlist -> {json_path}"
    )


if __name__ == "__main__":
    main()
