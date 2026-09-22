"""Parser tests for the ProCyclingStats fetchers.

The fixtures are trimmed copies of real PCS markup, so a change on their side
shows up here rather than as silently empty columns in an export.
"""

import tempfile
from pathlib import Path

from scripts.fill_uci_points import movement
from scripts.pcs.client import PCSClient
from scripts.pcs.races import parse_result
from scripts.pcs.rankings import RankingEntry, parse_entries
from scripts.pcs.riders import parse_overview, parse_seasons

RANKING_HTML = """
<table class="basic">
<thead><tr><th>#</th><th>Prev.</th><th>Diff.</th><th>Rider</th><th>Team</th><th>Points</th></tr></thead>
<tbody>
<tr><td>1</td><td>1</td><td>-</td>
    <td><span class="flag si"></span> <a href="rider/tadej-pogacar">POGA&#268;AR Tadej</a></td>
    <td class="cu600"><a href="team/uae-team-emirates-xrg-2026">UAE Team Emirates - XRG</a></td>
    <td><a href="rider.php?id=194619">11481.8</a></td></tr>
<tr><td>2</td><td>6</td><td>4</td>
    <td><span class="flag be"></span> <a href="rider/remco-evenepoel">EVENEPOEL Remco</a></td>
    <td class="cu600"><a href="team/red-bull-bora-hansgrohe-2026">Red Bull - BORA - hansgrohe</a></td>
    <td><a href="rider.php?id=212377">7921.6</a></td></tr>
<tr><td colspan="6">advertisement</td></tr>
</tbody></table>
"""

RESULT_HTML = """
<h1>2025 &nbsp; &raquo; &nbsp; 92nd World Championships ME - Road Race (WC)</h1>
<table class="results"><thead><tr><th>Rnk</th></tr></thead><tbody>
<tr><td>1</td><td class="bibs">1</td><td class="age">27</td>
    <td class="ridername"><a href="rider/tadej-pogacar"><span class="uppercase">Poga&#269;ar</span> Tadej</a>
        <div class="showIfMobile">Slovenia</div></td>
    <td class="cu600"><a href="team/slovenia-2025">Slovenia</a></td>
    <td class="uci_pnt">900</td><td class="pnt">350</td>
    <td class="time ar"><font>6:21:20</font><span class="hide">6:21:20</span></td></tr>
<tr><td>4</td><td class="bibs">92</td><td class="age">27</td>
    <td class="ridername"><a href="rider/mads-pedersen"><span class="uppercase">Pedersen</span> Mads</a></td>
    <td class="cu600"><a href="team/denmark-2025">Denmark</a></td>
    <td class="uci_pnt">490</td><td class="pnt">150</td>
    <td class="time ar"><font>,,</font><span class="hide">1:45</span></td></tr>
<tr><td>DNF</td><td class="bibs">131</td><td class="age">27</td>
    <td class="ridername"><a href="rider/attila-valter"><span class="uppercase">Valter</span> Attila</a></td>
    <td class="cu600"><a href="team/hungary-2025">Hungary</a></td>
    <td class="uci_pnt"></td><td class="pnt"></td><td class="time ar"></td></tr>
</tbody></table>
<ul class="keyvalueList">
<li><div class="title">Date: </div><div class="value">28 September 2025</div></li>
<li><div class="title">Race category: </div><div class="value">ME - Men Elite</div></li>
</ul>
"""

OVERVIEW_HTML = """
<div class="page-title"><div class="title"><span class="flag si w32"></span><h1>Tadej Poga&#269;ar</h1></div>
<div class="subtitle"><h2>UAE Team Emirates - XRG</h2></div></div>
<ul class="list"><li><div class="bold mr5">Date of birth:</div><div>21st</div><div>September</div>
    <div>1998</div><div>(</div><div>28</div><div>)</div></li></ul>
<ul class="list"><li><div class="bold mr5">Nationality:</div><div><a href="nation/slovenia">Slovenia</a></div></li></ul>
<ul class="rider-kpi">
<li><div class="kpi">130</div><div class="title"><a href="rider/tadej-pogacar/statistics/wins">Wins</a></div></li>
<li><div class="kpi">10</div><div class="title"><a href="x">Grand tours</a></div></li>
</ul>
"""

