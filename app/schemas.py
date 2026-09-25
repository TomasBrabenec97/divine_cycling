from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


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


class LeagueJoin(BaseModel):
    code: str = Field(min_length=3, max_length=40)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        code = value.strip().lower()
        if not code or not all(char.isalnum() and char.isascii() or char == "-" for char in code):
            raise ValueError("League code may use only letters, numbers, and hyphens")
        return code


class LeagueResponse(BaseModel):
    code: str
    submission_deadline: datetime
    joined_players: int
    submitted_players: int


class LeagueStatus(BaseModel):
    league: LeagueResponse | None


class AdminDeletePlayer(BaseModel):
    confirmation: str


class RiderResponse(BaseModel):
    id: int
    name: str
    nation: str
    uci_rank: int
    uci_points: float | None
    team: str | None = None
    # What a correct pick of this rider is worth under the current rules, so the
    # UI can show it without re-implementing the scoring curves.
    position_multiplier: float = 1.0
    wildcard_multiplier: float = 1.0


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


class PicksBase(BaseModel):
    """A positioned Top 10 plus up to three unpositioned wildcard riders."""

    selections: list[PredictionSelection] = Field(default_factory=list, max_length=10)
    wildcards: list[int] = Field(default_factory=list, max_length=3)

    @field_validator("selections")
    @classmethod
    def selections_are_complete(cls, value: list[PredictionSelection]) -> list[PredictionSelection]:
        if len({item.position for item in value}) != len(value):
            raise ValueError("A position may only be selected once")
        if len({item.rider_id for item in value}) != len(value):
            raise ValueError("A rider may only be selected once")
        return value

    @model_validator(mode="after")
    def wildcards_stand_apart(self) -> "PicksBase":
        if len(set(self.wildcards)) != len(self.wildcards):
            raise ValueError("A wildcard may only be selected once")
        if set(self.wildcards) & {item.rider_id for item in self.selections}:
            raise ValueError("A wildcard cannot also be in your Top 10")
        return self


class PredictionPicks(PicksBase):
    """The final prediction: the one that is scored, so it needs a Top 10 pick."""

    selections: list[PredictionSelection] = Field(min_length=1, max_length=10)


class PredictionUpsert(PredictionPicks):
    player_id: int


class TemplateUpsert(PicksBase):
    """A named draft list; it may be empty or incomplete."""

    player_id: int
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def name_is_trimmed(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Give the list a name")
        return cleaned


class TemplateOrder(BaseModel):
    """Every one of a player's templates, in the order their tabs should read."""

    template_ids: list[int] = Field(max_length=100)


class FavouritesUpdate(BaseModel):
    """Riders to heart and to un-heart in one go (a group, or every rider shown)."""

    add: list[int] = Field(default_factory=list, max_length=500)
    remove: list[int] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def add_and_remove_differ(self) -> "FavouritesUpdate":
        if set(self.add) & set(self.remove):
            raise ValueError("A rider cannot be added and removed at once")
        return self


class TemplateResponse(BaseModel):
    id: int
    name: str
    selections: list[PredictionSelection]
    wildcards: list[int]
    created_at: datetime
    updated_at: datetime


class PredictionResponse(BaseModel):
    id: int
    player_id: int
    event_id: int
    submitted_at: datetime
    updated_at: datetime
    selections: list[PredictionSelection]
    wildcards: list[int]


# Results are entered through 25th: deep enough for the lineage view and for a
# placement depth of up to 24 (guessed 10th, 14 places off).
MAX_RESULT_POSITION = 25


class ResultSelection(BaseModel):
    position: int = Field(ge=1, le=MAX_RESULT_POSITION)
    rider_id: int


class ResultUpsert(BaseModel):
    results: list[ResultSelection] = Field(min_length=1, max_length=MAX_RESULT_POSITION)

    @field_validator("results")
    @classmethod
    def results_are_unique(cls, value: list[ResultSelection]) -> list[ResultSelection]:
        if len({item.position for item in value}) != len(value):
            raise ValueError("A result position may only be entered once")
        if len({item.rider_id for item in value}) != len(value):
            raise ValueError("A rider may only appear once in results")
        return value


class PlacementLine(BaseModel):
    predicted_position: int
    rider_id: int
    actual_position: int | None
    distance: int | None
    distance_factor: float
    base_points: float
    uci_rank: int
    multiplier: float
    points: float


class PermutationLine(BaseModel):
    scope: str
    size: int
    matched: int
    points: float


class WildcardLine(BaseModel):
    slot: int
    rider_id: int
    actual_position: int | None
    base_points: float
    uci_rank: int
    multiplier: float
    points: float


class LeaderboardEntry(BaseModel):
    username: str
    total_points: float
    placement_points: float
    permutation_points: float
    wildcard_points: float
    placements: list[PlacementLine]
    permutations: list[PermutationLine]
    wildcards: list[WildcardLine]


class LeaderboardResponse(BaseModel):
    event_id: int
    rules_version: str
    rules: dict
    is_simulation: bool
    results: list[ResultSelection]
    entries: list[LeaderboardEntry]
