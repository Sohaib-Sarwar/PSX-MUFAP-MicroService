"""Parsing primitives shared by both scrapers.

Deliberately strict: a value that cannot be parsed becomes None rather than a
silently wrong number. For financial data a missing field is recoverable and a
wrong one is not.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

_BLANK = {"", "-", "--", "n/a", "na", "nil", "none"}

_DATE_FORMATS = (
    "%b %d, %Y", "%b %d %Y", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d",
    "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y",
)


def parse_number(text: str | None) -> float | None:
    """Parse an upstream numeric cell.

    Handles thousands separators, percent signs and accounting negatives:
    MUFAP renders a negative return as "(6.21)", which must become -6.21 and
    not +6.21.
    """
    if text is None:
        return None
    cleaned = text.strip()
    if cleaned.lower() in _BLANK:
        return None

    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]

    cleaned = cleaned.replace(",", "").replace("%", "").replace("+", "").strip()
    if cleaned.lower() in _BLANK:
        return None
    try:
        value = float(cleaned)
    except (TypeError, ValueError):
        return None
    return -value if negative else value


def parse_int(text: str | None) -> int | None:
    value = parse_number(text)
    return None if value is None else int(value)


def parse_date(text: str | None) -> str | None:
    """Return ISO YYYY-MM-DD, or None.

    Never falls back to today. Defaulting a missing source date to the current
    date is how stale data ends up labelled as current; the caller decides what
    a missing date means.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.lower() in _BLANK:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def table_rows(table) -> list[list[str]]:
    """Cell text for every row in a table body, whitespace-normalised."""
    body = table.find("tbody") or table
    out: list[list[str]] = []
    for row in body.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if cells:
            out.append([clean_text(c.get_text(" ", strip=True)) for c in cells])
    return out


def header_cells(table) -> list[str]:
    head = table.find("thead")
    if not head:
        return []
    return [
        clean_text(th.get_text(" ", strip=True)).lower()
        for th in head.find_all(["th", "td"])
    ]


class ColumnMapError(RuntimeError):
    """A required column was absent from the upstream header row.

    Raised rather than falling back to positional guessing. A positional
    fallback produces plausible but wrong numbers, which is the worst possible
    outcome for financial data; an exception produces an alert instead.
    """


def build_column_map(
    headers: list[str],
    spec: dict[str, tuple[str, ...]],
    required: tuple[str, ...],
) -> dict[str, int]:
    """Map a logical field name to its column index.

    `spec` maps each field to the header texts that identify it, most specific
    first. Every field is resolved by exact match before any prefix matching is
    attempted, so a short name like "nav" cannot claim a column that exactly
    matches a longer, more specific header.
    """
    mapping: dict[str, int] = {}
    used: set[int] = set()

    def claim(field: str, predicate) -> bool:
        for index, header in enumerate(headers):
            if index in used:
                continue
            if predicate(header):
                mapping[field] = index
                used.add(index)
                return True
        return False

    for pass_predicate in (
        lambda candidate: (lambda header: header == candidate),
        lambda candidate: (lambda header: header.startswith(candidate)),
        lambda candidate: (lambda header: candidate in header),
    ):
        for field, candidates in spec.items():
            if field in mapping:
                continue
            for candidate in candidates:
                if claim(field, pass_predicate(candidate)):
                    break

    missing = [f for f in required if f not in mapping]
    if missing:
        raise ColumnMapError(
            f"upstream header changed; missing columns {missing} in {headers!r}"
        )
    return mapping


def today_pkt() -> date:
    from .config import now_pkt

    return now_pkt().date()
