"""Small helpers shared by the ProCyclingStats page parsers."""

from __future__ import annotations

import re

from bs4 import Tag

# PCS writes a dash where a count is zero and leaves cells blank where a value
# does not apply; both mean "no number here".
BLANK = {"", "-", "–", "—"}

_SORT_GLYPHS = re.compile(r"[\u25a0\u25b2\u25bc]")


def text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def header_label(node: Tag | None) -> str:
    """A table header without the little sort squares PCS appends to it."""
    return _SORT_GLYPHS.sub("", text(node)).strip()


def as_int(value: str | None) -> int | None:
    value = (value or "").strip().replace(",", "")
    if value in BLANK:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def as_float(value: str | None) -> float | None:
    value = (value or "").strip().replace(",", "")
    if value in BLANK:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def slug_from_href(href: str | None, prefix: str) -> str:
    """Pull ``<slug>`` out of a relative PCS href such as ``rider/<slug>``."""
    if not href or prefix not in href:
        return ""
    slug = href.split(prefix, 1)[1].split("?")[0].strip("/")
    # Deeper links (rider/<slug>/statistics) are not rider identities.
    return "" if "/" in slug else slug


def rider_slug(node: Tag | None) -> str:
    """The rider slug of the first ``rider/<slug>`` link inside ``node``."""
    if node is None:
        return ""
    for link in node.find_all("a", href=True):
        slug = slug_from_href(link["href"], "rider/")
        if slug:
            return slug
    return ""


def team_from(node: Tag | None) -> tuple[str, str]:
    """Return ``(display name, team slug)`` for the first team link in ``node``."""
    if node is None:
        return "", ""
    link = node.find("a", href=lambda h: h and "team/" in h)
    if link is None:
        return text(node), ""
    return text(link), link["href"].split("team/", 1)[1].split("?")[0].strip("/")


def select_values(soup, name: str) -> list[str]:
    """Option values of ``<select name=...>`` -- PCS publishes its own paging."""
    select = soup.find("select", attrs={"name": name})
    if select is None:
        return []
    return [option.get("value", "") for option in select.find_all("option")]


def keyvalue_list(soup) -> dict[str, str]:
    """The ``title: value`` pairs PCS renders in its side information boxes."""
    out: dict[str, str] = {}
    for item in soup.select("ul.keyvalueList li"):
        title = item.find("div", class_="title")
        value = item.find("div", class_="value")
        if title is not None and value is not None:
            out[text(title).rstrip(":").strip()] = text(value)
    return out
