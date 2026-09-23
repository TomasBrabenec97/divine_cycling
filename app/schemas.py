from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PlayerCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)

    @field_validator("username")
    @classmethod
    def username_is_safe(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may use only letters, numbers, hyphens, and underscores")
        return cleaned


class PlayerResponse(BaseModel):
    id: int
    username: str


class RiderResponse(BaseModel):
    id: int
    name: str
    nation: str
    uci_rank: int
    uci_points: float | None
    team: str | None = None


class EventResponse(BaseModel):
    id: int
    slug: str
    name: str
    starts_at: datetime
    prediction_deadline: datetime
    status: str
    source_name: str
    source_updated_at: datetime
    riders: list[RiderResponse]


class PredictionSelection(BaseModel):
    position: int = Field(ge=1, le=10)
    rider_id: int


class PredictionUpsert(BaseModel):
    player_id: int
    selections: list[PredictionSelection] = Field(min_length=1, max_length=10)
    boosted_rider_id: int | None = None

    @field_validator("selections")
    @classmethod
    def selections_are_complete(cls, value: list[PredictionSelection]) -> list[PredictionSelection]:
        if len({item.position for item in value}) != len(value):
            raise ValueError("A position may only be selected once")
        if len({item.rider_id for item in value}) != len(value):
            raise ValueError("A rider may only be selected once")
        return value


class PredictionResponse(BaseModel):
    id: int
    player_id: int
    event_id: int
    submitted_at: datetime
    updated_at: datetime
    boosted_rider_id: int | None
    selections: list[PredictionSelection]


class ResultSelection(BaseModel):
    position: int = Field(ge=1, le=20)
    rider_id: int


class ResultUpsert(BaseModel):
    results: list[ResultSelection] = Field(min_length=1, max_length=20)

    @field_validator("results")
    @classmethod
    def results_are_unique(cls, value: list[ResultSelection]) -> list[ResultSelection]:
        if len({item.position for item in value}) != len(value):
            raise ValueError("A result position may only be entered once")
        if len({item.rider_id for item in value}) != len(value):
            raise ValueError("A rider may only appear once in results")
        return value


class ScoreBreakdownLine(BaseModel):
    rider_id: int
    predicted_position: int
    actual_position: int | None
    base_points: float
    boost_multiplier: float
    difficulty_multiplier: float
    final_points: float


class LeaderboardEntry(BaseModel):
    username: str
    total_points: float
    breakdown: list[ScoreBreakdownLine]


class LeaderboardResponse(BaseModel):
    event_id: int
    rules_version: str
    is_simulation: bool
    results: list[ResultSelection]
    entries: list[LeaderboardEntry]
