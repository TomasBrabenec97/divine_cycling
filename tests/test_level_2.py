import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as db_module
import app.seed as seed_module
from app.db import Base
from app.main import app
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
    assert board.json()["rules"]["version"] == "v2.0"


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
