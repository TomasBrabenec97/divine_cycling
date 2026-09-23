"""End-to-end smoke test against a blank local deployment.

    python -m scripts.e2e_local
    python -m scripts.e2e_local --headed --keep-server

What it does, in order:

1. Builds a fresh SQLite database in a temporary folder from the checked-in
   exports -- the same three commands the Render build runs -- and moves the
   event deadline a week ahead so the test still works after the real race.
2. Starts uvicorn on a free port with a throwaway admin key.
3. Drives the real UI in a browser: registers two players, fills each Top 10
   through the rider list and saves it.
4. Opens the admin page, enters a finishing order and previews the scoring.
5. Checks the previewed scores against the scoring module run independently,
   and that the leaderboard shows each player's total.

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
from pathlib import Path

import httpx

ADMIN_KEY = "e2e-admin-key"
ROOT = Path(__file__).resolve().parent.parent
RESULT_DEPTH = 20


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
    """Two players' Top 10s and a finishing order that overlaps both."""
    ranked = sorted(riders, key=lambda rider: rider["uci_rank"])
    picks = {
        "e2e_alice": [rider["id"] for rider in ranked[:10]],
        "e2e_bob": [rider["id"] for rider in reversed(ranked[3:13])],
    }
    finish = [rider["id"] for rider in ranked[:30]]
    random.Random(2026).shuffle(finish)
    return picks, finish[:RESULT_DEPTH]


def expected_total(picks: list[int], finish: list[int], ranks: dict[int, int]) -> float:
    from app.scoring import score_prediction

    result_positions = {rider_id: position for position, rider_id in enumerate(finish, start=1)}
    total, _ = score_prediction(
        list(enumerate(picks, start=1)), result_positions, ranks, boosted_rider_id=None
    )
    return total


def fill_top_ten(page, base: str, username: str, picks: list[int], shots: Path) -> None:
    page.goto(base)
    page.wait_for_selector("#identity:not(.hidden)")
    page.fill("#username", username)
    page.click("#join")
    page.wait_for_selector("#prediction:not(.hidden)")
    page.wait_for_selector("[data-rider-add]")
    for rider_id in picks:
        page.click(f'[data-rider-add="{rider_id}"]')
    page.click("#save")
    page.wait_for_selector("#prediction-message.ok")
    page.screenshot(path=shots / f"{username}-top10.png", full_page=True)
    page.click("#logout")
    page.wait_for_selector("#identity:not(.hidden)")


def simulate(page, base: str, finish: list[int], shots: Path) -> dict:
    page.on("dialog", lambda dialog: dialog.accept(ADMIN_KEY))
    page.goto(f"{base}/admin")
    page.wait_for_selector("#simulation:not(.hidden)")
    for position, rider_id in enumerate(finish, start=1):
        page.select_option(f'select[data-position="{position}"]', str(rider_id))
    with page.expect_response(lambda response: "/simulate" in response.url) as response:
        page.click("#simulate")
    preview = response.value.json()
    page.wait_for_selector("#simulation-output .leaderboard-entry, #simulation-output tr")
    page.screenshot(path=shots / "admin-simulation.png", full_page=True)
    preview["page_text"] = page.inner_text("#simulation-output")
    return preview


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

            for username, rider_ids in picks.items():
                print(f"Filling the Top 10 of {username} through the UI")
                fill_top_ten(page, base, username, rider_ids, shots)
                player = httpx.get(f"{base}/api/players/by-username/{username}").json()
                saved = httpx.get(f"{base}/api/events/{event['id']}/predictions/{player['id']}")
                saved_ids = [item["rider_id"] for item in saved.json()["selections"]]
                if saved_ids != rider_ids:
                    failures.append(f"{username}: saved {saved_ids}, picked {rider_ids}")

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
