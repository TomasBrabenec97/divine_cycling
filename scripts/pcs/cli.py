"""Command-line plumbing every PCS fetcher shares."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.pcs.client import DEFAULT_CACHE_DIR, DEFAULT_DELAY, PCSClient

STARTLIST_CSV = Path("exports/road-worlds-2026-startlist.csv")
OUTPUT_DIR = Path("exports/pcs")


def add_client_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="seconds between network requests (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="where downloaded pages are kept (default: %(default)s)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-download pages already in the cache"
    )


def client_from(args: argparse.Namespace) -> PCSClient:
    return PCSClient(cache_dir=args.cache_dir, delay=args.delay, refresh=args.refresh)


def read_startlist(path: Path) -> list[dict[str, str]]:
    """Read the startlist CSV, keyed on the PCS rider slug."""
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise SystemExit(f"{path} is empty.")
        if "pcs_rider_path" not in {(name or "").strip() for name in reader.fieldnames}:
            raise SystemExit(f"{path} has no pcs_rider_path column.")
        rows = [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in reader
        ]
    riders = [row for row in rows if row.get("pcs_rider_path")]
    if not riders:
        raise SystemExit(f"{path} lists no riders.")
    return riders


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
