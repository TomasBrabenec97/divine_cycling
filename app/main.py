import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import settings
from app.db import get_db, init_db
from app.models import (
    Event,
    EventResult,
    EventRider,
    Player,
    Prediction,
    PredictionItem,
    PredictionWildcard,
    Race,
    RaceEdition,
    RaceResult,
    Rider,
    ScoreLine,
    ScoreRun,
)
from app.schemas import (
    EventResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    PlayerCreate,
    PlayerResponse,
    PredictionPicks,
    PredictionResponse,
    PredictionUpsert,
    ResultUpsert,
    RiderResponse,
)
from app.scoring import (
    DEFAULT_RULES,
    position_multiplier,
    rules_snapshot,
    score_prediction,
    wildcard_multiplier,
)

app = FastAPI(title="divine. cycling API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)
# The rider reference set is a few hundred KB of JSON the page now loads on
# every visit; compressed it is a tenth of that.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.on_event("startup")
def initialize_database() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if settings.admin_api_key and not (
        x_admin_key and secrets.compare_digest(x_admin_key, settings.admin_api_key)
    ):
        raise HTTPException(status_code=401, detail="A valid admin key is required")


def active_event_or_404(db: Session) -> Event:
    event = db.scalar(select(Event).where(Event.status == "open").order_by(Event.starts_at))
    if event is None:
        raise HTTPException(status_code=404, detail="No active event is available")
    return event


def rider_team(rider: Rider) -> str | None:
    """The trade team from the PCS profile, else from the latest ranking snapshot."""
    if rider.profile is not None and rider.profile.team:
        return rider.profile.team
    latest = max(rider.rankings, key=lambda ranking: ranking.ranking_date, default=None)
    return latest.team if latest is not None and latest.team else None


def event_response(event: Event, db: Session) -> EventResponse:
    rows = db.scalars(
        select(EventRider)
        .options(
            joinedload(EventRider.rider).selectinload(Rider.profile),
            joinedload(EventRider.rider).selectinload(Rider.rankings),
        )
        .where(EventRider.event_id == event.id, EventRider.is_starter.is_(True))
        .order_by(EventRider.uci_rank)
    ).all()
    return EventResponse(
        id=event.id,
        slug=event.slug,
        name=event.name,
        starts_at=event.starts_at,
        prediction_deadline=event.prediction_deadline,
        status=event.status,
        source_name=event.source_name,
        source_updated_at=event.source_updated_at,
        riders=[
            RiderResponse(
                id=row.rider.id,
                name=f"{row.rider.first_name} {row.rider.last_name}",
                nation=row.rider.nation,
                uci_rank=row.uci_rank,
                uci_points=row.uci_points,
                team=rider_team(row.rider),
                position_multiplier=round(position_multiplier(row.uci_rank), 3),
                wildcard_multiplier=round(wildcard_multiplier(row.uci_rank), 3),
            )
            for row in rows
        ],
    )


def prediction_response(prediction: Prediction) -> PredictionResponse:
    return PredictionResponse(
        id=prediction.id,
        player_id=prediction.player_id,
        event_id=prediction.event_id,
        submitted_at=prediction.submitted_at,
        updated_at=prediction.updated_at,
        selections=[
            {"position": item.position, "rider_id": item.rider_id}
            for item in sorted(prediction.items, key=lambda item: item.position)
        ],
        wildcards=[wildcard.rider_id for wildcard in prediction.wildcards],
    )


@app.post("/api/players", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
def create_player(payload: PlayerCreate, db: Session = Depends(get_db)) -> Player:
    player = Player(username=payload.username)
    db.add(player)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That username is already taken") from None
    db.refresh(player)
    return player


@app.get("/api/players/by-username/{username}", response_model=PlayerResponse)
def get_player_by_username(username: str, db: Session = Depends(get_db)) -> Player:
    player = db.scalar(select(Player).where(Player.username == username.strip()))
    if player is None:
        raise HTTPException(status_code=404, detail="Username not found")
    return player


@app.get("/api/events/active", response_model=EventResponse)
def get_active_event(db: Session = Depends(get_db)) -> EventResponse:
    return event_response(active_event_or_404(db), db)


@app.get("/api/events/latest-finished", response_model=EventResponse)
def get_latest_finished_event(db: Session = Depends(get_db)) -> EventResponse:
    event = db.scalar(select(Event).where(Event.status == "finished").order_by(Event.starts_at.desc()))
    if event is None:
        raise HTTPException(status_code=404, detail="Leaderboard is available after the race is finalized")
    return event_response(event, db)


@app.get("/api/riders/reference")
def rider_reference_data(db: Session = Depends(get_db)) -> dict:
    """Read-only PCS background data, delivered once for the rider detail UI."""
    riders = db.scalars(
        select(Rider)
        .options(
            selectinload(Rider.profile),
            selectinload(Rider.seasons),
            selectinload(Rider.rankings),
            selectinload(Rider.race_results)
            .selectinload(RaceResult.edition)
            .selectinload(RaceEdition.race),
        )
        .order_by(Rider.last_name, Rider.first_name)
    ).unique().all()
    races = db.scalars(
        select(Race).options(selectinload(Race.editions)).order_by(Race.label)
    ).unique().all()

    return {
        "races": [
            {
                "key": race.key,
                "label": race.label,
                "editions": [
                    {"year": edition.year, "note": edition.note, "date": edition.race_date}
                    for edition in race.editions
                ],
            }
            for race in races
        ],
        "riders": [
            {
                "id": rider.id,
                "profile": None
                if rider.profile is None
                else {
                    "team": rider.profile.team,
                    "age": rider.profile.age,
                    "date_of_birth": rider.profile.date_of_birth,
                    "wins_total": rider.profile.wins_total,
                    "profile_url": rider.profile.profile_url,
                },
                "seasons": [
                    {
                        "season": season.season,
                        "pcs_points": season.pcs_points,
                        "racedays": season.racedays,
                        "wins": season.wins,
                        "top3s": season.top3s,
                        "top10s": season.top10s,
                    }
                    for season in rider.seasons
                ],
                "rankings": [
                    {
                        "date": ranking.ranking_date,
                        "uci_rank": ranking.uci_rank,
                        "uci_points": ranking.uci_points,
                        "team": ranking.team,
                    }
                    for ranking in rider.rankings
                ],
                "results": [
                    {
                        "race_key": result.edition.race.key,
                        "year": result.edition.year,
                        "position": result.position,
                        "status": result.status,
                    }
                    for result in rider.race_results
                    if result.edition is not None and result.edition.race is not None
                ],
            }
            for rider in riders
        ],
    }


def require_startlist_picks(event: Event, picks: PredictionPicks, db: Session) -> None:
    eligible_ids = set(
        db.scalars(
            select(EventRider.rider_id).where(
                EventRider.event_id == event.id, EventRider.is_starter.is_(True)
            )
        ).all()
    )
    requested_ids = {item.rider_id for item in picks.selections} | set(picks.wildcards)
    if not requested_ids.issubset(eligible_ids):
        raise HTTPException(
            status_code=422, detail="All selections must be riders on the startlist"
        )


@app.put("/api/events/{event_id}/predictions", response_model=PredictionResponse)
def upsert_prediction(
    event_id: int, payload: PredictionUpsert, db: Session = Depends(get_db)
) -> PredictionResponse:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != "open" or datetime.utcnow() >= event.prediction_deadline:
        raise HTTPException(status_code=409, detail="Predictions are locked for this event")
    if db.get(Player, payload.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    require_startlist_picks(event, payload, db)

    prediction = db.scalar(
        select(Prediction)
        .options(joinedload(Prediction.items), selectinload(Prediction.wildcards))
        .where(Prediction.player_id == payload.player_id, Prediction.event_id == event.id)
    )
    now = datetime.utcnow()
    if prediction is None:
        prediction = Prediction(
            player_id=payload.player_id, event_id=event.id, submitted_at=now, updated_at=now
        )
        db.add(prediction)
        db.flush()
    else:
        prediction.items.clear()
        prediction.wildcards.clear()
        prediction.updated_at = now
        db.flush()

    prediction.items.extend(
        PredictionItem(position=item.position, rider_id=item.rider_id)
        for item in payload.selections
    )
    prediction.wildcards.extend(
        PredictionWildcard(slot=slot, rider_id=rider_id)
        for slot, rider_id in enumerate(payload.wildcards, start=1)
    )
    # The v1 conviction boost is not part of scoring v2.
    prediction.boosted_rider_id = None
    db.commit()
    db.refresh(prediction)
    return prediction_response(prediction)


@app.get("/api/events/{event_id}/predictions/{player_id}", response_model=PredictionResponse)
def get_prediction(
    event_id: int, player_id: int, db: Session = Depends(get_db)
) -> PredictionResponse:
    prediction = db.scalar(
        select(Prediction)
        .options(joinedload(Prediction.items), selectinload(Prediction.wildcards))
        .where(Prediction.event_id == event_id, Prediction.player_id == player_id)
    )
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    return prediction_response(prediction)


def score_event(event: Event, db: Session, is_simulation: bool) -> LeaderboardResponse:
    results = db.scalars(select(EventResult).where(EventResult.event_id == event.id)).all()
    result_positions = {row.rider_id: row.position for row in results}
    ranks = {
        row.rider_id: row.uci_rank
        for row in db.scalars(select(EventRider).where(EventRider.event_id == event.id)).all()
    }
    predictions = db.execute(
        select(Prediction)
        .options(
            joinedload(Prediction.player),
            joinedload(Prediction.items),
            selectinload(Prediction.wildcards),
        )
        .where(Prediction.event_id == event.id)
    ).unique().scalars().all()
    entries = []
    for prediction in predictions:
        breakdown = score_prediction(
            [(item.position, item.rider_id) for item in prediction.items],
            [wildcard.rider_id for wildcard in prediction.wildcards],
            result_positions,
            ranks,
        )
        entries.append(LeaderboardEntry(username=prediction.player.username, **breakdown))
    entries.sort(key=lambda entry: (-entry.total_points, entry.username.lower()))
    return LeaderboardResponse(
        event_id=event.id,
        rules_version=DEFAULT_RULES.version,
        rules=rules_snapshot(),
        is_simulation=is_simulation,
        results=[
            {"position": row.position, "rider_id": row.rider_id}
            for row in sorted(results, key=lambda row: row.position)
        ],
        entries=entries,
    )


@app.get("/api/admin/overview", dependencies=[Depends(require_admin)])
def admin_overview(db: Session = Depends(get_db)) -> dict:
    events = db.scalars(select(Event).order_by(Event.starts_at.desc())).all()
    return {
        "rules": rules_snapshot(),
        "events": [
            {
                "id": event.id,
                "name": event.name,
                "status": event.status,
                "players_joined": len(db.scalars(select(Player)).all()),
                "submitted": len(db.scalars(select(Prediction).where(Prediction.event_id == event.id)).all()),
                "results_entered": len(db.scalars(select(EventResult).where(EventResult.event_id == event.id)).all()),
            }
            for event in events
        ],
    }


@app.get(
    "/api/admin/events/{event_id}",
    response_model=EventResponse,
    dependencies=[Depends(require_admin)],
)
def admin_event(event_id: int, db: Session = Depends(get_db)) -> EventResponse:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event_response(event, db)


@app.post(
    "/api/admin/events/{event_id}/simulate",
    response_model=LeaderboardResponse,
    dependencies=[Depends(require_admin)],
)
def simulate_results(event_id: int, payload: ResultUpsert, db: Session = Depends(get_db)) -> LeaderboardResponse:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    eligible_ids = set(db.scalars(select(EventRider.rider_id).where(EventRider.event_id == event_id)).all())
    if not {item.rider_id for item in payload.results}.issubset(eligible_ids):
        raise HTTPException(status_code=422, detail="Results must use riders on the event startlist")
    db.execute(delete(EventResult).where(EventResult.event_id == event_id))
    db.add_all(EventResult(event_id=event_id, rider_id=item.rider_id, position=item.position) for item in payload.results)
    db.flush()
    response = score_event(event, db, is_simulation=True)
    db.rollback()
    return response


@app.post(
    "/api/admin/events/{event_id}/results",
    response_model=LeaderboardResponse,
    dependencies=[Depends(require_admin)],
)
def publish_results(event_id: int, payload: ResultUpsert, db: Session = Depends(get_db)) -> LeaderboardResponse:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    eligible_ids = set(db.scalars(select(EventRider.rider_id).where(EventRider.event_id == event_id)).all())
    if not {item.rider_id for item in payload.results}.issubset(eligible_ids):
        raise HTTPException(status_code=422, detail="Results must use riders on the event startlist")
    db.execute(delete(EventResult).where(EventResult.event_id == event_id))
    db.add_all(EventResult(event_id=event_id, rider_id=item.rider_id, position=item.position) for item in payload.results)
    event.status = "finished"
    db.flush()
    response = score_event(event, db, is_simulation=False)
    run = ScoreRun(event_id=event_id, rules_version=DEFAULT_RULES.version, rules_json=json.dumps(rules_snapshot()), is_simulation=False)
    db.add(run)
    db.flush()
    predictions_by_username = {
        prediction.player.username: prediction
        for prediction in db.scalars(
            select(Prediction).options(joinedload(Prediction.player)).where(Prediction.event_id == event_id)
        ).all()
    }
    for entry in response.entries:
        prediction = predictions_by_username[entry.username]
        db.add(
            ScoreLine(
                score_run_id=run.id,
                prediction_id=prediction.id,
                total_points=entry.total_points,
                breakdown_json=entry.model_dump_json(),
            )
        )
    db.commit()
    return response


def persisted_leaderboard(event: Event, db: Session) -> LeaderboardResponse | None:
    """The scores stored when the result was published, if they use this model.

    Serving the stored run keeps a finished leaderboard stable when the rules
    or the UCI ranking change later.
    """
    run = db.scalar(
        select(ScoreRun)
        .where(ScoreRun.event_id == event.id, ScoreRun.is_simulation.is_(False))
        .order_by(ScoreRun.created_at.desc(), ScoreRun.id.desc())
    )
    if run is None or not run.rules_version.startswith("v2"):
        return None
    lines = db.scalars(select(ScoreLine).where(ScoreLine.score_run_id == run.id)).all()
    entries = [LeaderboardEntry.model_validate_json(line.breakdown_json) for line in lines]
    entries.sort(key=lambda entry: (-entry.total_points, entry.username.lower()))
    results = db.scalars(
        select(EventResult).where(EventResult.event_id == event.id).order_by(EventResult.position)
    ).all()
    return LeaderboardResponse(
        event_id=event.id,
        rules_version=run.rules_version,
        rules=json.loads(run.rules_json),
        is_simulation=False,
        results=[{"position": row.position, "rider_id": row.rider_id} for row in results],
        entries=entries,
    )


@app.get("/api/events/{event_id}/leaderboard", response_model=LeaderboardResponse)
def leaderboard(event_id: int, db: Session = Depends(get_db)) -> LeaderboardResponse:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != "finished":
        raise HTTPException(status_code=409, detail="Leaderboard is available after the race is finalized")
    return persisted_leaderboard(event, db) or score_event(event, db, is_simulation=False)


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(frontend_dir / "admin.html")


@app.get("/leaderboard", include_in_schema=False)
def leaderboard_page() -> FileResponse:
    return FileResponse(frontend_dir / "leaderboard.html")


if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
