"""The player-facing scoring guide quotes the rules; keep the two in step."""

from pathlib import Path

from app.scoring import DEFAULT_RULES, position_multiplier, wildcard_multiplier

GUIDE = (Path(__file__).resolve().parent.parent / "frontend" / "scoring.md").read_text(
    encoding="utf-8"
)


def table_row(label: str, values: list[str]) -> str:
    return f"| **{label}** | " + " | ".join(values) + " |"


def test_the_guide_quotes_the_current_rules() -> None:
    rules = DEFAULT_RULES
    assert f"rules version **{rules.version}**" in GUIDE

    factors = [f"×{factor:.2f}" for factor in rules.distance_factors]
    assert rules.placement_depth == 10 + len(rules.distance_factors) - 1
    assert table_row("Factor", factors) in GUIDE

    points = rules.top10_points
    assert table_row("Base points", [*map(str, points[:5]), f"{points[5]} each"]) in GUIDE
    assert len(set(points[5:])) == 1

    top10_ranks = [1, 5, 10, 20, 30, 50, 75]
    top10 = [f"×{position_multiplier(rank):.2f}" for rank in top10_ranks]
    assert table_row("Top 10 multiplier", [*top10, f"×{1 + rules.position_boost.max:.2f}"]) in GUIDE

    wildcard_ranks = [1, 10, 30, 50, 75, 100, 200, 300, 400]
    wildcards = [f"×{wildcard_multiplier(rank):.2f}" for rank in wildcard_ranks]
    wildcards.append(f"×{rules.position_boost.wildcard_max:.2f}")
    assert table_row("Wildcard multiplier", wildcards) in GUIDE

    bands = rules.wildcards.bonuses
    assert table_row("Bonus", [str(bands.top3), str(bands.top5), str(bands.top10), "0"]) in GUIDE


def test_the_guide_states_the_true_maximum_of_each_part() -> None:
    rules = DEFAULT_RULES
    placement = sum(rules.top10_points) * (1 + rules.position_boost.max)
    bonuses = rules.permutation_bonuses
    permutations = bonuses.top3_exact + bonuses.top5_exact + bonuses.top10_six_or_more
    wildcards = rules.wildcards.count * rules.wildcards.bonuses.top3 * rules.position_boost.wildcard_max

    assert f"| {placement:g} pts |" in GUIDE
    assert f"| {permutations:g} pts |" in GUIDE
    assert f"| {wildcards:g} pts |" in GUIDE
