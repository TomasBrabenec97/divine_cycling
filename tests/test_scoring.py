from app.scoring import DEFAULT_RULES, score_prediction


def test_scoring_is_deterministic_and_explains_each_line() -> None:
    selections = [(1, 11), (2, 22), (3, 33), (4, 44)]
    results = {11: 1, 22: 3, 33: 5}
    ranks = {11: 1, 22: 501, 33: 2000, 44: 7}

    total, lines = score_prediction(selections, results, ranks, boosted_rider_id=22)

    assert total == 47.0
    assert lines[0]["final_points"] == 20
    assert lines[1] == {
        "rider_id": 22,
        "predicted_position": 2,
        "actual_position": 3,
        "base_points": 10,
        "boost_multiplier": 1.5,
        "difficulty_multiplier": 1.35,
        "final_points": 20.25,
    }
    assert lines[2]["final_points"] == 6.75
    assert lines[3]["actual_position"] is None
    assert lines[3]["final_points"] == 0


def test_rank_margin_is_capped() -> None:
    total, lines = score_prediction([(1, 99)], {99: 1}, {99: 9999}, boosted_rider_id=None)
    assert lines[0]["difficulty_multiplier"] == DEFAULT_RULES.rank_margin_cap
    assert total == 27.0


def test_a_result_below_tenth_is_visible_but_not_yet_scored() -> None:
    total, lines = score_prediction([(7, 99)], {99: 15}, {99: 2000}, boosted_rider_id=99)

    assert total == 0
    assert lines[0]["actual_position"] == 15
    assert lines[0]["difficulty_multiplier"] == 1.0
    assert lines[0]["final_points"] == 0
