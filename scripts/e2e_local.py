"""End-to-end smoke test against a blank local deployment.

    python -m scripts.e2e_local
    python -m scripts.e2e_local --headed --keep-server

What it does, in order:

1. Builds a fresh SQLite database in a temporary folder from the checked-in
   exports -- the same three commands the Render build runs -- and moves the
   event deadline a week ahead so the test still works after the real race.
2. Starts uvicorn on a free port with a throwaway admin key.
3. Drives the real UI in a browser: registers two players and fills each Top
   10 and three wildcards through the rider list. The first player hearts a
   pool of favourites and picks from that view only, keeps a named template,
   saves it as final, then edits the template and switches between the lists;
   the second saves a final prediction directly.
4. Opens the admin page, enters a finishing order and previews the scoring.
5. Checks the previewed scores against the scoring module run independently,
   that the leaderboard shows each player's total, and that the compare chart
   never falls and ends exactly on each score, as a line and as stacked bars.

Screenshots of every step are written to the output folder printed at the end.
It never touches `data/game.sqlite3` or any remote database.

Needs Playwright (`pip install -e ".[e2e]"`). It drives the installed Edge or
Chrome, so no browser download is required.
"""

from __future__ import annotations

import argparse
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import httpx

ADMIN_KEY = "e2e-admin-key"
ROOT = Path(__file__).resolve().parent.parent
RESULT_DEPTH = 25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--keep-server", action="store_true", help="leave uvicorn running")
    parser.add_argument("--out", type=Path, help="folder for screenshots and the database")
    return parser.parse_args()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def build_database(workdir: Path) -> dict[str, str]:
    database = workdir / "e2e.sqlite3"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{database.as_posix()}", ADMIN_API_KEY=ADMIN_KEY)
    for command in (
        ["-m", "app.db", "init"],
        ["-m", "scripts.load_road_worlds_2026_csv"],
        ["-m", "scripts.load_pcs_data"],
    ):
        subprocess.run(
            [sys.executable, *command], cwd=ROOT, env=env, check=True, capture_output=True
        )

    import sqlite3

    with sqlite3.connect(database) as connection:
        # Deadlines are stored as naive UTC, which is what the API compares against.
        deadline = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=7)
        connection.execute(
            "UPDATE events SET prediction_deadline = ?, starts_at = ?",
            (deadline.isoformat(sep=" "), (deadline + timedelta(hours=1)).isoformat(sep=" ")),
        )
    return env


def start_server(env: dict[str, str], port: int, log: Path) -> subprocess.Popen:
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    server.terminate()
    raise SystemExit(f"uvicorn did not start; see {log}")


def plan(riders: list[dict]) -> tuple[dict[str, list[int]], list[int]]:
    """Two players' picks (Top 10, then wildcards) and a finishing order.

    The finish mixes favourites with a few low-ranked riders, so both rank
    multipliers and the wildcard bonus take part in the check.
    """
    ranked = sorted(riders, key=lambda rider: rider["uci_rank"])
    ids = [rider["id"] for rider in ranked]
    picks = {
        "e2e_alice": ids[:10] + [ids[60], ids[150], ids[40]],
        "e2e_bob": list(reversed(ids[3:13])) + [ids[61], ids[151], ids[15]],
    }
    finish = ids[:20] + ids[60:63] + ids[150:152]
    random.Random(2026).shuffle(finish)
    return picks, finish[:RESULT_DEPTH]


def expected_total(picks: list[int], finish: list[int], ranks: dict[int, int]) -> float:
    from app.scoring import score_prediction

    result_positions = {rider_id: position for position, rider_id in enumerate(finish, start=1)}
    top10, wildcards = picks[:10], picks[10:]
    score = score_prediction(list(enumerate(top10, start=1)), wildcards, result_positions, ranks)
    return score["total_points"]


def shown_top_ten(page) -> list[int]:
    return page.eval_on_selector_all(
        "#picks li", "items => items.map(item => Number(item.dataset.pickedRider || 0))"
    )


def saved_message(page, text: str) -> None:
    page.wait_for_function(
        "text => document.querySelector('#prediction-message').textContent.includes(text)", arg=text
    )


def sign_in_and_pick(page, base: str, username: str, picks: list[int], favourites=()) -> None:
    page.goto(base)
    page.wait_for_selector("#identity:not(.hidden)")
    page.fill("#username", username)
    page.click("#join")
    page.wait_for_selector("#prediction:not(.hidden)")
    page.wait_for_selector("[data-rider-add]")
    if favourites:
        # Heart a pool first, then build the lists from the favourites view only.
        for rider_id in favourites:
            page.click(f'#riders [data-rider-fav="{rider_id}"]')
        page.click("[data-favourites-only]")
        shown = page.locator("#riders .rider").count()
        if shown != len(favourites):
            raise AssertionError(f"Favourites view shows {shown} riders, hearted {len(favourites)}")
    for rider_id in picks:
        page.click(f'[data-rider-add="{rider_id}"]')
    if favourites:
        # The toggle combines with the selection filter: favourites not picked yet.
        page.click('[data-rider-selection="unselected"]')
        left = page.locator("#riders .rider").count()
        if left != len(set(favourites) - set(picks)):
            raise AssertionError(f"Unpicked favourites view shows {left} riders")
        page.click('[data-rider-selection="all"]')
        page.click("[data-favourites-only]")