SEASONS_HTML = """
<table class="basic"><thead><tr><th>Season</th><th>Points ■ ▲ ▼</th><th>Racedays</th>
<th>KMs</th><th>Wins ■ ▲ ▼</th><th>Top-3s</th><th>Top-10s</th></tr></thead><tbody>
<tr><td>2026</td><td>3690</td><td>45</td><td>7099</td><td>22</td><td>27</td><td>31</td></tr>
<tr><td>2018</td><td>103</td><td>14</td><td>2105</td><td>-</td><td>1</td><td>6</td></tr>
<tr><td></td><td>26502</td><td>441</td><td>72643</td><td>130</td><td>192</td><td>278</td></tr>
</tbody></table>
"""


def test_ranking_row_carries_rank_points_and_trade_team() -> None:
    entries = parse_entries(RANKING_HTML)

    assert entries["tadej-pogacar"].rank == 1
    assert entries["tadej-pogacar"].points == 11481.8
    assert entries["tadej-pogacar"].team == "UAE Team Emirates - XRG"
    assert entries["remco-evenepoel"].team_slug == "red-bull-bora-hansgrohe-2026"


def test_ranking_skips_rows_that_are_not_riders() -> None:
    assert len(parse_entries(RANKING_HTML)) == 2


def test_result_reads_the_race_name_and_information_box() -> None:
    info, _ = parse_result(RESULT_HTML)

    assert info["name"] == "92nd World Championships ME - Road Race"
    assert info["Date"] == "28 September 2025"
    assert info["Race category"] == "ME - Men Elite"


def test_result_row_renders_the_rider_as_first_name_then_surname() -> None:
    _, rows = parse_result(RESULT_HTML)

    assert rows[0].name == "Tadej Pogačar"
    assert rows[0].rank == 1
    assert rows[0].status == ""
    assert rows[0].uci_points == 900


def test_result_row_prefers_the_hidden_gap_over_the_same_time_shorthand() -> None:
    _, rows = parse_result(RESULT_HTML)

    # PCS prints ",," for a rider sharing the gap of the line above.
    assert rows[1].time == "1:45"


def test_result_row_keeps_a_did_not_finish_as_a_status_without_a_rank() -> None:
    _, rows = parse_result(RESULT_HTML)

    assert rows[2].rank is None
    assert rows[2].status == "DNF"


def test_overview_reads_age_birth_date_team_and_career_wins() -> None:
    profile = parse_overview(OVERVIEW_HTML, "tadej-pogacar")

    assert profile.age == 28
    assert profile.date_of_birth.isoformat() == "1998-09-21"
    assert profile.country_code == "SI"
    assert profile.team == "UAE Team Emirates - XRG"
    assert profile.wins_total == 130


def test_season_statistics_separate_the_career_total_from_the_seasons() -> None:
    profile = parse_overview(OVERVIEW_HTML, "tadej-pogacar")
    profile.seasons = parse_seasons(SEASONS_HTML)

    assert profile.wins_in(2026) == 22
    assert profile.wins_in(2018) == 0  # PCS writes a dash where a count is zero
    assert profile.wins_in(2019) == 0  # a season the rider has no row for
    assert profile.career_totals.wins == 130


def test_movement_is_positive_when_a_rider_gains_points_and_places() -> None:
    now = RankingEntry(slug="remco-evenepoel", rank=2, points=7921.6)
    before = RankingEntry(slug="remco-evenepoel", rank=6, points=4118.0)

    moved = movement(now, before)

    assert moved["uci_rank_change"] == 4
    assert moved["uci_points_change"] == 3803.6
    assert moved["uci_points_change_pct"] == 92.4
    assert moved["uci_rank_change_pct"] == 66.7


def test_movement_is_empty_for_a_rider_missing_from_the_reference_ranking() -> None:
    now = RankingEntry(slug="fredd-matute", rank=900, points=42.0)

    assert set(movement(now, None).values()) == {""}


def test_client_serves_a_repeated_page_from_the_cache() -> None:
    class Response:
        status_code = 200
        content = b"x" * 500
        text = "<html>cached</html>"

        def raise_for_status(self) -> None:
            pass

    class Session:
        calls = 0

        def get(self, *args: object, **kwargs: object) -> Response:
            Session.calls += 1
            return Response()

    with tempfile.TemporaryDirectory() as cache_dir:
        client = PCSClient(cache_dir=Path(cache_dir), delay=0, session=Session(), verbose=False)

        assert client.get("rider/tadej-pogacar") == "<html>cached</html>"
        assert client.get("rider/tadej-pogacar") == "<html>cached</html>"

    assert Session.calls == 1
    assert client.stats["network"] == 1
    assert client.stats["cache"] == 1
