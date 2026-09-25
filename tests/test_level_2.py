from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as db_module
import app.seed as seed_module
from app.db import Base
from app.main import app, leaderboard_cache, reference_cache
from app.models import (
    Event,
    FavouriteRider,
    LeagueMembership,
    LocalLeague,
    Prediction,
    PredictionTemplate,
    ScoreLine,
)
from app.seed import seed_mock_data


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.sqlite3'}", connect_args={"check_same_thread": False}
    )
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    monkeypatch.setattr(seed_module, "SessionLocal", session_local)
    Base.metadata.create_all(bind=engine)
    seed_mock_data()
    # The payload caches outlive a request, so they must not carry one test's
    # database into the next.
    reference_cache.clear()
    leaderboard_cache.clear()
    yield
    engine.dispose()


def test_register_get_event_and_save_prediction() -> None:
    client = TestClient(app)
    player = client.post("/api/players", json={"username": "Tomas_1"})
    assert player.status_code == 201
    event = client.get("/api/events/active")
    assert event.status_code == 200
    riders = event.json()["riders"]
    assert len(riders) == 20
    payload = {
        "player_id": player.json()["id"],
        "selections": [
            {"position": index + 1, "rider_id": rider["id"]}
            for index, rider in enumerate(riders[:10])
        ],
    }
    saved = client.put(f"/api/events/{event.json()['id']}/predictions", json=payload)
    assert saved.status_code == 200
    assert len(saved.json()["selections"]) == 10


def test_duplicate_username_and_duplicate_rider_are_rejected() -> None:
    client = TestClient(app)
    assert client.post("/api/players", json={"username": "Tomas"}).status_code == 201
    assert client.post("/api/players", json={"username": "Tomas"}).status_code == 409
    event = client.get("/api/events/active").json()
    player_id = client.post("/api/players", json={"username": "Alex"}).json()["id"]
    selections = [
        {"position": index + 1, "rider_id": event["riders"][0]["id"]} for index in range(10)
    ]
    response = client.put(
        f"/api/events/{event['id']}/predictions",
        json={"player_id": player_id, "selections": selections},
    )
    assert response.status_code == 422


def test_existing_username_can_be_loaded_for_a_returning_player() -> None:
    client = TestClient(app)
    created = client.post("/api/players", json={"username": "Tomas"})
    loaded = client.get("/api/players/by-username/Tomas")
    assert loaded.status_code == 200
    assert loaded.json() == created.json()


def test_partial_prediction_can_be_saved() -> None:
    client = TestClient(app)
    player_id = client.post("/api/players", json={"username": "Sam"}).json()["id"]
    event = client.get("/api/events/active").json()
    response = client.put(
        f"/api/events/{event['id']}/predictions",
        json={
            "player_id": player_id,
            "selections": [{"position": 4, "rider_id": event["riders"][0]["id"]}],
        },
    )
    assert response.status_code == 200
    assert response.json()["selections"] == [{"position": 4, "rider_id": event["riders"][0]["id"]}]


def test_result_simulation_scores_without_persisting_results() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    rider_ids = [rider["id"] for rider in event["riders"][:10]]
    player_id = client.post("/api/players", json={"username": "Simulator"}).json()["id"]
    saved = client.put(
        f"/api/events/{event['id']}/predictions",
        json={
            "player_id": player_id,
            "selections": [
                {"position": position, "rider_id": rider_id}
                for position, rider_id in enumerate(rider_ids, start=1)
            ],
        },
    )
    assert saved.status_code == 200

    preview = client.post(
        f"/api/admin/events/{event['id']}/simulate",
        json={
            "results": [
                {"position": position, "rider_id": rider_id}
                for position, rider_id in enumerate(rider_ids, start=1)
            ]
        },
    )

    assert preview.status_code == 200
    assert preview.json()["is_simulation"] is True
    assert preview.json()["entries"][0]["total_points"] > 0
    assert client.get(f"/api/events/{event['id']}/leaderboard").status_code == 409