def clear_favourites(page, rider_ids: list[int]) -> None:
    """Heart a few riders, then empty the list with the broken-heart button."""
    for rider_id in rider_ids:
        page.click(f'#riders [data-rider-fav="{rider_id}"]')
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("[data-favourites-clear]")
    page.wait_for_selector("[data-favourites-clear]", state="detached")
    if page.locator("#riders .rider-fav.on").count():
        raise AssertionError("hearts are still on after clearing favourites")


def fill_top_ten(page, base: str, username: str, picks: list[int], shots: Path) -> None:
    sign_in_and_pick(page, base, username, picks)
    clear_favourites(page, picks[:2])
    page.click("#save")
    saved_message(page, "Final prediction saved")
    page.screenshot(path=shots / f"{username}-top10.png", full_page=True)
    page.click("#logout")
    page.wait_for_selector("#identity:not(.hidden)")


def keep_a_template(
    page, base: str, username: str, picks: list[int], spare: int, favourites: list[int], shots: Path
):
    """Save the picks as a named template and as final, then change only the template.

    The picks are made from a hearted pool of favourites. Returns the
    template's picks after the edit; the final keeps `picks`.
    """
    sign_in_and_pick(page, base, username, picks, favourites)
    page.click("#save-new-template")
    page.wait_for_selector("[data-rename]")
    page.fill("[data-rename]", "Plan A")
    page.keyboard.press("Enter")
    page.wait_for_selector('.list-tab.active:has-text("Plan A")')
    page.click("#save")
    saved_message(page, "as your final prediction")

    page.click('[data-remove="9"]')
    page.click(f'[data-rider-add="{spare}"]')
    page.click("#save-template")
    saved_message(page, "Saved “Plan A”")
    edited = shown_top_ten(page)
    page.screenshot(path=shots / f"{username}-template.png", full_page=False)

    page.click('[data-list="final"]')
    page.wait_for_selector('.list-tab.final.active')
    if shown_top_ten(page) != picks[:10]:
        raise AssertionError(f"Final tab shows {shown_top_ten(page)}, expected {picks[:10]}")
    page.click('.list-tab:has-text("Plan A")')
    page.wait_for_selector('.list-tab.active:has-text("Plan A")')
    if shown_top_ten(page) != edited:
        raise AssertionError(f"Template tab shows {shown_top_ten(page)}, expected {edited}")

    # A throwaway copy, deleted again: the list falls back to Final.
    page.click("#save-new-template")
    page.wait_for_selector("[data-rename]")
    page.keyboard.press("Escape")
    page.once("dialog", lambda dialog: dialog.accept())
    page.click('[data-list-action="delete"]')
    saved_message(page, "Deleted")
    page.wait_for_selector(".list-tab.final.active")
    if page.locator(".list-tab").count() != 2:
        raise AssertionError("the deleted template is still listed")
    page.click("#logout")
    page.wait_for_selector("#identity:not(.hidden)")
    return edited


def simulate(page, base: str, finish: list[int], shots: Path) -> dict:
    page.on("dialog", lambda dialog: dialog.accept(ADMIN_KEY))
    page.goto(f"{base}/admin")
    page.wait_for_selector("#simulation:not(.hidden)")
    for position, rider_id in enumerate(finish, start=1):
        page.select_option(f'select[data-position="{position}"]', str(rider_id))
    with page.expect_response(lambda response: "/simulate" in response.url) as response:
        page.click("#simulate")
    preview = response.value.json()
    page.wait_for_selector("#simulation-output .lb-table")
    page.screenshot(path=shots / "admin-simulation.png", full_page=True)
    preview["page_text"] = page.inner_text("#simulation-output")
    page.click('#simulation-output [data-tab="compare"]')
    page.wait_for_selector("#simulation-output .viz-line")
    preview["chart_lines"] = page.evaluate(CHART_VALUES, "line")
    page.screenshot(path=shots / "admin-compare.png", full_page=True)
    page.click("#simulation-output .viz-chart-toggle >> text=Stacked bars")
    page.wait_for_selector("#simulation-output .viz-segment")
    preview["chart_bars"] = page.evaluate(CHART_VALUES, "stacked")
    return preview


# Reads the compare chart back into points using its own y-axis: each line's
# values left to right, or each stacked bar's top.
CHART_VALUES = """mode => {
  const svg = document.querySelector('#simulation-output .viz-svg');
  const grid = [...svg.querySelectorAll('.viz-grid')].map(line => Number(line.getAttribute('y1')));
  const ticks = [...svg.querySelectorAll('text.viz-axis[text-anchor="end"]')].map(t => Number(t.textContent));
  const value = y => ((grid[0] - y) / (grid[0] - grid[grid.length - 1])) * ticks[ticks.length - 1];
  if (mode === 'line') {
    return [...svg.querySelectorAll('.viz-line')].map(path => path.getAttribute('d')
      .split(/[ML]/).filter(Boolean).map(point => value(Number(point.split(',')[1]))));
  }
  const tops = new Map();
  svg.querySelectorAll('.viz-segment').forEach(segment => {
    const box = segment.getBBox();
    const key = Math.round(box.x);
    tops.set(key, Math.min(tops.get(key) ?? Infinity, box.y));
  });
  return [...tops.entries()].sort((a, b) => a[0] - b[0]).map(([, top]) => value(top));
}"""


