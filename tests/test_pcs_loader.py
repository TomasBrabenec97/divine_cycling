"""Tests for loading the ProCyclingStats exports into the database.

The loader is exercised against a throwaway SQLite file so a test run can never
reach the local game database.
"""

import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Race, RaceEdition, RaceResult, Rider, RiderProfile, RiderRanking, RiderSeason
from scripts.load_pcs_data import (
    Report,
    load_profiles,
    load_races,
    load_rankings,
    load_seasons,
    rider_ids,
)

RIDERS_CSV = """pcs_rider_path,pcs_name,team,age,date_of_birth,wins_total,profile_url,fetched_at
tadej-pogacar,Tadej Pogacar,UAE Team Emirates - XRG,28,1998-09-21,130,https://pcs/rider/tadej-pogacar,2026-09-22T17:00:00
ali-al-shaikhahmed,Ali Al Shaikhahmed,,26,2000-01-07,0,https://pcs/rider/ali-al-shaikhahmed,2026-09-22T17:00:00
someone-not-in-the-database,Someone Else,A Team,30,1996-01-01,3,https://pcs/rider/someone,2026-09-22T17:00:00
"""

SEASONS_CSV = """pcs_rider_path,season,pcs_points,racedays,kms,wins,top3s,top10s
tadej-pogacar,2026,3690,45,7099,22,27,31
tadej-pogacar,2025,4796,50,8635,20,31,38
ali-al-shaikhahmed,2026,,6,945,0,0,0
"""

STARTLIST_CSV = """pcs_rider_path,team,uci_rank,uci_points,uci_rank_prev,uci_points_prev,uci_date,uci_compare_date
tadej-pogacar,UAE Team Emirates - XRG,1,11481.8,1,11655.0,2026-09-22,2025-12-30
ali-al-shaikhahmed,,,,,,2026-09-22,2025-12-30
"""

EDITIONS_CSV = """race_key,race_label,year,race_name,race_date,classification,category,distance,finishers,startlist_riders,note,url
world-championship,Worlds RR,2025,92nd World Championships ME - Road Race,2025-09-28,WC,ME - Men Elite,267.5 km,30,2,,https://www.procyclingstats.com/race/world-championship/2025/result
world-championship,Worlds RR,2026,93rd World Championships ME - Road Race,2026-09-27,WC,ME - Men Elite,273.2 km,0,0,not ridden yet,https://www.procyclingstats.com/race/world-championship/2026/result
"""

RESULTS_CSV = """pcs_rider_path,rider_name,race_key,race_label,year,race_date,rank,status,team,age,uci_points,pcs_points,time
tadej-pogacar,Tadej Pogacar,world-championship,Worlds RR,2025,2025-09-28,1,,Slovenia,27,900.0,350.0,6:21:20
ali-al-shaikhahmed,Ali Al Shaikhahmed,world-championship,Worlds RR,2025,2025-09-28,,DNF,Saudi Arabia,25,,,
someone-not-in-the-database,Someone Else,world-championship,Worlds RR,2025,2025-09-28,40,,A Team,30,,,12:00
"""

FILES = {
    "riders.csv": RIDERS_CSV,
    "rider-seasons.csv": SEASONS_CSV,
    "startlist-enriched.csv": STARTLIST_CSV,
    "race-editions.csv": EDITIONS_CSV,
    "race-results.csv": RESULTS_CSV,
}


@pytest.fixture
def exports() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        for name, body in FILES.items():
            (path / name).write_text(body, encoding="utf-8")
        yield path


@pytest.fixture
def session() -> Iterator[Session]:
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'test.sqlite3'}")
        Base.metadata.create_all(bind=engine)
        with sessionmaker(bind=engine)() as db:
            db.add_all(
                [
                    Rider(
                        external_id="pcs:tadej-pogacar",
                        first_name="Tadej",
                        last_name="Pogacar",
                        nation="SI",
                    ),
                    Rider(
                        external_id="pcs:ali-al-shaikhahmed",
                        first_name="Ali",
                        last_name="Al Shaikhahmed",
                        nation="SA",
                    ),
                ]
            )
            db.commit()
            yield db
        engine.dispose()