def test_event_riders_carry_their_trade_team() -> None:
    from datetime import date

    from app.models import RiderProfile, RiderRanking

    client = TestClient(app)
    riders = client.get("/api/events/active").json()["riders"]
    profiled, ranked_only = riders[0]["id"], riders[1]["id"]
    with db_module.SessionLocal() as session:
        session.add_all(
            [
                RiderProfile(rider_id=profiled, team="Team Profile", profile_url="https://pcs/x"),
                RiderRanking(rider_id=ranked_only, ranking_date=date(2025, 12, 30), team="Old"),
                RiderRanking(rider_id=ranked_only, ranking_date=date(2026, 9, 22), team="New"),
            ]
        )
        session.commit()

    event = client.get("/api/events/active").json()
    teams = {rider["id"]: rider["team"] for rider in event["riders"]}
    assert teams[profiled] == "Team Profile"
    assert teams[ranked_only] == "New"
    assert teams[riders[2]["id"]] is None

    reference = client.get("/api/riders/reference").json()
    profile = next(rider for rider in reference["riders"] if rider["id"] == profiled)["profile"]
    assert profile["profile_url"] == "https://pcs/x"


def save_prediction(client: TestClient, event: dict, username: str, top10: list[int], wildcards=()):
    player_id = client.post("/api/players", json={"username": username}).json()["id"]
    return player_id, client.put(
        f"/api/events/{event['id']}/predictions",
        json={
            "player_id": player_id,
            "selections": [
                {"position": position, "rider_id": rider_id}
                for position, rider_id in enumerate(top10, start=1)
            ],
            "wildcards": list(wildcards),
        },
    )


def test_wildcards_are_saved_and_must_stand_apart_from_the_top_ten() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]

    player_id, saved = save_prediction(client, event, "Wild", ids[:10], ids[10:13])
    assert saved.status_code == 200
    assert saved.json()["wildcards"] == ids[10:13]
    loaded = client.get(f"/api/events/{event['id']}/predictions/{player_id}").json()
    assert loaded["wildcards"] == ids[10:13]

    _, overlap = save_prediction(client, event, "Overlap", ids[:10], [ids[0]])
    assert overlap.status_code == 422
    _, twice = save_prediction(client, event, "Twice", ids[:10], [ids[10], ids[10]])
    assert twice.status_code == 422
    _, too_many = save_prediction(client, event, "Many", ids[:10], ids[10:14])
    assert too_many.status_code == 422
    _, unknown = save_prediction(client, event, "Unknown", ids[:10], [999_999])
    assert unknown.status_code == 422


def test_published_scores_are_stored_and_served_unchanged() -> None:
    import app.main as main_module

    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]
    save_prediction(client, event, "Keeper", ids[:10], ids[15:18])
    results = {
        "results": [
            {"position": position, "rider_id": rider_id}
            for position, rider_id in enumerate([ids[1], ids[0], ids[15], *ids[2:9]], start=1)
        ]
    }

    published = client.post(f"/api/admin/events/{event['id']}/results", json=results)
    assert published.status_code == 200
    entry = published.json()["entries"][0]
    assert entry["wildcards"][0]["actual_position"] == 3
    assert entry["total_points"] == pytest.approx(
        entry["placement_points"] + entry["permutation_points"] + entry["wildcard_points"], abs=0.01
    )

    # A later ranking refresh must not move a finished leaderboard.
    def fail(*args, **kwargs):
        raise AssertionError("a finished leaderboard was recomputed")

    original = main_module.score_event
    main_module.score_event = fail
    try:
        board = client.get(f"/api/events/{event['id']}/leaderboard")
    finally:
        main_module.score_event = original
    assert board.status_code == 200
    assert board.json()["entries"][0] == entry
    assert board.json()["rules"]["version"] == "v3.0"


