import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import settings
from app.db import get_db, init_db
from app.models import (
    Event,
    EventResult,
    EventRider,
    FavouriteRider,
    LeagueMembership,
    LocalLeague,
    Player,
    Prediction,
    PredictionItem,
    PredictionTemplate,
    PredictionWildcard,
    Race,
    RaceEdition,
    RaceResult,
    Rider,
    ScoreLine,
    ScoreRun,
)
from app.response_cache import ResponseCache
from app.schemas import (
    AdminDeletePlayer,
    EventResponse,
    FavouritesUpdate,
    LeaderboardEntry,
    LeaderboardResponse,
    LeagueJoin,
    LeagueResponse,
    LeagueStatus,
    PicksBase,
    PlayerCreate,
    PlayerResponse,
    PredictionResponse,
    PredictionUpsert,
    ResultUpsert,
    RiderResponse,
    TemplateOrder,
    TemplateResponse,
    TemplateUpsert,
)
from app.scoring import (
    DEFAULT_RULES,
    STORED_BREAKDOWN_VERSIONS,
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)
# The startlist is tens of KB of JSON on every visit; compressed it is a fifth
# of that. The cached payloads below arrive already compressed and pass through.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# The reference set only changes when scripts.load_pcs_data runs, normally as
# part of a deploy; the lifetime also bounds how stale it is after a loader run
# against a server that keeps running.
REFERENCE_TTL_SECONDS = 300
reference_cache = ResponseCache(ttl_seconds=REFERENCE_TTL_SECONDS)
# A published score run never changes, so its leaderboard is kept until a newer
# run replaces it; browsers recheck it every minute in case the result is re-published.
LEADERBOARD_MAX_AGE_SECONDS = 60
leaderboard_cache = ResponseCache()


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


def local_league_or_404(db: Session, event_id: int, code: str) -> LocalLeague:
    league = db.scalar(
        select(LocalLeague).where(
            LocalLeague.event_id == event_id, LocalLeague.code == code.strip().lower()
        )
    )
    if league is None:
        raise HTTPException(status_code=404, detail="League code not found")
    return league


def player_membership(db: Session, event_id: int, player_id: int) -> LeagueMembership | None:
    return db.scalar(
        select(LeagueMembership)
        .options(joinedload(LeagueMembership.league))
        .where(LeagueMembership.event_id == event_id, LeagueMembership.player_id == player_id)
    )


def league_response(db: Session, league: LocalLeague) -> LeagueResponse:
    joined = db.scalar(
        select(func.count()).select_from(LeagueMembership).where(
            LeagueMembership.league_id == league.id
        )
    ) or 0
    submitted = db.scalar(
        select(func.count()).select_from(LeagueMembership)
        .join(
            Prediction,
            (Prediction.player_id == LeagueMembership.player_id)
            & (Prediction.event_id == LeagueMembership.event_id),
        )
        .where(LeagueMembership.league_id == league.id)
    ) or 0
    return LeagueResponse(
        code=league.code,
        submission_deadline=league.submission_deadline or league.event.prediction_deadline,
        joined_players=joined,
        submitted_players=submitted,
    )


@app.get("/api/events/{event_id}/leagues/{code}", response_model=LeagueResponse)
def get_local_league(
    event_id: int, code: str, db: Session = Depends(get_db)
) -> LeagueResponse:
    return league_response(db, local_league_or_404(db, event_id, code))


@app.get("/api/events/{event_id}/players/{player_id}/league", response_model=LeagueStatus)
def get_player_league(
    event_id: int, player_id: int, db: Session = Depends(get_db)
) -> LeagueStatus:
    if db.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    membership = player_membership(db, event_id, player_id)
    return LeagueStatus(league=league_response(db, membership.league) if membership else None)


@app.put("/api/events/{event_id}/players/{player_id}/league", response_model=LeagueStatus)
def join_local_league(
    event_id: int, player_id: int, payload: LeagueJoin, db: Session = Depends(get_db)
) -> LeagueStatus:
    if db.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    league = local_league_or_404(db, event_id, payload.code)
    membership = player_membership(db, event_id, player_id)
    if membership is None:
        db.add(LeagueMembership(event_id=event_id, player_id=player_id, league_id=league.id))
    else:
        membership.league_id = league.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="League membership changed; try again") from None
    return LeagueStatus(league=league_response(db, league))


@app.delete(
    "/api/events/{event_id}/players/{player_id}/league", status_code=status.HTTP_204_NO_CONTENT
)
def leave_local_league(
    event_id: int, player_id: int, db: Session = Depends(get_db)
) -> None:
    membership = player_membership(db, event_id, player_id)
    if membership is not None:
        db.delete(membership)
        db.commit()


