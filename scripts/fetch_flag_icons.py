"""Create local PNG flag assets for every country in the Road Worlds CSV.

PNG is deliberately used for inline application imagery: it is supported by
Chromium, GitHub Pages and Windows image viewers without an ICO decoder.

    python -m scripts.fetch_flag_icons
"""

import csv
from pathlib import Path
from urllib.request import Request, urlopen


SOURCE_CSV = Path("exports/road-worlds-2026-startlist.csv")
OUTPUT_DIR = Path("frontend/flags")
FLAG_URL = "https://flagcdn.com/w40/{country}.png"


def main() -> None:
    with SOURCE_CSV.open(newline="", encoding="utf-8-sig") as file:
        countries = sorted({row["country_code"].strip().lower() for row in csv.DictReader(file)})
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for country in countries:
        request = Request(FLAG_URL.format(country=country), headers={"User-Agent": "Ten-Up flag importer"})
        with urlopen(request, timeout=30) as response:
            png = response.read()
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"Flag source did not return a PNG for {country.upper()}")
        (OUTPUT_DIR / f"{country}.png").write_bytes(png)
        print(f"Wrote {country}.png")
    print(f"Created {len(countries)} PNG flags in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