def test_templates_are_named_drafts_kept_apart_from_the_final_prediction() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]
    player_id = client.post("/api/players", json={"username": "Drafter"}).json()["id"]
    base = f"/api/events/{event['id']}/templates"

    empty = client.post(base, json={"player_id": player_id, "name": "  Blank   list "})
    assert empty.status_code == 201
    assert empty.json()["name"] == "Blank list"
    assert empty.json()["selections"] == []

    picks = [{"position": 3, "rider_id": ids[0]}, {"position": 1, "rider_id": ids[1]}]
    created = client.post(
        base, json={"player_id": player_id, "name": "Sprint", "selections": picks, "wildcards": [ids[5]]}
    )
    assert created.status_code == 201
    template = created.json()
    assert [item["position"] for item in template["selections"]] == [1, 3]
    assert template["wildcards"] == [ids[5]]

    duplicate = client.post(base, json={"player_id": player_id, "name": "sprint"})
    assert duplicate.status_code == 409
    overlap = client.post(
        base, json={"player_id": player_id, "name": "Bad", "selections": picks, "wildcards": [ids[0]]}
    )
    assert overlap.status_code == 422

    renamed = client.put(
        f"{base}/{template['id']}",
        json={"player_id": player_id, "name": "Sprint finish", "selections": picks, "wildcards": []},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Sprint finish"
    assert renamed.json()["wildcards"] == []

    stranger = client.post("/api/players", json={"username": "Stranger"}).json()["id"]
    foreign = client.put(f"{base}/{template['id']}", json={"player_id": stranger, "name": "Mine"})
    assert foreign.status_code == 404

    listed = client.get(f"/api/events/{event['id']}/players/{player_id}/templates").json()
    assert [item["name"] for item in listed] == ["Blank list", "Sprint finish"]
    assert client.get(f"/api/events/{event['id']}/predictions/{player_id}").status_code == 404

    assert client.delete(f"{base}/{template['id']}?player_id={stranger}").status_code == 404
    assert client.delete(f"{base}/{template['id']}?player_id={player_id}").status_code == 204
    remaining = client.get(f"/api/events/{event['id']}/players/{player_id}/templates").json()
    assert [item["name"] for item in remaining] == ["Blank list"]


def test_templates_keep_the_order_their_player_gives_them() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    player_id = client.post("/api/players", json={"username": "Sorter"}).json()["id"]
    stranger = client.post("/api/players", json={"username": "Nosy"}).json()["id"]
    base = f"/api/events/{event['id']}/templates"
    listing = f"/api/events/{event['id']}/players/{player_id}/templates"
    ids = [
        client.post(base, json={"player_id": player_id, "name": name}).json()["id"]
        for name in ("A", "B", "C")
    ]
    client.post(base, json={"player_id": stranger, "name": "Theirs"})

    reordered = client.put(f"{listing}/order", json={"template_ids": [ids[2], ids[0], ids[1]]})
    assert reordered.status_code == 200
    assert [item["name"] for item in reordered.json()] == ["C", "A", "B"]
    assert [item["name"] for item in client.get(listing).json()] == ["C", "A", "B"]

    # A new list opens as the last tab.
    client.post(base, json={"player_id": player_id, "name": "D"})
    assert [item["name"] for item in client.get(listing).json()] == ["C", "A", "B", "D"]

    # The order must name every one of the player's lists and nobody else's.
    assert client.put(f"{listing}/order", json={"template_ids": ids}).status_code == 409
    theirs = client.get(f"/api/events/{event['id']}/players/{stranger}/templates").json()[0]["id"]
    everything = [item["id"] for item in client.get(listing).json()]
    assert (
        client.put(f"{listing}/order", json={"template_ids": [*everything[:-1], theirs]}).status_code
        == 409
    )
    assert [item["name"] for item in client.get(listing).json()] == ["C", "A", "B", "D"]


def test_init_db_adds_the_template_order_to_an_existing_table(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE prediction_templates (id INTEGER PRIMARY KEY, player_id INTEGER,"
            " event_id INTEGER, name VARCHAR(40), picks_json TEXT, created_at DATETIME,"
            " updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO prediction_templates (player_id, event_id, name, picks_json)"
            " VALUES (1, 1, 'Old', '{}')"
        )
    monkeypatch.setattr(db_module, "engine", engine)
    db_module.init_db()
    db_module.init_db()
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT name, sort_order FROM prediction_templates").all()
    assert [tuple(row) for row in rows] == [("Old", 0)]
    engine.dispose()


def test_a_player_keeps_a_bounded_number_of_templates() -> None:
    import app.main as main_module

    client = TestClient(app)
    event = client.get("/api/events/active").json()
    player_id = client.post("/api/players", json={"username": "Hoarder"}).json()["id"]
    base = f"/api/events/{event['id']}/templates"
    for index in range(main_module.MAX_TEMPLATES):
        created = client.post(base, json={"player_id": player_id, "name": f"List {index}"})
        assert created.status_code == 201
    assert client.post(base, json={"player_id": player_id, "name": "One more"}).status_code == 409


def test_favourites_are_kept_per_player_and_toggle_idempotently() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]
    player_id = client.post("/api/players", json={"username": "Fan"}).json()["id"]
    other_id = client.post("/api/players", json={"username": "Other"}).json()["id"]
    base = f"/api/events/{event['id']}/players"

    for rider_id in (ids[3], ids[1], ids[3]):
        assert client.put(f"{base}/{player_id}/favourites/{rider_id}").status_code == 204
    assert client.get(f"{base}/{player_id}/favourites").json() == [ids[3], ids[1]]
    assert client.get(f"{base}/{other_id}/favourites").json() == []

    assert client.delete(f"{base}/{player_id}/favourites/{ids[3]}").status_code == 204
    assert client.delete(f"{base}/{player_id}/favourites/{ids[3]}").status_code == 204
    assert client.get(f"{base}/{player_id}/favourites").json() == [ids[1]]

    assert client.put(f"{base}/{player_id}/favourites/999999").status_code == 422
    assert client.put(f"{base}/999999/favourites/{ids[0]}").status_code == 404


def test_favourites_can_be_cleared_in_one_call() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]
    player_id = client.post("/api/players", json={"username": "Clearer"}).json()["id"]
    other_id = client.post("/api/players", json={"username": "Keeper"}).json()["id"]
    base = f"/api/events/{event['id']}/players"
    for rider_id in ids[:3]:
        client.put(f"{base}/{player_id}/favourites/{rider_id}")
    client.put(f"{base}/{other_id}/favourites/{ids[0]}")

    assert client.delete(f"{base}/{player_id}/favourites").status_code == 204
    assert client.get(f"{base}/{player_id}/favourites").json() == []
    assert client.get(f"{base}/{other_id}/favourites").json() == [ids[0]]