def load_everything(session: Session, exports: Path) -> Report:
    report = Report()
    riders = rider_ids(session)
    load_profiles(session, riders, exports / "riders.csv", report)
    load_seasons(session, riders, exports / "rider-seasons.csv", report)
    load_rankings(session, riders, exports / "startlist-enriched.csv", report)
    load_races(session, riders, exports, report)
    session.commit()
    return report


def test_profiles_seasons_and_races_load_from_the_exports(session: Session, exports: Path) -> None:
    load_everything(session, exports)

    profile = session.scalar(
        select(RiderProfile).join(Rider).where(Rider.external_id == "pcs:tadej-pogacar")
    )
    assert profile.age == 28
    assert profile.date_of_birth == date(1998, 9, 21)
    assert profile.wins_total == 130
    assert profile.team == "UAE Team Emirates - XRG"

    season = session.scalar(
        select(RiderSeason).where(
            RiderSeason.rider_id == profile.rider_id, RiderSeason.season == 2026
        )
    )
    assert (season.wins, season.racedays, season.top10s) == (22, 45, 31)

    race = session.scalar(select(Race).where(Race.key == "world-championship"))
    assert race.label == "Worlds RR"
    assert race.pcs_slug == "world-championship"
    assert len(race.editions) == 2


def test_both_ranking_snapshots_are_stored_as_separate_rows(
    session: Session, exports: Path
) -> None:
    load_everything(session, exports)

    rankings = session.scalars(
        select(RiderRanking)
        .join(Rider)
        .where(Rider.external_id == "pcs:tadej-pogacar")
        .order_by(RiderRanking.ranking_date)
    ).all()

    assert [row.ranking_date for row in rankings] == [date(2025, 12, 30), date(2026, 9, 22)]
    assert [row.uci_points for row in rankings] == [11655.0, 11481.8]
    # Only the current snapshot carries a team; the reference one has no column for it.
    assert [row.team for row in rankings] == ["", "UAE Team Emirates - XRG"]


def test_a_rider_outside_a_ranking_gets_no_row_for_that_date(
    session: Session, exports: Path
) -> None:
    load_everything(session, exports)

    rankings = session.scalars(
        select(RiderRanking).join(Rider).where(Rider.external_id == "pcs:ali-al-shaikhahmed")
    ).all()

    assert rankings == []


def test_a_rider_who_did_not_finish_keeps_the_status_without_a_position(
    session: Session, exports: Path
) -> None:
    load_everything(session, exports)

    result = session.scalar(
        select(RaceResult).join(Rider).where(Rider.external_id == "pcs:ali-al-shaikhahmed")
    )

    assert result.position is None
    assert result.status == "DNF"


def test_an_edition_with_no_classification_keeps_the_reason(
    session: Session, exports: Path
) -> None:
    load_everything(session, exports)

    edition = session.scalar(select(RaceEdition).where(RaceEdition.year == 2026))

    assert edition.note == "not ridden yet"
    assert edition.results == []


def test_rows_for_unknown_riders_are_skipped_not_invented(session: Session, exports: Path) -> None:
    report = load_everything(session, exports)

    assert len(session.scalars(select(RiderProfile)).all()) == 2
    assert len(session.scalars(select(RaceResult)).all()) == 2
    assert report.skipped["rider_profiles"] == ["someone-not-in-the-database"]
    assert report.skipped["race_results"] == ["someone-not-in-the-database"]


def test_reloading_the_same_exports_changes_nothing(session: Session, exports: Path) -> None:
    load_everything(session, exports)

    second = load_everything(session, exports)

    for table, counts in second.counts.items():
        assert counts["created"] == 0, f"{table} created rows on a reload"
        assert counts["updated"] == 0, f"{table} rewrote rows on a reload"
