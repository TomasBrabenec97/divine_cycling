"""Draw the scoring-guide figures from the live scoring rules.

    python -m scripts.render_scoring_figures

Writes plain SVG, so the figures are always the curves `app.scoring` actually
uses -- re-run it after changing a rule:

    frontend/scoring/distance-factors.svg   distance factor by places off
    frontend/scoring/multipliers.svg        Top 10 and wildcard rank multipliers
    docs/position_boost_curve.svg           the positioned-rider boost (design doc)
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from app.scoring import DEFAULT_RULES, position_boost, position_multiplier, wildcard_multiplier

FONT = "'Bricolage Grotesque', Arial, sans-serif"
INK = "#2f3136"
MUTED = "#746e66"
GRID = "#e6dccb"
SURFACE = "#fcf8f1"
SERIES = ("#2f6f9f", "#c46a3a")  # validated pair: slate blue, the site's terracotta
DE_EMPHASIS = "#cbbfae"

WIDTH, HEIGHT = 640, 300
LEFT, RIGHT, TOP, BOTTOM = 52, 70, 40, 44


class Plot:
    def __init__(self, x_range: tuple[float, float], y_range: tuple[float, float], label: str):
        self.x0, self.x1 = x_range
        self.y0, self.y1 = y_range
        self.parts: list[str] = []
        self.label = label

    def x(self, value: float) -> float:
        return LEFT + (value - self.x0) / (self.x1 - self.x0) * (WIDTH - LEFT - RIGHT)

    def y(self, value: float) -> float:
        return HEIGHT - BOTTOM - (value - self.y0) / (self.y1 - self.y0) * (HEIGHT - TOP - BOTTOM)

    def text(self, x: float, y: float, body: str, anchor: str = "start", color: str = MUTED,
             size: int = 11, weight: int = 400) -> None:
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{color}" '
            f'font-size="{size}" font-weight="{weight}">{escape(body)}</text>'
        )

    def grid(self, ticks: list[float], fmt) -> None:
        for tick in ticks:
            y = self.y(tick)
            self.parts.append(
                f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" '
                f'stroke="{GRID}" stroke-width="1"/>'
            )
            self.text(LEFT - 8, y + 4, fmt(tick), anchor="end")

    def x_ticks(self, ticks: list[tuple[float, str]], title: str) -> None:
        for value, label in ticks:
            self.text(self.x(value), HEIGHT - BOTTOM + 18, label, anchor="middle")
        self.text(WIDTH - RIGHT, HEIGHT - 6, title, anchor="end")

    def line(self, points: list[tuple[float, float]], color: str) -> None:
        path = " ".join(
            f"{'M' if index == 0 else 'L'}{self.x(x):.1f},{self.y(y):.1f}"
            for index, (x, y) in enumerate(points)
        )
        self.parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )

    def dot(self, x: float, y: float, color: str, title: str) -> None:
        self.parts.append(
            f'<circle cx="{self.x(x):.1f}" cy="{self.y(y):.1f}" r="4.5" fill="{color}" '
            f'stroke="{SURFACE}" stroke-width="2"><title>{escape(title)}</title></circle>'
        )

    def legend(self, entries: list[tuple[str, str]]) -> None:
        x = LEFT
        for label, color in entries:
            self.parts.append(
                f'<line x1="{x}" x2="{x + 16}" y1="16" y2="16" stroke="{color}" '
                f'stroke-width="2" stroke-linecap="round"/>'
            )
            self.text(x + 22, 20, label, color=INK)
            x += 30 + 7 * len(label)

    def svg(self) -> str:
        body = "\n  ".join(self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(self.label)}" '
            f'font-family="{FONT}">\n  {body}\n</svg>\n'
        )


def rounded_column(x: float, y_top: float, y_base: float, width: float, color: str) -> str:
    """A column with a 4px rounded data end and a square foot on the baseline."""
    radius = min(4.0, (y_base - y_top) / 2)
    left, right = x - width / 2, x + width / 2
    return (
        f'<path d="M{left:.1f},{y_base:.1f} L{left:.1f},{y_top + radius:.1f} '
        f'Q{left:.1f},{y_top:.1f} {left + radius:.1f},{y_top:.1f} '
        f'L{right - radius:.1f},{y_top:.1f} Q{right:.1f},{y_top:.1f} {right:.1f},{y_top + radius:.1f} '
        f'L{right:.1f},{y_base:.1f} Z" fill="{color}"/>'
    )


def distance_figure() -> str:
    factors = DEFAULT_RULES.distance_factors
    reachable = DEFAULT_RULES.placement_depth - 1
    plot = Plot((-0.6, len(factors) - 0.4), (0, 1.0), "Distance factor by places off")
    plot.grid([0, 0.25, 0.5, 0.75, 1.0], lambda tick: f"×{tick:.2f}")
    for distance, factor in enumerate(factors):
        color = SERIES[0] if distance <= reachable else DE_EMPHASIS
        x = plot.x(distance)
        plot.parts.append(rounded_column(x, plot.y(factor), plot.y(0), 20, color))
        plot.parts[-1] = plot.parts[-1].replace(
            "/>", f"><title>{distance} places off: ×{factor:.2f}</title></path>"
        )
        if distance in (0, 1, len(factors) - 1):
            plot.text(x, plot.y(factor) - 6, f"×{factor:.2f}", anchor="middle", color=INK)
    plot.x_ticks([(d, str(d)) for d in range(len(factors))], "places off")
    plot.text(
        WIDTH - 8, TOP - 18,
        f"grey: more than {reachable} places off, unreachable while only the top 10 scores",
        anchor="end",
    )
    return plot.svg()


def multiplier_figure() -> str:
    curve = DEFAULT_RULES.position_boost
    last = curve.cap_rank + 100
    plot = Plot((1, last), (0, curve.wildcard_max), "Rank multipliers by UCI rank")
    plot.grid([0, 1, 2, 3], lambda tick: f"×{tick:.0f}")
    ranks = list(range(1, last + 1))
    plot.line([(rank, position_multiplier(rank)) for rank in ranks], SERIES[0])
    plot.line([(rank, wildcard_multiplier(rank)) for rank in ranks], SERIES[1])
    plot.dot(curve.transition_rank, position_multiplier(curve.transition_rank), SERIES[0],
             f"Top 10 pick: ×{1 + curve.max:.2f} from rank {curve.transition_rank}")
    plot.dot(curve.transition_rank, 1.0, SERIES[1], f"Wildcard: ×1.00 at rank {curve.transition_rank}")
    plot.dot(curve.cap_rank, curve.wildcard_max, SERIES[1],
             f"Wildcard: ×{curve.wildcard_max:.2f} from rank {curve.cap_rank}")
    plot.text(WIDTH - RIGHT + 8, plot.y(position_multiplier(last)) + 4,
              f"×{1 + curve.max:.2f}", color=INK)
    plot.text(WIDTH - RIGHT + 8, plot.y(wildcard_multiplier(last)) + 4,
              f"×{curve.wildcard_max:.2f}", color=INK)
    plot.legend([("Top 10 pick", SERIES[0]), ("Wildcard", SERIES[1])])
    plot.x_ticks([(1, "1"), *[(rank, str(rank)) for rank in range(100, last + 1, 100)]],
                 "UCI rank")
    return plot.svg()


def boost_figure() -> str:
    curve = DEFAULT_RULES.position_boost
    last = 150
    plot = Plot((1, last), (0, curve.max), "Positioned-rider boost by UCI rank")
    plot.grid([0, 0.25, 0.5, 0.75, 1.0], lambda tick: f"{tick:.2f}")
    plot.line([(rank, position_boost(rank)) for rank in range(1, last + 1)], SERIES[0])
    plot.dot(curve.knee_rank, curve.knee_boost, SERIES[0],
             f"{curve.knee_boost:.2f} at rank {curve.knee_rank}")
    plot.dot(curve.transition_rank, curve.max, SERIES[0],
             f"{curve.max:.2f} from rank {curve.transition_rank}")
    plot.text(plot.x(curve.knee_rank) + 8, plot.y(curve.knee_boost) + 16,
              f"{curve.knee_boost:.2f} at rank {curve.knee_rank}", color=INK)
    plot.text(plot.x(curve.transition_rank), plot.y(curve.max) - 10,
              f"{curve.max:.2f} from rank {curve.transition_rank} (multiplier ×{1 + curve.max:.2f})",
              anchor="middle", color=INK)
    plot.x_ticks([(1, "1"), (20, "20"), (50, "50"), (100, "100"), (150, "150")], "UCI rank")
    return plot.svg()


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    figures = {
        root / "frontend/scoring/distance-factors.svg": distance_figure(),
        root / "frontend/scoring/multipliers.svg": multiplier_figure(),
        root / "docs/position_boost_curve.svg": boost_figure(),
    }
    for path, svg in figures.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
        print(f"Wrote {path.relative_to(root)}")


if __name__ == "__main__":
    main()
