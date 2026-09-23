"""Deterministic, versioned scoring for Road Worlds predictions (model v2).

The model is specified in docs/SCORING_MODEL_V2.md and explained to players in
frontend/scoring.md. A score has three parts:

* placement -- each of the ten positioned riders earns the base points of the
  guessed position, scaled down by how far off the guess was and up by how
  obscure the rider is (a low UCI rank);
* permutations -- flat bonuses for naming the right riders in the top 3, top 5
  and top 10, in any order;
* wildcards -- three extra, unpositioned riders earn a bonus for finishing in
  the top 10, scaled by a steeper rank multiplier.

Every tunable number lives in `ScoringRules`, whose snapshot is stored with a
published score run so a result can always be audited and recomputed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

UNRANKED = 999999


@dataclass(frozen=True)
class PermutationBonuses:
    top3_exact: float = 10
    top5_exact: float = 10
    top5_four_of_five: float = 5
    top10_six_or_more: float = 10
    top10_four_or_five: float = 5


@dataclass(frozen=True)
class WildcardBonuses:
    top3: float = 10
    top5: float = 7
    top10: float = 5


@dataclass(frozen=True)
class WildcardRules:
    count: int = 3
    bonuses: WildcardBonuses = field(default_factory=WildcardBonuses)


@dataclass(frozen=True)
class RankCurves:
    """Shapes of the two UCI-rank multipliers.

    Positioned riders: an S-curve from 0 at rank 1, through `knee_boost` at
    `knee_rank`, to `max` at `transition_rank`, flat after that; the multiplier
    is 1 + boost. Wildcards: an exponential ramp from 0.00x at rank 1 to 1.00x
    at `transition_rank` (worth `low_target` halfway along), then linear to
    `wildcard_max` at `cap_rank`, flat after that.
    """

    max: float = 1.0
    knee_rank: int = 20
    knee_boost: float = 0.2
    transition_rank: int = 100
    low_target: float = 0.1
    wildcard_max: float = 3.0
    cap_rank: int = 500


@dataclass(frozen=True)
class ScoringRules:
    version: str = "v2.0"
    # The deepest actual finish that can still earn placement points. The
    # model document scores the actual top 10 only; raise it (and enter that
    # many results) to reward near misses outside the top 10 as well.
    placement_depth: int = 10
    # Factor by distance between guessed and actual position, 0 through 14;
    # 15 or more places off earns nothing. Anchored at 1.00, 0.80 and 0.10
    # with an exponential decay between 1 and 14 places.
    distance_factors: tuple[float, ...] = (
        1.0, 0.8, 0.68, 0.58, 0.5, 0.42, 0.36, 0.31, 0.26, 0.22, 0.19, 0.16, 0.14, 0.12, 0.1,
    )
    top10_points: tuple[float, ...] = (15, 10, 8, 7, 6, 5, 5, 5, 5, 5)
    permutation_bonuses: PermutationBonuses = field(default_factory=PermutationBonuses)
    wildcards: WildcardRules = field(default_factory=WildcardRules)
    position_boost: RankCurves = field(default_factory=RankCurves)


DEFAULT_RULES = ScoringRules()


def rules_snapshot(rules: ScoringRules = DEFAULT_RULES) -> dict:
    return asdict(rules)


def _known_rank(rank: int | None) -> int | None:
    return None if rank is None or rank <= 0 or rank >= UNRANKED else rank


def distance_factor(distance: int, rules: ScoringRules = DEFAULT_RULES) -> float:
    factors = rules.distance_factors
    return factors[distance] if 0 <= distance < len(factors) else 0.0


def position_boost(rank: int | None, rules: ScoringRules = DEFAULT_RULES) -> float:
    """Extra multiplier for a positioned rider: 0 at rank 1, `max` from rank 100.

    An unranked rider is treated as the hardest possible pick.
    """
    curve = rules.position_boost
    rank = _known_rank(rank)
    if rank is None or rank >= curve.transition_rank:
        return curve.max
    if rank <= curve.knee_rank:
        boost = curve.knee_boost * ((rank - 1) / (curve.knee_rank - 1)) ** 2
    else:
        remaining = (curve.transition_rank - rank) / (curve.transition_rank - curve.knee_rank)
        boost = curve.max - (curve.max - curve.knee_boost) * remaining**2
    return min(curve.max, max(0.0, boost))


def position_multiplier(rank: int | None, rules: ScoringRules = DEFAULT_RULES) -> float:
    return 1 + position_boost(rank, rules)


def wildcard_multiplier(rank: int | None, rules: ScoringRules = DEFAULT_RULES) -> float:
    """Wildcard multiplier: near 0 for favourites, 1.00 at rank 100, capped at rank 500."""
    curve = rules.position_boost
    rank = _known_rank(rank)
    if rank is None or rank >= curve.cap_rank:
        return curve.wildcard_max
    if rank >= curve.transition_rank:
        progress = (rank - curve.transition_rank) / (curve.cap_rank - curve.transition_rank)
        return 1 + (curve.wildcard_max - 1) * progress
    # (e^(kx) - 1) / (e^k - 1) passes through 0 and 1, and through `low_target`
    # at x = 1/2 exactly when k = 2 ln(1/low_target - 1).
    x = (rank - 1) / (curve.transition_rank - 1)
    k = 2 * math.log(1 / curve.low_target - 1)
    if abs(k) < 1e-9:
        return x
    return math.expm1(k * x) / math.expm1(k)


def wildcard_base_bonus(actual_position: int | None, rules: ScoringRules = DEFAULT_RULES) -> float:
    bonuses = rules.wildcards.bonuses
    if actual_position is None:
        return 0.0
    if actual_position <= 3:
        return bonuses.top3
    if actual_position <= 5:
        return bonuses.top5
    if actual_position <= 10:
        return bonuses.top10
    return 0.0


def _permutations(
    guess_by_position: dict[int, int], actual_by_position: dict[int, int], rules: ScoringRules
) -> list[dict]:
    bonuses = rules.permutation_bonuses
    lines = []
    for size in (3, 5, 10):
        guessed = {guess_by_position[p] for p in range(1, size + 1) if p in guess_by_position}
        actual = {actual_by_position[p] for p in range(1, size + 1) if p in actual_by_position}
        matched = len(guessed & actual)
        if size == 3:
            points = bonuses.top3_exact if matched == 3 else 0.0
        elif size == 5:
            points = (
                bonuses.top5_exact
                if matched == 5
                else bonuses.top5_four_of_five
                if matched == 4
                else 0.0
            )
        else:
            points = (
                bonuses.top10_six_or_more
                if matched >= 6
                else bonuses.top10_four_or_five
                if matched >= 4
                else 0.0
            )
        lines.append({"scope": f"top{size}", "size": size, "matched": matched, "points": points})
    return lines


def score_prediction(
    selections: list[tuple[int, int]],
    wildcards: list[int],
    result_positions: dict[int, int],
    rider_ranks: dict[int, int],
    rules: ScoringRules = DEFAULT_RULES,
) -> dict:
    """Score one prediction and explain every point.

    `selections` are (guessed position, rider id) pairs, `wildcards` rider ids
    in slot order, `result_positions` maps rider id to actual finish and
    `rider_ranks` the frozen UCI rank of each rider. Fractions are kept through
    the calculation and the total is rounded once, to two decimals.
    """
    guess_by_position = {position: rider_id for position, rider_id in selections}
    actual_by_position = {position: rider_id for rider_id, position in result_positions.items()}

    placements = []
    for predicted, rider_id in sorted(selections):
        actual = result_positions.get(rider_id)
        rank = rider_ranks.get(rider_id, UNRANKED)
        base = rules.top10_points[predicted - 1]
        scored = actual is not None and actual <= rules.placement_depth
        distance = abs(predicted - actual) if actual is not None else None
        factor = distance_factor(distance, rules) if scored else 0.0
        multiplier = position_multiplier(rank, rules)
        placements.append(
            {
                "predicted_position": predicted,
                "rider_id": rider_id,
                "actual_position": actual,
                "distance": distance,
                "distance_factor": factor,
                "base_points": base,
                "uci_rank": rank,
                "multiplier": round(multiplier, 6),
                "points": base * factor * multiplier,
            }
        )

    permutations = _permutations(guess_by_position, actual_by_position, rules)

    wildcard_lines = []
    for slot, rider_id in enumerate(wildcards[: rules.wildcards.count], start=1):
        actual = result_positions.get(rider_id)
        rank = rider_ranks.get(rider_id, UNRANKED)
        base = wildcard_base_bonus(actual, rules)
        multiplier = wildcard_multiplier(rank, rules)
        wildcard_lines.append(
            {
                "slot": slot,
                "rider_id": rider_id,
                "actual_position": actual,
                "base_points": base,
                "uci_rank": rank,
                "multiplier": round(multiplier, 6),
                "points": base * multiplier,
            }
        )

    placement_points = sum(line["points"] for line in placements)
    permutation_points = sum(line["points"] for line in permutations)
    wildcard_points = sum(line["points"] for line in wildcard_lines)
    total = round(placement_points + permutation_points + wildcard_points, 2)

    # Components keep six decimals: enough to re-add to the rounded total,
    # short enough to read in the stored breakdown.
    for line in (*placements, *wildcard_lines):
        line["points"] = round(line["points"], 6)
    return {
        "total_points": total,
        "placement_points": round(placement_points, 6),
        "permutation_points": round(permutation_points, 6),
        "wildcard_points": round(wildcard_points, 6),
        "placements": placements,
        "permutations": permutations,
        "wildcards": wildcard_lines,
    }