def test_favourites_can_be_added_and_removed_in_batches() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]
    player_id = client.post("/api/players", json={"username": "Batcher"}).json()["id"]
    url = f"/api/events/{event['id']}/players/{player_id}/favourites"

    added = client.patch(url, json={"add": ids[:5] + [ids[0]]})
    assert added.status_code == 200
    assert sorted(added.json()) == sorted(ids[:5])
    changed = client.patch(url, json={"add": [ids[6]], "remove": ids[:3]})
    assert sorted(changed.json()) == sorted([ids[3], ids[4], ids[6]])

    assert client.patch(url, json={"add": [ids[7]], "remove": [ids[7]]}).status_code == 422
    assert client.patch(url, json={"add": [ids[8], 999_999]}).status_code == 422
    assert sorted(client.get(url).json()) == sorted([ids[3], ids[4], ids[6]])


def test_rider_reference_is_built_once_and_revalidated_by_etag(monkeypatch) -> None:
    import app.main as main_module

    client = TestClient(app)
    first = client.get("/api/riders/reference")
    assert first.status_code == 200
    assert first.headers["content-encoding"] == "gzip"
    assert first.headers["cache-control"] == "public, max-age=300"

    def fail(db):
        raise AssertionError("the reference set was rebuilt")

    monkeypatch.setattr(main_module, "rider_reference_payload", fail)
    plain = client.get("/api/riders/reference", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert plain.json() == first.json()
    unchanged = client.get(
        "/api/riders/reference", headers={"If-None-Match": first.headers["etag"]}
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""


def test_rider_reference_picks_up_a_loader_run_once_it_ages_out(monkeypatch) -> None:
    from app.models import RiderProfile

    client = TestClient(app)
    rider_id = client.get("/api/events/active").json()["riders"][0]["id"]
    before = client.get("/api/riders/reference")
    with db_module.SessionLocal() as session:
        session.add(RiderProfile(rider_id=rider_id, team="Fresh Team"))
        session.commit()

    assert client.get("/api/riders/reference").json() == before.json()
    monkeypatch.setattr(reference_cache, "ttl_seconds", -1)
    after = client.get("/api/riders/reference", headers={"If-None-Match": before.headers["etag"]})
    assert after.status_code == 200
    profile = next(rider for rider in after.json()["riders"] if rider["id"] == rider_id)["profile"]
    assert profile["team"] == "Fresh Team"


def test_a_published_leaderboard_is_cached_until_the_result_is_republished(monkeypatch) -> None:
    import app.main as main_module

    client = TestClient(app)
    event = client.get("/api/events/active").json()
    ids = [rider["id"] for rider in event["riders"]]
    save_prediction(client, event, "Keeper", ids[:10])
    url = f"/api/events/{event['id']}/leaderboard"

    def publish(order):
        results = [
            {"position": position, "rider_id": rider_id}
            for position, rider_id in enumerate(order, start=1)
        ]
        return client.post(f"/api/admin/events/{event['id']}/results", json={"results": results})

    published = publish(ids[:10])
    board = client.get(url)
    assert board.json()["entries"] == published.json()["entries"]
    assert board.headers["cache-control"] == "public, max-age=60"

    builds = []
    original = main_module.persisted_leaderboard
    monkeypatch.setattr(
        main_module, "persisted_leaderboard", lambda *args: builds.append(args) or original(*args)
    )
    assert client.get(url).json() == board.json()
    assert builds == []

    corrected = publish(ids[10:20])
    again = client.get(url, headers={"If-None-Match": board.headers["etag"]})
    assert again.status_code == 200
    assert len(builds) == 1
    assert again.json()["entries"] == corrected.json()["entries"]
    assert again.json()["entries"] != board.json()["entries"]


def test_local_league_join_deadline_and_exit() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    league_player = client.post("/api/players", json={"username": "LeagueRider"}).json()
    global_player = client.post("/api/players", json={"username": "GlobalRider"}).json()
    status_url = f"/api/events/{event['id']}/players/{league_player['id']}/league"
    assert client.get(status_url).json() == {"league": None}
    assert client.put(status_url, json={"code": "missing"}).status_code == 404

    with db_module.SessionLocal() as db:
        stored_event = db.get(Event, event["id"])
        stored_event.prediction_deadline = datetime.utcnow() - timedelta(minutes=5)
        deadline = datetime.utcnow() + timedelta(hours=2)
        db.add(LocalLeague(event_id=event["id"], code="prg-office", submission_deadline=deadline))
        db.add(LocalLeague(event_id=event["id"], code="default-deadline"))
        db.commit()

    joined = client.put(status_url, json={"code": "PRG-OFFICE"})
    assert joined.status_code == 200
    assert joined.json()["league"]["code"] == "prg-office"
    assert joined.json()["league"]["joined_players"] == 1
    assert joined.json()["league"]["submitted_players"] == 0
    assert joined.json()["league"]["submission_deadline"] == deadline.isoformat()
    assert client.get(f"/api/events/{event['id']}/leagues/missing").status_code == 404

    pick = {"selections": [{"position": 1, "rider_id": event["riders"][0]["id"]}]}
    prediction_url = f"/api/events/{event['id']}/predictions"
    assert client.put(prediction_url, json={"player_id": global_player["id"], **pick}).status_code == 409
    assert client.put(prediction_url, json={"player_id": league_player["id"], **pick}).status_code == 200
    assert client.get(status_url).json()["league"]["submitted_players"] == 1
    assert client.put(status_url, json={"code": "default-deadline"}).status_code == 200
    assert client.put(prediction_url, json={"player_id": league_player["id"], **pick}).status_code == 409
    assert client.delete(status_url).status_code == 204
    assert client.get(status_url).json() == {"league": None}


def test_local_leaderboard_contains_only_members() -> None:
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    with db_module.SessionLocal() as db:
        db.add(LocalLeague(event_id=event["id"], code="friends"))
        db.commit()
    ids = [rider["id"] for rider in event["riders"]]
    first, first_save = save_prediction(client, event, "LeagueOne", ids[:10])
    second, second_save = save_prediction(client, event, "LeagueTwo", ids[1:11])
    _, global_save = save_prediction(client, event, "Elsewhere", ids[2:12])
    assert all(saved.status_code == 200 for saved in (first_save, second_save, global_save))
    for player_id in (first, second):
        response = client.put(
            f"/api/events/{event['id']}/players/{player_id}/league", json={"code": "friends"}
        )
        assert response.status_code == 200
    results = [{"position": position, "rider_id": rider_id} for position, rider_id in enumerate(ids[:10], 1)]
    assert client.post(f"/api/admin/events/{event['id']}/results", json={"results": results}).status_code == 200
    url = f"/api/events/{event['id']}/leaderboard"
    assert len(client.get(url).json()["entries"]) == 3
    local = client.get(url, params={"league_code": "friends"})
    assert local.status_code == 200
    assert {entry["username"] for entry in local.json()["entries"]} == {"LeagueOne", "LeagueTwo"}
    assert client.get(url, params={"league_code": "unknown"}).status_code == 404


def test_admin_players_summary_and_confirmed_delete(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")
    client = TestClient(app)
    event = client.get("/api/events/active").json()
    player_id = client.post("/api/players", json={"username": "DeleteMe"}).json()["id"]
    with db_module.SessionLocal() as db:
        league = LocalLeague(event_id=event["id"], code="office")
        db.add(league)
        db.flush()
        db.add(LeagueMembership(event_id=event["id"], player_id=player_id, league_id=league.id))
        db.add(PredictionTemplate(event_id=event["id"], player_id=player_id, name="Draft"))
        db.add(FavouriteRider(event_id=event["id"], player_id=player_id, rider_id=event["riders"][0]["id"]))
        db.commit()
    pick = {"player_id": player_id, "selections": [{"position": 1, "rider_id": event["riders"][0]["id"]}]}
    assert client.put(f"/api/events/{event['id']}/predictions", json=pick).status_code == 200
    summary_url = f"/api/admin/events/{event['id']}/players"
    assert client.get(summary_url).status_code == 401
    headers = {"X-Admin-Key": "test-admin-key"}
    summary = client.get(summary_url, headers=headers).json()
    assert summary["players"] == [{
        "id": player_id,
        "username": "DeleteMe",
        "submitted_flag": True,
        "last_edit": summary["players"][0]["last_edit"],
        "league_code": "office",
    }]
    assert summary["leagues"][0]["joined_players"] == 1
    assert summary["leagues"][0]["submitted_players"] == 1
    result = {"results": [{"position": 1, "rider_id": event["riders"][0]["id"]}]}
    assert client.post(f"/api/admin/events/{event['id']}/results", headers=headers, json=result).status_code == 200
    assert len(client.get(f"/api/events/{event['id']}/leaderboard").json()["entries"]) == 1
    delete_url = f"/api/admin/players/{player_id}/delete"
    assert client.post(delete_url, headers=headers, json={"confirmation": "no"}).status_code == 422
    assert client.post(delete_url, headers=headers, json={"confirmation": "DELETE"}).status_code == 200
    assert client.get(summary_url, headers=headers).json()["players"] == []
    assert client.get(f"/api/events/{event['id']}/leaderboard").json()["entries"] == []
    with db_module.SessionLocal() as db:
        assert db.query(Prediction).filter_by(player_id=player_id).count() == 0
        assert db.query(PredictionTemplate).filter_by(player_id=player_id).count() == 0
        assert db.query(FavouriteRider).filter_by(player_id=player_id).count() == 0
        assert db.query(LeagueMembership).filter_by(player_id=player_id).count() == 0
        assert db.query(ScoreLine).count() == 0
