import argparse
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _connect_args() -> dict:
    return {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}


def _ensure_sqlite_parent() -> None:
    if settings.database_url.startswith("sqlite:///./"):
        Path(settings.database_url.removeprefix("sqlite:///./")).parent.mkdir(
            parents=True, exist_ok=True
        )


_ensure_sqlite_parent()


def _database_url() -> str:
    # Managed Postgres providers commonly publish the legacy scheme. Explicitly
    # select psycopg 3 so local SQLite and hosted Postgres share one code path.
    if settings.database_url.startswith("postgresql://"):
        return settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return settings.database_url


engine = create_engine(
    _database_url(),
    connect_args=_connect_args(),
    pool_pre_ping=not settings.database_url.startswith("sqlite"),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db(seed_mock_data: bool = False) -> None:
    # Import models here so future model modules are registered before create_all.
    from app import models  # noqa: F401

    _ensure_sqlite_parent()
    Base.metadata.create_all(bind=engine)
    # This project starts with SQLite, so make the first additive schema change
    # usable for existing local databases without requiring users to reset them.
    if engine.dialect.name == "sqlite":
        event_rider_columns = {column["name"] for column in inspect(engine).get_columns("event_riders")}
        if "uci_points" not in event_rider_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE event_riders ADD COLUMN uci_points FLOAT"))
        prediction_columns = {column["name"] for column in inspect(engine).get_columns("predictions")}
        if "boosted_rider_id" not in prediction_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE predictions ADD COLUMN boosted_rider_id INTEGER"))
    # Templates already exist in the hosted Postgres too, so this column is
    # added on either database; existing rows keep their creation order.
    template_columns = {column["name"] for column in inspect(engine).get_columns("prediction_templates")}
    if "sort_order" not in template_columns:
        if_missing = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"ALTER TABLE prediction_templates ADD COLUMN {if_missing}"
                    "sort_order INTEGER NOT NULL DEFAULT 0"
                )
            )
    event_columns = {column["name"] for column in inspect(engine).get_columns("events")}
    if "results_expected_at" not in event_columns:
        if_missing = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE events ADD COLUMN {if_missing}results_expected_at TIMESTAMP")
            )
    if seed_mock_data:
        from app.seed import seed_mock_data as seed

        seed()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    # Running `python -m app.db` executes this module as __main__. Importing the
    # package module here keeps model registration tied to its canonical Base.
    from app.db import init_db as package_init_db

    parser = argparse.ArgumentParser(description="Initialize the local game database")
    parser.add_argument("command", choices=["init"], nargs="?", default="init")
    parser.add_argument("--seed-mock-data", action="store_true")
    args = parser.parse_args()
    package_init_db(seed_mock_data=args.seed_mock_data)
    print("Database initialized")
