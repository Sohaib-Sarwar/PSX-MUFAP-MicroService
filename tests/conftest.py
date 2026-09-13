"""Test fixtures backed by real upstream captures.

Every fixture in tests/fixtures/ is a verbatim response captured from the live
upstream on 2026-09-13. Tests assert exact values for known rows, which is what
catches a parser regression — a shape-only assertion would have passed happily
while the old positional fallback reported CNERGY's volume as 4.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

# Deterministic configuration for every test run.
os.environ.setdefault("SNAPSHOT_STORE", "memory")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("INTERNAL_TOKEN", "test-token")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "0")
os.environ.setdefault("SERVE_STATIC", "false")


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


def available_domains() -> tuple[str, ...]:
    """Which domain packages this branch ships.

    The split branches omit the other domain entirely, so shared tests skip
    rather than fail when a package is absent."""
    found = []
    for name in ("psx", "mufap"):
        if (ROOT / "app" / name).is_dir():
            found.append(name)
    return tuple(found)


DOMAINS = available_domains()
HAS_PSX = "psx" in DOMAINS
HAS_MUFAP = "mufap" in DOMAINS

requires_psx = pytest.mark.skipif(not HAS_PSX, reason="branch does not ship the PSX domain")
requires_mufap = pytest.mark.skipif(not HAS_MUFAP, reason="branch does not ship the MUFAP domain")


@pytest.fixture
def domains() -> tuple[str, ...]:
    return DOMAINS


@pytest.fixture
def market_watch_html() -> str:
    return fixture_text("psx_market_watch.html")


@pytest.fixture
def indices_html() -> str:
    return fixture_text("psx_indices.html")


@pytest.fixture
def symbols_payload():
    return fixture_json("psx_symbols.json")


@pytest.fixture
def timeseries_payload():
    return fixture_json("psx_timeseries_int.json")


@pytest.fixture
def mufap_tab1_html() -> str:
    return fixture_text("mufap_tab1.html")


@pytest.fixture
def mufap_tab3_html() -> str:
    return fixture_text("mufap_tab3.html")


@pytest.fixture
def stub_upstream(monkeypatch):
    """Route every upstream fetch to a captured fixture — no network in tests."""

    async def fake_text(url: str, **_):
        if "market-watch" in url:
            return fixture_text("psx_market_watch.html")
        if "/indices" in url:
            return fixture_text("psx_indices.html")
        if "tab=1" in url:
            return fixture_text("mufap_tab1.html")
        if "tab=3" in url:
            return fixture_text("mufap_tab3.html")
        raise AssertionError(f"unstubbed text fetch: {url}")

    async def fake_json(url: str, **_):
        if url.endswith("/symbols"):
            return fixture_json("psx_symbols.json")
        if "/timeseries/" in url:
            return fixture_json("psx_timeseries_int.json")
        raise AssertionError(f"unstubbed json fetch: {url}")

    modules = [f"app.{d}.service" for d in DOMAINS]
    for module in modules:
        __import__(module)
        monkeypatch.setattr(f"{module}.fetch_text", fake_text, raising=False)
        monkeypatch.setattr(f"{module}.fetch_text_browser", fake_text, raising=False)
        monkeypatch.setattr(f"{module}.fetch_json", fake_json, raising=False)

    return {"text": fake_text, "json": fake_json}