def main() -> int:
    args = parse_args()
    from playwright.sync_api import sync_playwright

    workdir = args.out or Path(tempfile.mkdtemp(prefix="divine-e2e-"))
    workdir.mkdir(parents=True, exist_ok=True)
    shots = workdir / "screenshots"
    shots.mkdir(exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    print(f"Building a blank database in {workdir}")
    env = build_database(workdir)
    server = start_server(env, port, workdir / "uvicorn.log")
    failures: list[str] = []
    try:
        event = httpx.get(f"{base}/api/events/active").json()
        ranks = {rider["id"]: rider["uci_rank"] for rider in event["riders"]}
        picks, finish = plan(event["riders"])

        with sync_playwright() as playwright:
            browser = None
            for channel in ("msedge", "chrome", None):
                try:
                    browser = playwright.chromium.launch(channel=channel, headless=not args.headed)
                    break
                except Exception as error:  # noqa: BLE001 - try the next installed browser
                    print(f"  no {channel or 'bundled'} browser: {str(error).splitlines()[0]}")
            if browser is None:
                raise SystemExit("No Chromium-based browser found for Playwright.")
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(f"page error: {error}"))

            template_edits: dict[str, list[int]] = {}
            for index, (username, rider_ids) in enumerate(picks.items()):
                print(f"Filling the Top 10 of {username} through the UI")
                if index == 0:
                    spare = next(r for r in ranks if r not in rider_ids)
                    favourites = rider_ids + [r for r in ranks if r not in rider_ids][1:3]
                    template_edits[username] = keep_a_template(
                        page, base, username, rider_ids, spare, favourites, shots
                    )
                else:
                    fill_top_ten(page, base, username, rider_ids, shots)
                player = httpx.get(f"{base}/api/players/by-username/{username}").json()
                saved = httpx.get(f"{base}/api/events/{event['id']}/predictions/{player['id']}")
                body = saved.json()
                saved_ids = [item["rider_id"] for item in body["selections"]] + body["wildcards"]
                if saved_ids != rider_ids:
                    failures.append(f"{username}: saved {saved_ids}, picked {rider_ids}")
                stored = httpx.get(
                    f"{base}/api/events/{event['id']}/players/{player['id']}/favourites"
                ).json()
                # The first player kept a pool; the second hearted two and cleared them.
                expected = sorted(favourites) if index == 0 else []
                if sorted(stored) != expected:
                    failures.append(f"{username}: favourites stored as {stored}")
                if username in template_edits:
                    lists = httpx.get(
                        f"{base}/api/events/{event['id']}/players/{player['id']}/templates"
                    ).json()
                    stored = {item["position"]: item["rider_id"] for item in lists[0]["selections"]}
                    if [lists[0]["name"], [stored.get(p, 0) for p in range(1, 11)]] != [
                        "Plan A", template_edits[username]
                    ]:
                        failures.append(f"{username}: template stored as {lists}")

            print("Previewing the scoring on the admin page")
            preview = simulate(page, base, finish, shots)
            browser.close()

        totals = {entry["username"]: entry["total_points"] for entry in preview["entries"]}
        for username, rider_ids in picks.items():
            want = expected_total(rider_ids, finish, ranks)
            got = totals.get(username)
            status = "ok" if got == want else "MISMATCH"
            print(f"  {username}: API {got} / independent {want} -> {status}")
            if got != want:
                failures.append(f"{username}: API scored {got}, expected {want}")
            if username not in preview["page_text"]:
                failures.append(f"{username} missing from the admin leaderboard")
        if not any(total > 0 for total in totals.values()):
            failures.append("nobody scored: the finishing order did not overlap any pick")
        # Compare view: the two leaders, as it opens by default.
        leaders = [entry["total_points"] for entry in preview["entries"][:2]]
        for values, total in zip(preview["chart_lines"], leaders, strict=True):
            if any(later < earlier - 0.01 for earlier, later in pairwise(values)):
                failures.append(f"compare line falls somewhere: {values}")
            if abs(values[-1] - total) > 0.01:
                failures.append(f"compare line ends at {values[-1]:.2f}, not {total}")
        for top, total in zip(preview["chart_bars"], leaders, strict=True):
            if abs(top - total) > 0.01:
                failures.append(f"stacked bar reaches {top:.2f}, not {total}")
        print(f"  compare chart: lines end at {[round(v[-1], 2) for v in preview['chart_lines']]}, "
              f"bars reach {[round(v, 2) for v in preview['chart_bars']]}")
    finally:
        if args.keep_server:
            print(f"Server still running at {base} (pid {server.pid})")
        else:
            server.terminate()

    print(f"Screenshots: {shots}")
    if failures:
        print("E2E FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("E2E passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
