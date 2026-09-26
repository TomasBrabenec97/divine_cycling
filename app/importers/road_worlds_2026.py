import html
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Event, EventRider, Rider

PCS_RIDER_PATTERN = re.compile(
    r'<li class=" ">.*?<span class="flag (?P<nation>[a-z]{2})"></span>'
    r'<a href="rider/(?P<slug>[^"]+)">(?P<name>[^<]+)</a></li>',
    re.DOTALL,
)
UCI_URL = "https://dataride.uci.org/iframe/ObjectRankings/"


@dataclass(frozen=True)
class StartlistRider:
    slug: str
    display_name: str
    nation: str


def normalized_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed if character.isalnum()).casefold()


def split_pcs_name(value: str) -> tuple[str, str]:
    """PCS gives names as SURNAME Given-name; retain a safe display fallback."""
    words = html.unescape(value).strip().split()
    first_given = next(
        (index for index, word in enumerate(words) if word != word.upper()), len(words)
    )
    if first_given == 0 or first_given == len(words):
        return "", " ".join(words).title()
    return " ".join(words[first_given:]), " ".join(words[:first_given]).title()


def parse_pcs_startlist(source: str) -> list[StartlistRider]:
    riders = [
        StartlistRider(
            slug=match.group("slug"),
            display_name=html.unescape(match.group("name")).strip(),
            nation=match.group("nation").upper(),
        )
        for match in PCS_RIDER_PATTERN.finditer(source)
    ]
    if not riders:
        raise ValueError("No PCS startlist riders were found in the supplied HTML")
    return riders


def fetch_uci_rankings() -> tuple[dict[str, dict], datetime]:
    """Fetch the UCI Road / Men Elite / Individual 2026 ranking once."""
    payload = {
        "rankingId": 1,
        "disciplineId": 10,
        "rankingTypeId": 1,
        "currentRankingTypeId": 1,
        "take": 4000,
        "skip": 0,
        "page": 1,
        "pageSize": 4000,
        "filter[filters][0][field]": "RaceTypeId",
        "filter[filters][0][value]": 0,
        "filter[filters][1][field]": "CategoryId",
        "filter[filters][1][value]": 22,
        "filter[filters][2][field]": "SeasonId",
        "filter[filters][2][value]": 464,
        "filter[filters][3][field]": "MomentId",
        "filter[filters][3][value]": 205091,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://dataride.uci.org/iframe/Rankings/10/",
        "User-Agent": "Mozilla/5.0 (Windows NT; Windows NT 10.0; en-US) WindowsPowerShell/5.1",
        "X-Requested-With": "XMLHttpRequest",
    }
    response = httpx.post(
        UCI_URL,
        data=payload,
        headers={
            **headers,
        },
        timeout=30,
    )
    response.raise_for_status()
    try:
        document = response.json()
    except json.JSONDecodeError:
        # UCI currently returns its HTML shell to some Python TLS clients. The
        # native Windows client is used only as a local, one-off fallback.
        powershell = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "$body=@{rankingId=1;disciplineId=10;rankingTypeId=1;currentRankingTypeId=1;"
                    "take=4000;skip=0;page=1;pageSize=4000;"
                    "'filter[filters][0][field]'='RaceTypeId';'filter[filters][0][value]'=0;"
                    "'filter[filters][1][field]'='CategoryId';'filter[filters][1][value]'=22;"
                    "'filter[filters][2][field]'='SeasonId';'filter[filters][2][value]'=464;"
                    "'filter[filters][3][field]'='MomentId';'filter[filters][3][value]'=205091};"
                    f"Invoke-RestMethod -Method Post -Uri '{UCI_URL}' -Body $body | "
                    "ConvertTo-Json -Depth 8 -Compress"
                ),
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=45,
        )
        try:
            document = json.loads(powershell.stdout)
            if isinstance(document, str):
                document = json.loads(document)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "UCI returned no usable ranking data. The PCS startlist was not changed. "
                "Retry later with the same command, or use --without-uci to import only the startlist."
            ) from error
    if not isinstance(document, dict) or not document.get("data"):
        raise RuntimeError(
            "UCI returned no usable ranking data. The PCS startlist was not changed. "
            "Retry later with the same command, or use --without-uci to import only the startlist."
        )
    rows = document["data"]
    ranking_time = datetime.fromisoformat(rows[0]["ComputationDate"])
    rankings = {normalized_name(row["IndividualFullName"]): row for row in rows}
    return rankings, ranking_time


def import_startlist(pcs_html_path: Path, include_uci: bool = True) -> dict[str, int | str]:
    startlist = parse_pcs_startlist(pcs_html_path.read_text(encoding="utf-8"))
    rankings: dict[str, dict] = {}
    ranking_time: datetime | None = None
    if include_uci:
        rankings, ranking_time = fetch_uci_rankings()
    db = SessionLocal()
    try:
        event = db.scalar(select(Event).where(Event.slug == "road-worlds-2026"))
        if event is None:
            event = Event(
                slug="road-worlds-2026",
                name="2026 Road World Championship — Men Elite Road Race",
                starts_at=datetime(2026, 9, 27, 13, 0),
                prediction_deadline=datetime(2026, 9, 27, 12, 30),
                # 21:30 CEST, the estimated finish time for the elite men's race.
                results_expected_at=datetime(2026, 9, 27, 19, 30),
                status="open",
                source_name="PCS preliminary startlist",
                source_updated_at=datetime.utcnow(),
            )
            db.add(event)
            db.flush()
        # A row created before `results_expected_at` existed would otherwise be
        # stuck on NULL forever, since only new rows set it above.
        if event.results_expected_at is None:
            event.results_expected_at = datetime(2026, 9, 27, 19, 30)

        matched = 0
        for item in startlist:
            first_name, last_name = split_pcs_name(item.display_name)
            rider = db.scalar(select(Rider).where(Rider.external_id == f"pcs:{item.slug}"))
            if rider is None:
                rider = Rider(
                    external_id=f"pcs:{item.slug}",
                    first_name=first_name,
                    last_name=last_name,
                    nation=item.nation,
                )
                db.add(rider)
                db.flush()
            ranking = rankings.get(normalized_name(item.display_name))
            if ranking:
                matched += 1
            event_rider = db.scalar(
                select(EventRider).where(
                    EventRider.event_id == event.id, EventRider.rider_id == rider.id
                )
            )
            values = {
                "uci_rank": int(ranking["Rank"]) if ranking else 999999,
                "uci_points": float(ranking["Points"]) if ranking else None,
                "is_starter": True,
            }
            if event_rider is None:
                db.add(EventRider(event_id=event.id, rider_id=rider.id, **values))
            else:
                for key, value in values.items():
                    setattr(event_rider, key, value)
        if ranking_time:
            event.source_name = "PCS preliminary startlist + UCI Road Men Elite ranking"
            event.source_updated_at = ranking_time.replace(tzinfo=None)
        db.commit()
        return {
            "event": event.name,
            "startlist_riders": len(startlist),
            "uci_matches": matched,
            "uci_snapshot": ranking_time.date().isoformat() if ranking_time else "not imported",
        }
    finally:
        db.close()
