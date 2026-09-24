#!/usr/bin/env python3
"""Download a small jersey icon for every trade team on the startlist.

Team names come from the enriched startlist and the rider profiles, which is
exactly the set the API can hand the frontend. The PCS team slug for each name
is read off the UCI ranking pages (already cached by `fill_uci_points`), then
each team page is visited once for the path of its jersey image. The image is
shrunk to a small square PNG so the whole set stays light enough to ship with
the static frontend.

    python -m scripts.fetch_team_icons

Outputs:

    frontend/teams/<team-slug>.png   one jersey per team, 48 px square
    frontend/teams/index.json        {"<team name>": "<team-slug>.png", ...}

The frontend reads index.json to find a team's icon by its display name, the
same way it finds a flag by country code. A team PCS shows no jersey for is
left out of the index, and the UI falls back to a plain monogram.

Needs Pillow (`pip install -e ".[dev]"`). Run from your own machine: PCS
disallows automated access in robots.txt, so the delay between requests is
deliberate -- please leave it in.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

from scripts.pcs.cli import OUTPUT_DIR, add_client_arguments, client_from
from scripts.pcs.client import PCSClient
from scripts.pcs.rankings import fetch_ranking

STARTLIST_ENRICHED = OUTPUT_DIR / "startlist-enriched.csv"
RIDERS_CSV = OUTPUT_DIR / "riders.csv"
ICON_DIR = Path("frontend/teams")
RANKING_DATES = ("2026-09-22", "2025-12-30")
ICON_SIZE = 48

_SHIRT = re.compile(r"""images/shirts/[^"'\s]+\.png""")
_YEAR_SUFFIX = re.compile(r"-\d{4}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output-dir", type=Path, default=ICON_DIR)
    parser.add_argument("--size", type=int, default=ICON_SIZE, help="icon edge in pixels")
    add_client_arguments(parser)
    return parser.parse_args()


def team_names() -> set[str]:
    names: set[str] = set()
    for path in (STARTLIST_ENRICHED, RIDERS_CSV):
        with path.open(newline="", encoding="utf-8-sig") as file:
            names.update((row.get("team") or "").strip() for row in csv.DictReader(file))
    return {name for name in names if name}


def team_slugs(client: PCSClient, wanted: set[str]) -> dict[str, str]:
    """Map team display names to PCS team slugs using the ranking pages."""
    slugs: dict[str, str] = {}
    for date in RANKING_DATES:
        _, ranking = fetch_ranking(client, date, label=f"ranking {date}")
        for entry in ranking.values():
            if entry.team and entry.team_slug and entry.team in wanted:
                slugs.setdefault(entry.team, entry.team_slug)
        if wanted <= set(slugs):
            break
    return slugs


def square_icon(png: bytes, size: int) -> bytes:
    """Fit the jersey into a transparent square without distorting it."""
    with Image.open(io.BytesIO(png)) as source:
        image = source.convert("RGBA")
    bounds = image.getbbox()
    if bounds:
        image = image.crop(bounds)
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2), image)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def download_png(client: PCSClient, path: str, page: str) -> bytes:
    png = client.get_bytes(path, referer=page)
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError(f"PCS did not return a PNG for {path}")
    return png


def main() -> int:
    args = parse_args()
    client = client_from(args)
    wanted = team_names()
    slugs = team_slugs(client, wanted)
    missing = sorted(wanted - set(slugs))
    if missing:
        print(f"No PCS slug for {len(missing)} teams: {', '.join(missing)}", file=sys.stderr)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    for name, slug in sorted(slugs.items()):
        page = client.get_optional(f"team/{slug}")
        shirt = _SHIRT.search(page or "")
        if shirt is None:
            print(f"  no jersey on the page of {name}", file=sys.stderr)
            continue
        filename = f"{_YEAR_SUFFIX.sub('', slug)}.png"
        target = args.output_dir / filename
        if args.refresh or not target.exists():
            png = download_png(client, shirt.group(0), page=f"team/{slug}")
            target.write_bytes(square_icon(png, args.size))
            print(f"  wrote {filename}", file=sys.stderr)
        index[name] = filename

    (args.output_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"{len(index)} team icons in {args.output_dir} ({client.summary()})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
