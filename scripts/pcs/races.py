"""Read one-day race results from ProCyclingStats.

The races tracked here are the one-day events that say something about a
rider's form on a Worlds-like parcours. Every edition is fetched from
``race/<slug>/<year>/result``, which is the full classification -- the plain
``race/<slug>/<year>`` page only shows a top-10 teaser.

Three shapes of "no result" have to be told apart, and PCS signals them
differently:

* the edition never existed        -> HTTP 500 with a one-byte body
* the edition is still to be ridden -> HTTP 200 with an empty result table
* the edition was cancelled         -> HTTP 200 with an empty result table
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag

from scripts.pcs.client import PCSClient
from scripts.pcs.parsing import as_float, as_int, keyvalue_list, rider_slug, team_from, text

RESULT_PATH = "race/{slug}/{year}/result"

MEN_ELITE = "ME - Men Elite"


@dataclass(frozen=True)
class RaceSpec:
    """A race to track, and the seasons worth asking for."""

    key: str
    slug: str
    label: str
    years: tuple[int, ...] | None = None  # None -> whatever --years asks for


# Paris-Roubaix is deliberately the men's slug. PCS uses `paris-roubaix-we`
# for Paris-Roubaix Femmes, which is a Women Elite race and would not match a
# single rider on a men's startlist; --races can override this list.
DEFAULT_RACES: tuple[RaceSpec, ...] = (
    RaceSpec("world-championship", "world-championship", "Worlds RR"),
    RaceSpec("strade-bianche", "strade-bianche", "Strade Bianche"),
    RaceSpec("san-sebastian", "san-sebastian", "San Sebastián"),
    RaceSpec("gp-quebec", "gp-quebec", "GP Québec"),
    RaceSpec("gp-montreal", "gp-montreal", "GP Montréal"),
    RaceSpec("paris-roubaix", "paris-roubaix", "Paris-Roubaix"),
    RaceSpec("ronde-van-vlaanderen", "ronde-van-vlaanderen", "Ronde van Vlaanderen"),
    RaceSpec("milano-sanremo", "milano-sanremo", "Milano-Sanremo"),
    RaceSpec("liege-bastogne-liege", "liege-bastogne-liege", "Liège-Bastogne-Liège"),
    RaceSpec("il-lombardia", "il-lombardia", "Il Lombardia"),
    RaceSpec("olympic-games", "olympic-games", "Olympics RR", years=(2024,)),
)

RACES_BY_KEY = {race.key: race for race in DEFAULT_RACES}


@dataclass(frozen=True)
class ResultRow:
    """One rider's line in a race classification."""

    rank: int | None  # None for anyone who did not finish classified
    status: str  # "" when classified, else DNF / DNS / OTL / DSQ ...
    slug: str
    name: str
    team: str
    age: int | None
    uci_points: float | None
    pcs_points: float | None
    time: str


@dataclass
class RaceEdition:
    key: str
    slug: str
    label: str
    year: int
    url: str
    name: str = ""
    race_date: date | None = None
    classification: str = ""
    category: str = ""
    distance: str = ""
    rows: list[ResultRow] = field(default_factory=list)
    note: str = ""  # empty when the edition has a classification

    @property
    def has_result(self) -> bool:
        return bool(self.rows)


def parse_result(html: str) -> tuple[dict[str, str], list[ResultRow]]:
    soup = BeautifulSoup(html, "html.parser")
    info = keyvalue_list(soup)

    heading = soup.find("h1")
    # "2025 » 92nd World Championships ME - Road Race (WC)" -> the race part.
    name = text(heading).split("»", 1)[-1].strip() if heading else ""
    if name.endswith(")") and "(" in name:
        name = name.rsplit("(", 1)[0].strip()
    info["name"] = name

    table = soup.find("table", class_="results")
    if table is None:
        return info, []

    rows: list[ResultRow] = []
    for row in (table.find("tbody") or table).find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        slug = rider_slug(row.find("td", class_="ridername"))
        if not slug:
            continue

        raw_rank = text(cells[0])
        rank = as_int(raw_rank)
        team, _ = team_from(row.find("td", class_="cu600"))
        rows.append(
            ResultRow(
                rank=rank,
                status="" if rank is not None else raw_rank.upper(),
                slug=slug,
                name=_rider_name(row.find("td", class_="ridername")),
                team=team,
                age=as_int(text(row.find("td", class_="age"))),
                uci_points=as_float(text(row.find("td", class_="uci_pnt"))),
                pcs_points=as_float(text(row.find("td", class_="pnt"))),
                time=_time(row.find("td", class_="time")),
            )
        )
    return info, rows


def fetch_edition(client: PCSClient, race: RaceSpec, year: int) -> RaceEdition:
    path = RESULT_PATH.format(slug=race.slug, year=year)
    edition = RaceEdition(
        key=race.key, slug=race.slug, label=race.label, year=year, url=client.url(path)
    )

    html = client.get_optional(path)
    if html is None:
        edition.note = "no such edition on PCS"
        return edition

    info, rows = parse_result(html)
    edition.name = info.get("name", "")
    edition.race_date = _as_date(info.get("Date"))
    edition.classification = info.get("Classification", "")
    edition.category = info.get("Race category", "")
    edition.distance = info.get("Distance", "")
    edition.rows = rows
    if not rows:
        edition.note = (
            "not ridden yet"
            if edition.race_date and edition.race_date >= date.today()
            else "no classification published"
        )
    return edition


def _rider_name(cell: Tag | None) -> str:
    """Turn PCS's "SURNAME Firstname" link into "Firstname Surname".

    The cell also carries a mobile-only country line, so only the link text is
    read, and the surname is whatever sits in the uppercase span.
    """
    if cell is None:
        return ""
    link = cell.find("a", href=lambda h: h and "rider/" in h)
    if link is None:
        return text(cell)
    surname = link.find("span", class_="uppercase")
    if surname is None:
        return text(link)
    last = text(surname)
    first = text(link)[len(last) :].strip()
    return f"{first} {last}".strip()


def _time(cell: Tag | None) -> str:
    """The finishing time or gap to the winner.

    Read the hidden span rather than the visible one: PCS prints ",," for a
    rider who shares the gap of the line above and keeps the real value in the
    span it hides.
    """
    if cell is None:
        return ""
    hidden = cell.find("span", class_="hide")
    return text(hidden) if hidden is not None else text(cell)


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d %B %Y").date()
    except ValueError:
        return None
