"""Read a rider's ProCyclingStats profile: age, team and win counts.

Two pages are needed per rider. The overview carries the date of birth and the
career "Wins" figure PCS puts in its key-statistics box; the season-statistics
table carries the per-season breakdown, which is the only place a single
season's win count is published.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from bs4 import BeautifulSoup

from scripts.pcs.client import PCSClient
from scripts.pcs.parsing import as_float, as_int, header_label, text

OVERVIEW_PATH = "rider/{slug}"
SEASONS_PATH = "rider/{slug}/statistics/season-statistics"

# "21st September 1998 ( 28 )" -- the trailing number is PCS's own age today.
_DOB = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]+)\s+(\d{4})")
_AGE = re.compile(r"\(\s*(\d{1,3})\s*\)")


@dataclass(frozen=True)
class SeasonStats:
    """One row of the rider's season-statistics table."""

    season: int | None  # None marks the career-total row PCS appends last
    points: float | None
    racedays: int | None
    kms: int | None
    wins: int | None
    top3s: int | None
    top10s: int | None


@dataclass
class RiderProfile:
    slug: str
    name: str = ""
    country_code: str = ""
    nationality: str = ""
    team: str = ""
    date_of_birth: date | None = None
    age: int | None = None
    wins_total: int | None = None
    seasons: list[SeasonStats] = field(default_factory=list)

    def season(self, year: int) -> SeasonStats | None:
        return next((row for row in self.seasons if row.season == year), None)

    def wins_in(self, year: int) -> int:
        row = self.season(year)
        return (row.wins or 0) if row else 0

    @property
    def career_totals(self) -> SeasonStats | None:
        return next((row for row in self.seasons if row.season is None), None)


def parse_overview(html: str, slug: str) -> RiderProfile:
    soup = BeautifulSoup(html, "html.parser")
    profile = RiderProfile(slug=slug)

    heading = soup.select_one(".page-title .title h1")
    profile.name = text(heading)
    subtitle = soup.select_one(".page-title .subtitle h2")
    profile.team = text(subtitle)

    # PCS renders the nationality as <span class="flag si w32">: the two-letter
    # class is the country code, the rest are sizing hints.
    flag = soup.select_one(".page-title .title span.flag")
    if flag is not None:
        codes = [cls for cls in flag.get("class", []) if len(cls) == 2 and cls.isalpha()]
        profile.country_code = codes[0].upper() if codes else ""

    for item in soup.find_all("li"):
        label = text(item.find("div", class_="bold")).rstrip(":").casefold()
        if label == "date of birth":
            line = text(item)
            match = _DOB.search(line)
            if match:
                day, month, year = match.groups()
                try:
                    profile.date_of_birth = datetime.strptime(
                        f"{day} {month} {year}", "%d %B %Y"
                    ).date()
                except ValueError:
                    profile.date_of_birth = None
            age = _AGE.search(line)
            profile.age = int(age.group(1)) if age else _years_since(profile.date_of_birth)
        elif label == "nationality":
            link = item.find("a", href=lambda h: h and "nation/" in h)
            profile.nationality = text(link)

    kpis = soup.find("ul", class_="rider-kpi")
    if kpis is not None:
        for entry in kpis.find_all("li"):
            if text(entry.find(class_="title")).casefold().startswith("wins"):
                profile.wins_total = as_int(text(entry.find(class_="kpi")))
                break

    return profile


def parse_seasons(html: str) -> list[SeasonStats]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="basic")
    if table is None:
        return []

    columns = {header_label(th).lower(): index for index, th in enumerate(table.find_all("th"))}
    rows: list[SeasonStats] = []
    for row in (table.find("tbody") or table).find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        rows.append(
            SeasonStats(
                season=as_int(_value(cells, columns, "season")),
                points=as_float(_value(cells, columns, "points")),
                racedays=as_int(_value(cells, columns, "racedays")),
                kms=as_int(_value(cells, columns, "kms")),
                wins=as_int(_value(cells, columns, "wins")),
                top3s=as_int(_value(cells, columns, "top-3s")),
                top10s=as_int(_value(cells, columns, "top-10s")),
            )
        )
    return rows


def _value(cells, columns: dict[str, int], label: str) -> str:
    """The text under ``label``, or "" when PCS does not render that column."""
    index = columns.get(label)
    return text(cells[index]) if index is not None and index < len(cells) else ""


def fetch_rider(client: PCSClient, slug: str) -> RiderProfile | None:
    """Fetch and merge a rider's overview and season-statistics pages."""
    overview = client.get_optional(OVERVIEW_PATH.format(slug=slug))
    if overview is None:
        return None

    profile = parse_overview(overview, slug)
    seasons = client.get_optional(SEASONS_PATH.format(slug=slug))
    if seasons is not None:
        profile.seasons = parse_seasons(seasons)

    if profile.wins_total is None:
        totals = profile.career_totals
        profile.wins_total = (totals.wins or 0) if totals else None
    return profile


def _years_since(born: date | None, today: date | None = None) -> int | None:
    if born is None:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
