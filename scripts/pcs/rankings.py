"""Read the ProCyclingStats UCI World Ranking (men elite, 52-week rolling).

Only ``rankings.php`` supports pagination. The pretty
``/rankings/me/uci-individual`` URL returns ranks 1-100 and silently ignores
every query parameter, so don't be tempted to use it -- it looks like it works
and gives you one page forever.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from bs4 import BeautifulSoup

from scripts.pcs.client import PCSClient
from scripts.pcs.parsing import (
    as_float,
    as_int,
    header_label,
    rider_slug,
    select_values,
    team_from,
    text,
)

RANKINGS_PATH = "rankings.php"
BASE_PARAMS: dict[str, object] = {"p": "me", "s": "uci-individual", "filter": "Filter"}


@dataclass(frozen=True)
class RankingEntry:
    """One rider's standing on one ranking date."""

    slug: str
    rank: int | None
    points: float | None
    name: str = ""
    team: str = ""
    team_slug: str = ""


def parse_entries(html: str) -> dict[str, RankingEntry]:
    """Extract ``{slug: RankingEntry}`` from one ranking page."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return {}

    # Read the columns off the header rather than assuming their order: PCS
    # has added and dropped columns (Team is the most recent arrival).
    columns = {header_label(th).lower(): index for index, th in enumerate(table.find_all("th"))}
    out: dict[str, RankingEntry] = {}

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        slug = rider_slug(row)
        if not slug or slug in out:
            continue

        rider_cell = _cell(cells, columns, "rider")
        team_name, team_slug = team_from(_cell(cells, columns, "team"))
        points_cell = _cell(cells, columns, "points")
        out[slug] = RankingEntry(
            slug=slug,
            rank=as_int(text(_cell(cells, columns, "#")) or text(cells[0])),
            points=as_float(text(points_cell)) if points_cell is not None else _last_number(cells),
            name=text(rider_cell) if rider_cell is not None else "",
            team=team_name,
            team_slug=team_slug,
        )

    return out


def _cell(cells, columns: dict[str, int], label: str):
    """The cell under ``label``, or None when PCS does not render that column."""
    index = columns.get(label)
    return cells[index] if index is not None and index < len(cells) else None


def _last_number(cells) -> float | None:
    """Fallback for a page without a recognisable Points header."""
    for cell in reversed(cells):
        value = as_float(text(cell))
        if value is not None:
            return value
    return None


def available_dates(html: str) -> list[str]:
    return [value for value in select_values(BeautifulSoup(html, "html.parser"), "date") if value]


def fetch_ranking(
    client: PCSClient,
    date: str | None = None,
    wanted: set[str] | None = None,
    *,
    label: str = "ranking",
) -> tuple[str, dict[str, RankingEntry]]:
    """Page the ranking for ``date``, stopping once every ``wanted`` slug is in.

    Returns the effective ranking date and ``{slug: RankingEntry}``.
    """
    params = dict(BASE_PARAMS, offset=0)
    if date:
        params["date"] = date
    html = client.get(RANKINGS_PATH, params)

    dates = available_dates(html)
    if date and dates and date not in dates:
        raise SystemExit(
            f"PCS publishes no {label} for {date}. Nearest available: " + ", ".join(dates[:5])
        )
    effective = date or (dates[0] if dates else "")

    ranking = parse_entries(html)
    if not ranking:
        raise SystemExit(
            f"No rider rows parsed from the {label} page. PCS may have changed "
            f"its markup; inspect {client.url(RANKINGS_PATH, params)}."
        )

    soup = BeautifulSoup(html, "html.parser")
    offsets = [
        int(value)
        for value in select_values(soup, "offset")
        if re.fullmatch(r"\d+", value or "") and int(value) > 0
    ]
    print(
        f"{label} {effective or '(site default)'}: {len(offsets) + 1} pages, "
        f"{len(ranking)} riders on page 1",
        file=sys.stderr,
    )

    missing = set(wanted) - set(ranking) if wanted is not None else None
    for offset in offsets:
        if missing is not None and not missing:
            print(f"  all wanted riders found, stopping at offset {offset}", file=sys.stderr)
            break

        page = parse_entries(client.get(RANKINGS_PATH, dict(params, offset=offset)))
        fresh = {slug: entry for slug, entry in page.items() if slug not in ranking}
        ranking.update(fresh)
        if missing is not None:
            missing -= set(page)
        tail = f", {len(missing)} still missing" if missing is not None else ""
        print(
            f"  offset {offset:>5}: +{len(fresh)} riders (total {len(ranking)}{tail})",
            file=sys.stderr,
        )
        if not page:
            break

    return effective, ranking
