from app.importers.road_worlds_2026 import parse_pcs_startlist


def test_parse_pcs_startlist_extracts_rider_slug_name_and_nation() -> None:
    html = """<li class=" "><span class="bib">-</span><span class="flag be"></span><a href="rider/remco-evenepoel">EVENEPOEL Remco</a></li>"""
    riders = parse_pcs_startlist(html)
    assert riders[0].slug == "remco-evenepoel"
    assert riders[0].display_name == "EVENEPOEL Remco"
    assert riders[0].nation == "BE"
