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
