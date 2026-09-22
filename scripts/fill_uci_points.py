#!/usr/bin/env python3
"""Fill the UCI ranking columns of a startlist CSV from ProCyclingStats.

Reads the UCI World Ranking (men elite, 52-week rolling) on two dates -- the
current one and a reference date -- and writes each rider's rank, points,
trade team and the movement between the two into the output CSV.

Join key is `pcs_rider_path`, the rider slug from PCS /rider/<slug> URLs.

    python -m scripts.fill_uci_points <input.csv> <output.csv>

    # other snapshot dates (both must be dates PCS publishes, i.e. Mondays):
    python -m scripts.fill_uci_points in.csv out.csv --date 2026-09-15 \
        --compare-date 2025-12-30

    # only the current snapshot, no movement columns:
    python -m scripts.fill_uci_points in.csv out.csv --no-compare

Columns written on top of the input:

    team                   trade team on the current ranking date
    uci_rank_prev          rank on the compare date
    uci_points_prev        points on the compare date
    uci_points_change      points now - points then      (positive = gained)
    uci_points_change_pct  that change as a % of "then"
    uci_rank_change        rank then - rank now          (positive = moved up)
    uci_rank_change_pct    that climb as a % of "then"
    uci_date               the ranking date the snapshot is from
    uci_compare_date       the reference date the movement is measured against

Riders absent from a snapshot keep empty cells there rather than a guess, and
the movement columns stay empty unless the rider appears in both snapshots.

Both percentages are the change as a share of the earlier value. That is worth
remembering for ranks: climbing towards #1 can never beat +100%, while falling
away from it is unbounded, so -280% is a real answer and not a bug.

Run from your own machine. PCS disallows automated access in robots.txt, so
the delay between requests is deliberate -- please leave it in.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from scripts.pcs.cli import add_client_arguments, client_from
from scripts.pcs.rankings import RankingEntry, fetch_ranking

CURRENT_DATE = "2026-09-22"
COMPARE_DATE = "2025-12-30"

ADDED_COLUMNS = [
    "team",
    "uci_rank_prev",
    "uci_points_prev",
    "uci_points_change",
    "uci_points_change_pct",
    "uci_rank_change",
    "uci_rank_change_pct",
    "uci_date",
    "uci_compare_date",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--date", default=CURRENT_DATE, help="current ranking date")
    parser.add_argument("--compare-date", default=COMPARE_DATE, help="reference ranking date")
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="skip the reference snapshot and the movement columns",
    )
    add_client_arguments(parser)
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise SystemExit(f"{path} is empty.")
        return [(name or "").strip() for name in reader.fieldnames], list(reader)


def movement(current: RankingEntry | None, previous: RankingEntry | None) -> dict[str, object]:
    """Absolute and relative movement between the two snapshots."""
    out: dict[str, object] = {
        "uci_rank_prev": "",
        "uci_points_prev": "",
        "uci_points_change": "",
        "uci_points_change_pct": "",
        "uci_rank_change": "",
        "uci_rank_change_pct": "",
    }
    if previous is None:
        return out
    out["uci_rank_prev"] = previous.rank if previous.rank is not None else ""
    out["uci_points_prev"] = previous.points if previous.points is not None else ""
    if current is None:
        return out

    if current.points is not None and previous.points is not None:
        change = current.points - previous.points
        out["uci_points_change"] = round(change, 1)
        if previous.points:
            out["uci_points_change_pct"] = round(100 * change / previous.points, 1)
    if current.rank is not None and previous.rank is not None:
        climb = previous.rank - current.rank
        out["uci_rank_change"] = climb
        if previous.rank:
            out["uci_rank_change_pct"] = round(100 * climb / previous.rank, 1)
    return out


def main() -> None:
    args = parse_args()
    fieldnames, rows = read_csv(args.input_csv)
    for column in ("uci_rank", "uci_points", *ADDED_COLUMNS):
        if column not in fieldnames:
            fieldnames.append(column)

    wanted = {(row.get("pcs_rider_path") or "").strip() for row in rows} - {""}
    client = client_from(args)

    date, current = fetch_ranking(client, args.date, wanted, label="ranking")
    compare_date, previous = "", {}
    if not args.no_compare:
        compare_date, previous = fetch_ranking(
            client, args.compare_date, wanted, label="reference ranking"
        )

    hits, missing = 0, []
    for row in rows:
        slug = (row.get("pcs_rider_path") or "").strip()
        entry, before = current.get(slug), previous.get(slug)
        if entry is None:
            missing.append(slug)
        else:
            hits += 1
            row["uci_rank"] = entry.rank if entry.rank is not None else ""
            row["uci_points"] = entry.points if entry.points is not None else ""
            row["team"] = entry.team
        row.update(movement(entry, before))
        row["uci_date"] = date
        row["uci_compare_date"] = compare_date

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{client.summary()}", file=sys.stderr)
    print(f"matched {hits}/{len(rows)} riders on {date} -> {args.output_csv}", file=sys.stderr)
    if not args.no_compare:
        both = sum(1 for row in rows if row.get("uci_rank_change") != "")
        print(f"movement against {compare_date} for {both}/{len(rows)} riders", file=sys.stderr)
    if missing:
        print(f"\n{len(missing)} unranked or unmatched:", file=sys.stderr)
        for slug in missing:
            print(f"  {slug}", file=sys.stderr)


if __name__ == "__main__":
    main()
