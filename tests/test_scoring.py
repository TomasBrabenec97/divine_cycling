"""Scoring model v2: the invariants listed in docs/SCORING_MODEL_V2.md."""

from dataclasses import replace

import pytest

from app.scoring import (
    DEFAULT_RULES,
    UNRANKED,
    distance_factor,
    position_boost,
    position_multiplier,
    score_prediction,
    wildcard_multiplier,
)

TOP10_POINTS = [15, 10, 8, 7, 6, 5, 5, 5, 5, 5]


def ranked(*ranks: int) -> dict[int, int]:
    """Rider ids 1..n with the given UCI ranks."""
    return {rider_id: rank for rider_id, rank in enumerate(ranks, start=1)}


def finish(*rider_ids: int) -> dict[int, int]:
    return {rider_id: position for position, rider_id in enumerate(rider_ids, start=1)}


def test_exact_top_ten_earns_every_base_value_multiplier_and_permutation_bonus() -> None:
    ranks = ranked(*range(1, 11))
    guess = list(enumerate(range(1, 11), start=1))
    score = score_prediction(guess, [], finish(*range(1, 11)), ranks)

    expected_placement = sum(
        points * position_multiplier(rank) for points, rank in zip(TOP10_POINTS, range(1, 11))
    )
    assert score["placement_points"] == pytest.approx(expected_placement, abs=1e-5)
    assert [line["points"] for line in score["permutations"]] == [10, 10, 10]
    assert all(line["distance_factor"] == 1.0 for line in score["placements"])
    assert score["total_points"] == round(expected_placement + 30, 2)


def test_placement_scales_base_points_by_distance_and_rank() -> None:
    # Guessed 1st, finished 3rd: 15 base, two places off, UCI rank 30.
    score = score_prediction([(1, 7)], [], {7: 3}, {7: 30})
    line = score["placements"][0]

    assert line["distance"] == 2
    assert line["distance_factor"] == distance_factor(2) == pytest.approx(0.5625)
    assert line["points"] == pytest.approx(15 * 0.5625 * position_multiplier(30), abs=1e-5)


def test_near_misses_outside_the_top_ten_still_earn_placement_points() -> None:
    # Guessed 10th, finished 11th: one place off, like any other near miss.
    score = score_prediction([(10, 5)], [], {5: 11}, {5: 1})
    assert score["placements"][0]["distance_factor"] == 0.75
    assert score["placements"][0]["points"] == pytest.approx(5 * 0.75)

    # 19th is the deepest a 10th pick can finish and still score (9 places off).
    deepest = score_prediction([(10, 5)], [], {5: 19}, {5: 1})
    assert deepest["placements"][0]["distance"] == 9
    assert deepest["placements"][0]["points"] == pytest.approx(5 * 0.75**9, abs=1e-5)


def test_far_misses_and_non_finishers_earn_no_placement_points() -> None:
    ranks = {5: UNRANKED, 6: 700, 7: 30}
    score = score_prediction([(1, 5), (10, 6), (5, 7)], [], {5: 11, 6: 20}, ranks)

    # 10 places off, beyond the deepest scored finish, and no classified finish.
    assert [line["points"] for line in score["placements"]] == [0.0, 0.0, 0.0]
    assert score["total_points"] == 0


def test_placement_depth_can_restrict_placement_to_the_top_ten() -> None:
    top_ten_only = replace(DEFAULT_RULES, placement_depth=10)
    assert score_prediction([(10, 5)], [], {5: 11}, {5: 1}, rules=top_ten_only)["total_points"] == 0


def test_distance_factors_decay_by_a_quarter_per_place_and_stop_at_ten() -> None:
    assert distance_factor(0) == 1.0
    assert distance_factor(1) == 0.75
    assert distance_factor(9) == pytest.approx(0.075, abs=5e-4)
    assert distance_factor(10) == 0.0
    assert all(distance_factor(d) == pytest.approx(0.75**d) for d in range(10))
    factors = [distance_factor(d) for d in range(12)]
    assert factors == sorted(factors, reverse=True)


