"""Deterministic, versioned scoring for Road Worlds predictions."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ScoringRules:
    version: str = "level-4-v1"
    exact_points: float = 20
    one_away_points: float = 10
    two_away_points: float = 5
    appearance_points: float = 2
    boost_multiplier: float = 1.5
    rank_margin_cap: float = 1.35


DEFAULT_RULES = ScoringRules()


def rules_snapshot() -> dict[str, float | str]:
    return asdict(DEFAULT_RULES)


def base_points(predicted_position: int, actual_position: int | None) -> float:
    # Results may be stored through 20th place for analysis. Level 4 scoring
    # deliberately remains Top-10-only until an explicit 11–20 rule is added.
    if actual_position is None or actual_position > 10:
        return 0
    distance = abs(predicted_position - actual_position)
    if distance == 0:
        return DEFAULT_RULES.exact_points
    if distance == 1:
        return DEFAULT_RULES.one_away_points
    if distance == 2:
        return DEFAULT_RULES.two_away_points
    return DEFAULT_RULES.appearance_points


def difficulty_multiplier(uci_rank: int) -> float:
    """Award a small, capped margin for an outsider finishing in the top 10."""
    if uci_rank <= 1 or uci_rank >= 999999:
        return 1.0
    return min(DEFAULT_RULES.rank_margin_cap, 1 + (uci_rank - 1) / 1000)


def score_prediction(
    selections: list[tuple[int, int]],
    result_positions: dict[int, int],
    rider_ranks: dict[int, int],
    boosted_rider_id: int | None,
) -> tuple[float, list[dict[str, float | int | None]]]:
    lines: list[dict[str, float | int | None]] = []
    for predicted_position, rider_id in sorted(selections):
        actual_position = result_positions.get(rider_id)
        base = base_points(predicted_position, actual_position)
        boost = DEFAULT_RULES.boost_multiplier if rider_id == boosted_rider_id else 1.0
        difficulty = (
            difficulty_multiplier(rider_ranks.get(rider_id, 999999))
            if actual_position and actual_position <= 10
            else 1.0
        )
        final = round(base * boost * difficulty, 2)
        lines.append(
            {
                "rider_id": rider_id,
                "predicted_position": predicted_position,
                "actual_position": actual_position,
                "base_points": base,
                "boost_multiplier": boost,
                "difficulty_multiplier": round(difficulty, 3),
                "final_points": final,
            }
        )
    return round(sum(line["final_points"] for line in lines), 2), lines
