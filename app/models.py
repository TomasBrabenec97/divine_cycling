from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="player")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    prediction_deadline: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="open")
    source_name: Mapped[str] = mapped_column(String(80), default="mock data")
    source_updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    event_riders: Mapped[list["EventRider"]] = relationship(back_populates="event")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="event")


class Rider(Base):
    __tablename__ = "riders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    nation: Mapped[str] = mapped_column(String(3))
    event_riders: Mapped[list["EventRider"]] = relationship(back_populates="rider")
    profile: Mapped["RiderProfile | None"] = relationship(back_populates="rider")
    seasons: Mapped[list["RiderSeason"]] = relationship(back_populates="rider")
    rankings: Mapped[list["RiderRanking"]] = relationship(back_populates="rider")
    race_results: Mapped[list["RaceResult"]] = relationship(back_populates="rider")


class EventRider(Base):
    __tablename__ = "event_riders"
    __table_args__ = (UniqueConstraint("event_id", "rider_id", name="uq_event_rider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    uci_rank: Mapped[int] = mapped_column(Integer)
    uci_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_starter: Mapped[bool] = mapped_column(default=True)
    event: Mapped[Event] = relationship(back_populates="event_riders")
    rider: Mapped[Rider] = relationship(back_populates="event_riders")


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("player_id", "event_id", name="uq_player_event_prediction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    boosted_rider_id: Mapped[int | None] = mapped_column(ForeignKey("riders.id"), nullable=True)
    player: Mapped[Player] = relationship(back_populates="predictions")
    event: Mapped[Event] = relationship(back_populates="predictions")
    items: Mapped[list["PredictionItem"]] = relationship(
        back_populates="prediction", cascade="all, delete-orphan"
    )
    wildcards: Mapped[list["PredictionWildcard"]] = relationship(
        back_populates="prediction",
        cascade="all, delete-orphan",
        order_by="PredictionWildcard.slot",
    )


class PredictionTemplate(Base):
    """A named draft list a player keeps next to the final prediction.

    Templates are never scored; saving one as final copies its picks into the
    player's `Prediction`. The picks are stored as JSON because a draft may be
    incomplete and is always read and written whole.
    """

    __tablename__ = "prediction_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    picks_json: Mapped[str] = mapped_column(Text, default='{"selections": [], "wildcards": []}')
    # The player's own tab order; ties (templates from before ordering) fall
    # back to creation order.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FavouriteRider(Base):
    """A rider a player has hearted to narrow the pool while building lists.

    Favourites are a browsing aid only: they never affect a prediction or its
    score, so they stay editable after the deadline.
    """

    __tablename__ = "favourite_riders"
    __table_args__ = (
        UniqueConstraint("player_id", "event_id", "rider_id", name="uq_favourite_rider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PredictionItem(Base):
    __tablename__ = "prediction_items"
    __table_args__ = (
        UniqueConstraint("prediction_id", "position", name="uq_prediction_position"),
        UniqueConstraint("prediction_id", "rider_id", name="uq_prediction_rider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), index=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    prediction: Mapped[Prediction] = relationship(back_populates="items")
    rider: Mapped[Rider] = relationship()


class PredictionWildcard(Base):
    """One of the unpositioned wildcard riders that go with a Top 10.

    A wildcard never also appears in the same prediction's Top 10; the API
    enforces that, since it spans two tables.
    """

    __tablename__ = "prediction_wildcards"
    __table_args__ = (
        UniqueConstraint("prediction_id", "slot", name="uq_prediction_wildcard_slot"),
        UniqueConstraint("prediction_id", "rider_id", name="uq_prediction_wildcard_rider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), index=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)
    prediction: Mapped[Prediction] = relationship(back_populates="wildcards")
    rider: Mapped[Rider] = relationship()


class EventResult(Base):
    __tablename__ = "event_results"
    __table_args__ = (
        UniqueConstraint("event_id", "rider_id", name="uq_event_result_rider"),
        UniqueConstraint("event_id", "position", name="uq_event_result_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)


class ScoreRun(Base):
    __tablename__ = "score_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    rules_version: Mapped[str] = mapped_column(String(40))
    rules_json: Mapped[str] = mapped_column(Text)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScoreLine(Base):
    __tablename__ = "score_lines"
    __table_args__ = (
        UniqueConstraint("score_run_id", "prediction_id", name="uq_score_run_prediction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score_run_id: Mapped[int] = mapped_column(ForeignKey("score_runs.id"), index=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), index=True)
    total_points: Mapped[float] = mapped_column(Float)
    breakdown_json: Mapped[str] = mapped_column(Text)


# -- ProCyclingStats reference data -----------------------------------------
#
# Everything below is imported from PCS by scripts/load_pcs_data.py and is
# read-only background for the game: it never feeds scoring, which stays on
# EventRider and EventResult.


class RiderProfile(Base):
    """The facts PCS publishes on a rider's own page."""

    __tablename__ = "rider_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), unique=True, index=True)
    pcs_name: Mapped[str] = mapped_column(String(120), default="")
    team: Mapped[str] = mapped_column(String(120), default="")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    wins_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_url: Mapped[str] = mapped_column(String(200), default="")
    source_updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rider: Mapped[Rider] = relationship(back_populates="profile")


class RiderSeason(Base):
    """One rider's totals for one season, including wins."""

    __tablename__ = "rider_seasons"
    __table_args__ = (UniqueConstraint("rider_id", "season", name="uq_rider_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    pcs_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    racedays: Mapped[int] = mapped_column(Integer, default=0)
    kms: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    top3s: Mapped[int] = mapped_column(Integer, default=0)
    top10s: Mapped[int] = mapped_column(Integer, default=0)
    rider: Mapped[Rider] = relationship(back_populates="seasons")


class RiderRanking(Base):
    """A rider's UCI standing on one ranking date.

    Movement between two dates is a difference of two rows rather than a stored
    number, so any pair of snapshots can be compared later.
    """

    __tablename__ = "rider_rankings"
    __table_args__ = (UniqueConstraint("rider_id", "ranking_date", name="uq_rider_ranking_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    ranking_date: Mapped[date] = mapped_column(Date, index=True)
    uci_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uci_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    team: Mapped[str] = mapped_column(String(120), default="")
    rider: Mapped[Rider] = relationship(back_populates="rankings")


class Race(Base):
    """A tracked race, independent of the year it was ridden."""

    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80))
    pcs_slug: Mapped[str] = mapped_column(String(80), default="")
    editions: Mapped[list["RaceEdition"]] = relationship(
        back_populates="race", order_by="RaceEdition.year"
    )


class RaceEdition(Base):
    """One running of a tracked race.

    An edition with no results -- still to be ridden, or cancelled -- is kept
    with the reason in `note` so the difference stays visible.
    """

    __tablename__ = "race_editions"
    __table_args__ = (UniqueConstraint("race_id", "year", name="uq_race_edition_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    race_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    classification: Mapped[str] = mapped_column(String(20), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    distance: Mapped[str] = mapped_column(String(20), default="")
    note: Mapped[str] = mapped_column(String(80), default="")
    url: Mapped[str] = mapped_column(String(200), default="")
    race: Mapped[Race] = relationship(back_populates="editions")
    results: Mapped[list["RaceResult"]] = relationship(
        back_populates="edition", cascade="all, delete-orphan"
    )


class RaceResult(Base):
    """One rider's line in one edition's classification.

    `position` is null for a rider who started but was not classified; `status`
    then holds the PCS marker (DNF, DNS, OTL, DSQ).
    """

    __tablename__ = "race_results"
    __table_args__ = (UniqueConstraint("race_edition_id", "rider_id", name="uq_race_result_rider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_edition_id: Mapped[int] = mapped_column(ForeignKey("race_editions.id"), index=True)
    rider_id: Mapped[int] = mapped_column(ForeignKey("riders.id"), index=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="")
    team: Mapped[str] = mapped_column(String(120), default="")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uci_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    pcs_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    time: Mapped[str] = mapped_column(String(20), default="")
    edition: Mapped[RaceEdition] = relationship(back_populates="results")
    rider: Mapped[Rider] = relationship(back_populates="race_results")