@pytest.mark.parametrize(
    ("guess", "expected"),
    [
        ([3, 1, 2, 5, 8, 7, 6, 9, 4, 10], [10, 5, 10]),  # top 3 set, 4/5, all ten
        ([1, 2, 11, 3, 12, 13, 14, 15, 16, 17], [0, 0, 0]),  # 2/3, 3/5, 3/10
        ([1, 2, 3, 4, 5, 11, 12, 13, 14, 15], [10, 10, 5]),  # 5/10 earns the lower tier
        ([11, 12, 13, 14, 15, 1, 2, 3, 4, 16], [0, 0, 5]),  # 4/10, none in the top 5
        ([11, 12, 13, 14, 15, 1, 2, 3, 4, 5], [0, 0, 5]),  # 5/10
        ([11, 12, 13, 14, 1, 2, 3, 4, 5, 6], [0, 0, 10]),  # 6/10
    ],
)
def test_permutation_bonuses_match_sets_not_order(guess: list[int], expected: list[int]) -> None:
    ranks = ranked(*[1] * 20)
    score = score_prediction(list(enumerate(guess, start=1)), [], finish(*range(1, 11)), ranks)
    assert [line["points"] for line in score["permutations"]] == expected


def test_permutations_ignore_rank_multipliers() -> None:
    guess = list(enumerate(range(1, 11), start=1))
    favourites = score_prediction(guess, [], finish(*range(1, 11)), ranked(*range(1, 11)))
    outsiders = score_prediction(guess, [], finish(*range(1, 11)), ranked(*[900] * 10))
    assert favourites["permutations"] == outsiders["permutations"]
    assert outsiders["placement_points"] > favourites["placement_points"]


def test_wildcards_earn_a_finish_band_bonus_times_their_own_multiplier() -> None:
    ranks = {20: 600, 21: 150, 22: 1, 23: 300}
    score = score_prediction([], [20, 21, 22], {20: 2, 21: 5, 22: 8, 23: 1}, ranks)
    lines = score["wildcards"]

    assert [line["base_points"] for line in lines] == [10, 7, 5]
    assert lines[0]["points"] == pytest.approx(10 * 4.0)
    assert lines[1]["points"] == pytest.approx(7 * 2.25)
    assert lines[2]["points"] == 0.0  # the world number one is no wildcard
    assert score_prediction([], [23], {23: 11}, ranks)["wildcards"][0]["points"] == 0.0


def test_wildcards_do_not_count_towards_permutations() -> None:
    score = score_prediction([(1, 1), (2, 2)], [3], finish(1, 2, 3), ranked(1, 2, 3))
    assert score["permutations"][0]["matched"] == 2
    assert score["permutations"][0]["points"] == 0


def test_rank_curves_hit_their_anchors_and_never_decrease() -> None:
    assert position_boost(1) == 0.0
    assert position_boost(20) == pytest.approx(0.2)
    assert position_boost(100) == 1.0
    assert position_multiplier(UNRANKED) == 2.0
    assert wildcard_multiplier(1) == 0.0
    assert wildcard_multiplier(30) < 0.07
    assert 0.5 < wildcard_multiplier(75) < wildcard_multiplier(80) < 1.0
    assert wildcard_multiplier(100) == pytest.approx(2.0)
    assert wildcard_multiplier(300) == pytest.approx(3.0)
    assert wildcard_multiplier(500) == 4.0
    assert wildcard_multiplier(UNRANKED) == 4.0

    positions = [position_multiplier(rank) for rank in range(1, 701)]
    wildcards = [wildcard_multiplier(rank) for rank in range(1, 701)]
    assert positions == sorted(positions) and max(positions) == 2.0
    assert wildcards == sorted(wildcards) and max(wildcards) == 4.0


def test_scoring_is_deterministic_and_rounds_once() -> None:
    guess = [(1, 1), (2, 2), (3, 3)]
    ranks = ranked(33, 77, 250)
    first = score_prediction(guess, [4], finish(3, 1, 2, 4), {**ranks, 4: 420})
    second = score_prediction(guess, [4], finish(3, 1, 2, 4), {**ranks, 4: 420})

    assert first == second
    lines = first["placements"] + first["permutations"] + first["wildcards"]
    raw = sum(line["points"] for line in lines)
    assert first["total_points"] == round(raw, 2)
