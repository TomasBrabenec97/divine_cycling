import argparse
from pathlib import Path

from app.db import init_db
from app.importers.road_worlds_2026 import import_startlist

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="One-off 2026 Road Worlds startlist and UCI ranking import"
    )
    parser.add_argument("pcs_html", type=Path, help="Saved PCS startlist HTML")
    parser.add_argument(
        "--without-uci",
        action="store_true",
        help="Import only the saved PCS startlist when the UCI public endpoint is unavailable",
    )
    args = parser.parse_args()
    init_db()
    summary = import_startlist(args.pcs_html, include_uci=not args.without_uci)
    print(
        "Imported {startlist_riders} PCS riders; matched {uci_matches} to UCI ranking "
        "snapshot {uci_snapshot}.".format(**summary)
    )