@app.get("/api/riders/reference")
def rider_reference_data(request: Request, db: Session = Depends(get_db)) -> Response:
    """Read-only PCS background data, delivered once for the rider detail UI."""
    payload = reference_cache.get("riders", None, lambda: rider_reference_payload(db))
    return payload.response(request, max_age=REFERENCE_TTL_SECONDS)


def rider_reference_payload(db: Session) -> dict:
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


def require_open_event(event_id: int, db: Session, player_id: int | None = None) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    membership = player_membership(db, event_id, player_id) if player_id is not None else None
    deadline = event.prediction_deadline
    if membership and membership.league.submission_deadline:
        deadline = membership.league.submission_deadline
    if event.status != "open" or datetime.utcnow() >= deadline:
        raise HTTPException(status_code=409, detail="Predictions are locked for this event")
    return event


def require_startlist_picks(event: Event, picks: PicksBase, db: Session) -> None:
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
    event = require_open_event(event_id, db, payload.player_id)
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


# -- templates: named draft lists next to the final prediction -------------

MAX_TEMPLATES = 12


def template_response(template: PredictionTemplate) -> TemplateResponse:
    picks = json.loads(template.picks_json)
    return TemplateResponse(
        id=template.id,
        name=template.name,
        selections=picks.get("selections", []),
        wildcards=picks.get("wildcards", []),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def template_picks_json(payload: TemplateUpsert) -> str:
    return json.dumps(
        {
            "selections": [
                item.model_dump() for item in sorted(payload.selections, key=lambda i: i.position)
            ],
            "wildcards": payload.wildcards,
        }
    )


def require_unique_template_name(
    db: Session, event_id: int, player_id: int, name: str, template_id: int | None = None
) -> None:
    names = db.execute(
        select(PredictionTemplate.id, PredictionTemplate.name).where(
            PredictionTemplate.event_id == event_id, PredictionTemplate.player_id == player_id
        )
    ).all()
    if any(row.name.casefold() == name.casefold() and row.id != template_id for row in names):
        raise HTTPException(status_code=409, detail=f"You already have a list called {name}")


def owned_template(
    db: Session, event_id: int, template_id: int, player_id: int
) -> PredictionTemplate:
    template = db.get(PredictionTemplate, template_id)
    if template is None or template.event_id != event_id or template.player_id != player_id:
        raise HTTPException(status_code=404, detail="List not found")
    return template


@app.get(
    "/api/events/{event_id}/players/{player_id}/templates", response_model=list[TemplateResponse]
)
def list_templates(
    event_id: int, player_id: int, db: Session = Depends(get_db)
) -> list[TemplateResponse]:
    templates = db.scalars(
        select(PredictionTemplate)
        .where(PredictionTemplate.event_id == event_id, PredictionTemplate.player_id == player_id)
        .order_by(PredictionTemplate.sort_order, PredictionTemplate.created_at, PredictionTemplate.id)
    ).all()
    return [template_response(template) for template in templates]


@app.put(
    "/api/events/{event_id}/players/{player_id}/templates/order",
    response_model=list[TemplateResponse],
)
def reorder_templates(
    event_id: int, player_id: int, payload: TemplateOrder, db: Session = Depends(get_db)
) -> list[TemplateResponse]:
    # The order carries no picks, so like favourites it is not bound to the deadline.
    if db.get(Event, event_id) is None:
        raise HTTPException(status_code=404, detail="Event not found")
    templates = {
        template.id: template
        for template in db.scalars(
            select(PredictionTemplate).where(
                PredictionTemplate.event_id == event_id, PredictionTemplate.player_id == player_id
            )
        )
    }
    if sorted(payload.template_ids) != sorted(templates):
        raise HTTPException(status_code=409, detail="Your lists changed meanwhile; reload the page")
    for sort_order, template_id in enumerate(payload.template_ids):
        templates[template_id].sort_order = sort_order
    db.commit()
    return [template_response(templates[template_id]) for template_id in payload.template_ids]


@app.post(
    "/api/events/{event_id}/templates",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    event_id: int, payload: TemplateUpsert, db: Session = Depends(get_db)
) -> TemplateResponse:
    event = require_open_event(event_id, db, payload.player_id)
    if db.get(Player, payload.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    require_startlist_picks(event, payload, db)
    existing = db.scalars(
        select(PredictionTemplate.sort_order).where(
            PredictionTemplate.event_id == event_id,
            PredictionTemplate.player_id == payload.player_id,
        )
    ).all()
    if len(existing) >= MAX_TEMPLATES:
        raise HTTPException(status_code=409, detail=f"You can keep up to {MAX_TEMPLATES} lists")
    require_unique_template_name(db, event_id, payload.player_id, payload.name)
    now = datetime.utcnow()
    template = PredictionTemplate(
        player_id=payload.player_id,
        event_id=event_id,
        name=payload.name,
        picks_json=template_picks_json(payload),
        # A new list opens as the last tab.
        sort_order=max(existing, default=-1) + 1,
        created_at=now,
        updated_at=now,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template_response(template)


@app.put("/api/events/{event_id}/templates/{template_id}", response_model=TemplateResponse)
def update_template(
    event_id: int, template_id: int, payload: TemplateUpsert, db: Session = Depends(get_db)
) -> TemplateResponse:
    event = require_open_event(event_id, db, payload.player_id)
    template = owned_template(db, event_id, template_id, payload.player_id)
    require_startlist_picks(event, payload, db)
    require_unique_template_name(db, event_id, payload.player_id, payload.name, template_id)
    template.name = payload.name
    template.picks_json = template_picks_json(payload)
    template.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(template)
    return template_response(template)


@app.delete(
    "/api/events/{event_id}/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_template(
    event_id: int, template_id: int, player_id: int, db: Session = Depends(get_db)
) -> None:
    require_open_event(event_id, db, player_id)
    db.delete(owned_template(db, event_id, template_id, player_id))
    db.commit()


# -- favourites: riders a player hearts to narrow the pool ---------------------


def require_player_and_starters(
    db: Session, event_id: int, player_id: int, rider_ids: set[int]
) -> None:
    if db.get(Event, event_id) is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if db.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    if not rider_ids:
        return
    starters = set(
        db.scalars(
            select(EventRider.rider_id).where(
                EventRider.event_id == event_id,
                EventRider.rider_id.in_(rider_ids),
                EventRider.is_starter.is_(True),
            )
        ).all()
    )
    if starters != rider_ids:
        raise HTTPException(status_code=422, detail="Favourites must be riders on the startlist")


def require_player_and_starter(db: Session, event_id: int, player_id: int, rider_id: int) -> None:
    require_player_and_starters(db, event_id, player_id, {rider_id})


@app.get("/api/events/{event_id}/players/{player_id}/favourites", response_model=list[int])
def list_favourites(
    event_id: int, player_id: int, db: Session = Depends(get_db)
) -> list[int]:
    return list(
        db.scalars(
            select(FavouriteRider.rider_id)
            .where(FavouriteRider.event_id == event_id, FavouriteRider.player_id == player_id)
            .order_by(FavouriteRider.created_at, FavouriteRider.id)
        ).all()
    )


@app.put(
    "/api/events/{event_id}/players/{player_id}/favourites/{rider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def add_favourite(
    event_id: int, player_id: int, rider_id: int, db: Session = Depends(get_db)
) -> None:
    require_player_and_starter(db, event_id, player_id, rider_id)
    exists = db.scalar(
        select(FavouriteRider.id).where(
            FavouriteRider.event_id == event_id,
            FavouriteRider.player_id == player_id,
            FavouriteRider.rider_id == rider_id,
        )
    )
    if exists is None:
        db.add(FavouriteRider(event_id=event_id, player_id=player_id, rider_id=rider_id))
        try:
            db.commit()
        except IntegrityError:
            # A double tap raced itself; the favourite is there either way.
            db.rollback()


@app.patch("/api/events/{event_id}/players/{player_id}/favourites", response_model=list[int])
def update_favourites(
    event_id: int, player_id: int, payload: FavouritesUpdate, db: Session = Depends(get_db)
) -> list[int]:
    """Heart and un-heart many riders at once; returns the whole list afterwards."""
    require_player_and_starters(db, event_id, player_id, set(payload.add))
    if payload.remove:
        db.execute(
            delete(FavouriteRider).where(
                FavouriteRider.event_id == event_id,
                FavouriteRider.player_id == player_id,
                FavouriteRider.rider_id.in_(payload.remove),
            )
        )
    existing = set(list_favourites(event_id, player_id, db))
    db.add_all(
        FavouriteRider(event_id=event_id, player_id=player_id, rider_id=rider_id)
        for rider_id in dict.fromkeys(payload.add)
        if rider_id not in existing
    )
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request hearted some of them first; they are there either way.
        db.rollback()
    return list_favourites(event_id, player_id, db)


@app.delete(
    "/api/events/{event_id}/players/{player_id}/favourites",
    status_code=status.HTTP_204_NO_CONTENT,
)
def clear_favourites(event_id: int, player_id: int, db: Session = Depends(get_db)) -> None:
    db.execute(
        delete(FavouriteRider).where(
            FavouriteRider.event_id == event_id, FavouriteRider.player_id == player_id
        )
    )
    db.commit()


@app.delete(
    "/api/events/{event_id}/players/{player_id}/favourites/{rider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_favourite(
    event_id: int, player_id: int, rider_id: int, db: Session = Depends(get_db)
) -> None:
    db.execute(
        delete(FavouriteRider).where(
            FavouriteRider.event_id == event_id,
            FavouriteRider.player_id == player_id,
            FavouriteRider.rider_id == rider_id,
        )
    )
    db.commit()


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
                "slug": event.slug,
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


@app.get("/api/admin/events/{event_id}/players", dependencies=[Depends(require_admin)])
def admin_players(event_id: int, db: Session = Depends(get_db)) -> dict:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    predictions = {
        row.player_id: row
        for row in db.scalars(select(Prediction).where(Prediction.event_id == event_id)).all()
    }
    memberships = {
        row.player_id: row.league.code
        for row in db.scalars(
            select(LeagueMembership)
            .options(joinedload(LeagueMembership.league))
            .where(LeagueMembership.event_id == event_id)
        ).all()
    }
    players = [
        {
            "id": player.id,
            "username": player.username,
            "submitted_flag": player.id in predictions,
            "last_edit": predictions[player.id].updated_at if player.id in predictions else None,
            "league_code": memberships.get(player.id),
        }
        for player in db.scalars(select(Player).order_by(Player.username)).all()
    ]
    leagues = [
        league_response(db, league).model_dump(mode="json")
        for league in db.scalars(
            select(LocalLeague).where(LocalLeague.event_id == event_id).order_by(LocalLeague.code)
        ).all()
    ]
    return {"event_id": event_id, "players": players, "leagues": leagues}


@app.post("/api/admin/players/{player_id}/delete", dependencies=[Depends(require_admin)])
def admin_delete_player(
    player_id: int, payload: AdminDeletePlayer, db: Session = Depends(get_db)
) -> dict[str, str]:
    if payload.confirmation != "DELETE":
        raise HTTPException(status_code=422, detail="Type DELETE to confirm")
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    prediction_ids = select(Prediction.id).where(Prediction.player_id == player_id)
    db.execute(delete(ScoreLine).where(ScoreLine.prediction_id.in_(prediction_ids)))
    db.execute(delete(PredictionItem).where(PredictionItem.prediction_id.in_(prediction_ids)))
    db.execute(delete(PredictionWildcard).where(PredictionWildcard.prediction_id.in_(prediction_ids)))
    db.execute(delete(Prediction).where(Prediction.player_id == player_id))
    db.execute(delete(PredictionTemplate).where(PredictionTemplate.player_id == player_id))
    db.execute(delete(FavouriteRider).where(FavouriteRider.player_id == player_id))
    db.execute(delete(LeagueMembership).where(LeagueMembership.player_id == player_id))
    db.delete(player)
    db.commit()
    leaderboard_cache.clear()
    return {"deleted": player.username}


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


def latest_published_run(event: Event, db: Session) -> ScoreRun | None:
    return db.scalar(
        select(ScoreRun)
        .where(ScoreRun.event_id == event.id, ScoreRun.is_simulation.is_(False))
        .order_by(ScoreRun.created_at.desc(), ScoreRun.id.desc())
    )


def persisted_leaderboard(event: Event, run: ScoreRun, db: Session) -> LeaderboardResponse:
    """The scores stored when the result was published.

    Serving the stored run keeps a finished leaderboard stable when the rules
    or the UCI ranking change later.
    """
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
def leaderboard(
    event_id: int, request: Request, league_code: str | None = None, db: Session = Depends(get_db)
) -> Response | LeaderboardResponse:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != "finished":
        raise HTTPException(status_code=409, detail="Leaderboard is available after the race is finalized")
    league = local_league_or_404(db, event_id, league_code) if league_code else None
    run = latest_published_run(event, db)
    if run is None or not run.rules_version.startswith(STORED_BREAKDOWN_VERSIONS):
        board = score_event(event, db, is_simulation=False)
    elif league is not None:
        board = persisted_leaderboard(event, run, db)
    else:
        # The timestamp keeps a rebuilt database that reuses a run id from being
        # served the board of the run it replaced.
        payload = leaderboard_cache.get(
            event.id, (run.id, run.created_at), lambda: persisted_leaderboard(event, run, db)
        )
        return payload.response(request, max_age=LEADERBOARD_MAX_AGE_SECONDS)
    if league is None:
        return board
    usernames = set(
        db.scalars(
            select(Player.username)
            .join(LeagueMembership, LeagueMembership.player_id == Player.id)
            .where(LeagueMembership.league_id == league.id)
        ).all()
    )
    return board.model_copy(update={"entries": [entry for entry in board.entries if entry.username in usernames]})


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(frontend_dir / "admin.html")


@app.get("/leaderboard", include_in_schema=False)
def leaderboard_page() -> FileResponse:
    return FileResponse(frontend_dir / "leaderboard.html")


@app.get("/join_league", include_in_schema=False)
def join_league_page() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
