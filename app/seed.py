from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Event, EventRider, Rider

MOCK_RIDERS = [
    ("mock-001", "Mateo", "Varga", "HUN", 1),
    ("mock-002", "Luca", "Bellini", "ITA", 2),
    ("mock-003", "Elias", "Nordstrom", "SWE", 4),
    ("mock-004", "Noah", "Keller", "SUI", 6),
    ("mock-005", "Julien", "Moreau", "FRA", 9),
    ("mock-006", "Theo", "Ashford", "GBR", 14),
    ("mock-007", "Iker", "Serrano", "ESP", 18),
    ("mock-008", "Milan", "Kovac", "CRO", 25),
    ("mock-009", "Jonas", "Lind", "DEN", 31),
    ("mock-010", "Tomas", "Novak", "CZE", 38),
    ("mock-011", "Felix", "Hartmann", "GER", 47),
    ("mock-012", "Rui", "Carvalho", "POR", 61),
    ("mock-013", "Bram", "de Vries", "NED", 79),
    ("mock-014", "Kacper", "Zielinski", "POL", 96),
    ("mock-015", "Oskar", "Berg", "NOR", 121),
    ("mock-016", "Adam", "Kral", "SVK", 154),
    ("mock-017", "Antoine", "Leroux", "FRA", 173),
    ("mock-018", "Riccardo", "Conti", "ITA", 189),
    ("mock-019", "Harry", "Wainwright", "GBR", 216),
    ("mock-020", "Petr", "Dvorak", "CZE", 251),
]


def seed_mock_data() -> None:
    """Upsert a fictional event and riders; safe to run repeatedly."""
    db = SessionLocal()
    try:
        event = db.scalar(select(Event).where(Event.slug == "mock-road-worlds-2027"))
        if event is None:
            event = Event(
                slug="mock-road-worlds-2027",
                name="2027 Road World Championship (mock)",
                starts_at=datetime(2027, 9, 26, 10, 0),
                prediction_deadline=datetime(2027, 9, 26, 9, 30),
                results_expected_at=datetime(2027, 9, 26, 18, 30),
                status="open",
                source_name="Artificial development data",
                source_updated_at=datetime.utcnow(),
            )
            db.add(event)
            db.flush()

        for external_id, first_name, last_name, nation, rank in MOCK_RIDERS:
            rider = db.scalar(select(Rider).where(Rider.external_id == external_id))
            if rider is None:
                rider = Rider(
                    external_id=external_id,
                    first_name=first_name,
                    last_name=last_name,
                    nation=nation,
                )
                db.add(rider)
                db.flush()
            event_rider = db.scalar(
                select(EventRider).where(
                    EventRider.event_id == event.id, EventRider.rider_id == rider.id
                )
            )
            if event_rider is None:
                db.add(
                    EventRider(event_id=event.id, rider_id=rider.id, uci_rank=rank, is_starter=True)
                )
        db.commit()
    finally:
        db.close()
