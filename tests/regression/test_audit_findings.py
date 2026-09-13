"""One test per audit finding that can be reproduced deterministically.

Each test names the finding it locks down, so a future change that reintroduces
the defect fails with an explanation rather than a bare assertion.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.infra.store import Snapshot
from app.infra.validation import gate_batch, validate_quote
from app.main import create_app
from tests.conftest import DOMAINS, requires_mufap, requires_psx


@pytest.fixture
def client(stub_upstream):
    app = create_app(DOMAINS, title="Regression")
    with TestClient(app) as c:
        c.post("/internal/refresh", headers={"X-Internal-Token": "test-token"})
        yield c


# ── F-03 — a collapsed batch must not replace good data ───────────────────────

def test_f03_short_batch_is_rejected():
    """A parser regression yielding 3 rows must not replace 500.

    The old code guarded only `df.empty`, reported {"status": "success"}, and
    left /api/health returning 200 "healthy" with the bad data in place.
    """
    result = gate_batch("psx.stocks", new_count=3, previous_count=500,
                        min_rows=50, max_drop_ratio=0.5)
    assert not result.accepted
    assert "below the minimum" in result.reason


def test_f03_steep_drop_is_rejected_even_above_the_floor():
    result = gate_batch("psx.stocks", new_count=120, previous_count=500,
                        min_rows=50, max_drop_ratio=0.5)
    assert not result.accepted
    assert "fell" in result.reason


def test_f03_normal_variation_is_accepted():
    """A thin trading day must still publish — the guard cannot be so tight
    that it rejects legitimate data."""
    assert gate_batch("psx.stocks", 480, 500, min_rows=50, max_drop_ratio=0.5).accepted
    assert gate_batch("psx.stocks", 300, 500, min_rows=50, max_drop_ratio=0.5).accepted


def test_f03_degraded_state_is_visible(client):
    """When a refresh fails the previous data keeps serving, but the envelope
    must say `degraded` rather than continuing to claim freshness."""
    snapshot = Snapshot(dataset="psx.stocks", rows=[{"symbol": "X"}],
                        fetched_at="2026-09-13T10:00:00+05:00",
                        error="batch rejected: only 3 rows")
    envelope = snapshot.freshness(600)
    assert envelope["state"] == "degraded"
    assert "error" in envelope


# ── F-04 — startup must not block on upstream ─────────────────────────────────

def test_f04_service_answers_before_any_data_is_loaded(stub_upstream):
    """The app must bind and serve immediately.

    The old service ran both scrapes inside lifespan before yielding, so with
    slow upstreams it served nothing for up to ~350s against a 90s healthcheck
    grace period — an unrecoverable restart loop.
    """
    app = create_app(DOMAINS, title="Startup")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 503        # honest: no data yet
        first = "psx" if "psx" in DOMAINS else "mufap"
        probe = "/api/psx/stocks" if first == "psx" else "/api/mufap/funds"
        assert c.get(probe).status_code == 503


def test_f04_health_and_ready_are_different(client):
    """/health is liveness, /ready is data. Conflating them means an upstream
    outage restarts a perfectly healthy process."""
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/health").json()["status"] == "ok"


# ── F-05 — indices must actually resolve ──────────────────────────────────────

@requires_psx
def test_f05_indices_endpoint_returns_data(client):
    response = client.get("/api/psx/indices")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 18
    assert any(r["index_name"] == "KSE100" for r in body["data"])


# ── F-06 — frontend/API contract ──────────────────────────────────────────────

@requires_psx
def test_f06_index_records_carry_the_key_the_dashboard_reads(client):
    body = client.get("/api/psx/indices").json()
    for row in body["data"]:
        assert row.get("name"), "IndicesView reads idx.name"
        assert row.get("value") is not None, "IndicesView reads idx.value"


@requires_mufap
def test_f06_fund_records_carry_a_date_the_dashboard_can_read(client):
    body = client.get("/api/mufap/funds?limit=5").json()
    for row in body["data"]:
        assert "validity_date" in row


# ── F-07 — mutating endpoints require authentication ──────────────────────────

@pytest.mark.parametrize(
    "path",
    ["/internal/refresh"] + [f"/api/{d}/refresh" for d in DOMAINS],
)
def test_f07_refresh_requires_a_token(client, path):
    """Unauthenticated refresh endpoints are a way to drive unbounded traffic
    at dps.psx.com.pk and mufap.com.pk from this service's IP."""
    assert client.post(path).status_code == 401
    assert client.post(path, headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert client.post(path, headers={"X-Internal-Token": "test-token"}).status_code == 200


# ── F-08 — freshness must be explicit ─────────────────────────────────────────

def test_f08_every_list_response_carries_freshness(client):
    paths = []
    if "psx" in DOMAINS:
        paths += ["/api/psx/stocks?limit=1", "/api/psx/indices"]
    if "mufap" in DOMAINS:
        paths += ["/api/mufap/funds?limit=1", "/api/mufap/funds/categories"]
    for path in paths:
        freshness = client.get(path).json()["freshness"]
        assert freshness["state"] in {"fresh", "stale", "degraded", "unavailable"}
        assert "fetched_at" in freshness
        assert "data_as_of" in freshness


@requires_psx
def test_f08_data_as_of_differs_from_fetched_at(client):
    """`data_as_of` is when the market last traded; `fetched_at` is when we
    looked. Conflating them is what made weekend data look live."""
    freshness = client.get("/api/psx/stocks?limit=1").json()["freshness"]
    assert freshness["data_as_of"] != freshness["fetched_at"]
    assert freshness["market_status"] in {"open", "closed", "unknown"}


def test_f08_dates_are_never_defaulted_to_today():
    """The old parsers defaulted a missing source date to today, which labels
    stale data as current."""
    from app.infra.parsing import parse_date

    assert parse_date("not a date") is None
    assert parse_date("") is None
    assert parse_date("Sep 11, 2026") == "2026-09-11"


# ── F-09 — price precision ────────────────────────────────────────────────────

@requires_psx
def test_f09_prices_are_exact_not_float32(client):
    """float32 downcasting made 12.93 serialise as 12.930000305175781 and grew
    the payload 30%."""
    body = client.get("/api/psx/stocks?limit=50&sort_by=volume").json()
    cnergy = next(r for r in body["data"] if r["symbol"] == "CNERGY")
    assert cnergy["current"] == 12.93
    assert len(str(cnergy["current"])) <= 6

    raw = client.get("/api/psx/stocks?limit=50").text
    assert "0000305175781" not in raw


# ── F-12 — unknown sort field ─────────────────────────────────────────────────

@requires_psx
def test_f12_unknown_sort_field_is_an_error_not_a_silent_noop(client):
    """The old endpoint ignored an unrecognised sort_by and returned HTTP 200
    with unsorted data, so a typo produced a wrong-looking page silently."""
    response = client.get("/api/psx/stocks?sort_by=nonexistent")
    assert response.status_code == 400
    assert "Cannot sort by" in response.json()["error"]["message"]


@requires_psx
def test_f12_valid_sort_actually_sorts(client):
    body = client.get("/api/psx/stocks?sort_by=volume&ascending=false&limit=5").json()
    volumes = [r["volume"] for r in body["data"]]
    assert volumes == sorted(volumes, reverse=True)


# ── F-13 — one response envelope across both domains ──────────────────────────

@requires_psx
@requires_mufap
def test_f13_list_envelopes_are_identical_across_domains(client):
    psx = client.get("/api/psx/stocks?limit=1").json()
    mufap = client.get("/api/mufap/funds?limit=1").json()
    expected = {"count", "total_filtered", "total", "offset", "limit", "freshness", "data"}
    assert expected <= set(psx)
    assert expected <= set(mufap)


# ── F-14 — record validation ──────────────────────────────────────────────────

def test_f14_impossible_quotes_are_rejected():
    assert validate_quote({"current": -5}) is not None
    assert validate_quote({"current": 10, "low": 20, "high": 5}) is not None
    assert validate_quote({"current": 10, "change_pct": 5000}) is not None


def test_f14_illiquid_quotes_are_kept():
    """An illiquid instrument legitimately shows a current price outside the
    session high/low — NAGC had high=0, low=0, current=77.00. Rejecting those
    would discard real quotes."""
    assert validate_quote({"current": 77.0, "low": 0.0, "high": 0.0, "volume": 0}) is None


# ── F-19 — no unauthenticated introspection ───────────────────────────────────

def test_f19_debug_memory_endpoint_is_gone(client):
    assert client.get("/api/debug/memory").status_code == 404


# ── F-24 — 404 body shape ─────────────────────────────────────────────────────

def test_f24_unknown_api_path_returns_structured_json(client):
    response = client.get(f"/api/{DOMAINS[0]}/definitely-not-a-route")
    assert response.status_code == 404
    assert "error" in response.json()


@requires_psx
def test_errors_never_leak_internals(client):
    response = client.get("/api/psx/stocks/NOSUCHSYMBOL")
    assert response.status_code == 404
    body = response.text
    assert "Traceback" not in body
    assert "dps.psx.com.pk" not in body
