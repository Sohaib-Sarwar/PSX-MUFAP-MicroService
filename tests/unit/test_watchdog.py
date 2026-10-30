"""The watchdog's judgement, which is the only part of it that can be wrong.

Its two verdicts trigger opposite actions — `late` asks for another run,
`blocked` deliberately does not — so confusing them either hammers a source
that has already refused, or sits quietly while nothing is running at all. The
second is what happened on 2026-10-30, for twenty-two hours.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PKT = timezone(timedelta(hours=5))

_spec = importlib.util.spec_from_file_location(
    "watchdog",
    Path(__file__).resolve().parents[2] / ".github" / "scripts" / "watchdog.py",
)
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)


NOW = datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc)


def entry(**overrides):
    """A healthy dataset, fetched an hour ago and due in an hour."""
    base = {
        "state": "fresh",
        "fetched_at": (NOW - timedelta(hours=1)).isoformat(),
        "stale_at": (NOW + timedelta(hours=1)).astimezone(PKT).isoformat(),
        "next_refresh_at": (NOW + timedelta(hours=1)).astimezone(PKT).isoformat(),
        "record_count": 753,
    }
    base.update(overrides)
    return base


def verdict(**overrides):
    return watchdog.classify("psx.stocks", entry(**overrides), NOW)["verdict"]


def test_a_dataset_inside_its_window_is_ok():
    assert verdict() == "ok"


def test_past_the_deadline_is_late():
    assert verdict(stale_at=(NOW - timedelta(hours=4)).astimezone(PKT).isoformat()) == "late"


def test_a_degraded_dataset_is_blocked_not_late():
    """The source refused a fetch. Asking again is just more refused requests."""
    assert verdict(
        state="degraded",
        stale_at=(NOW - timedelta(days=2)).astimezone(PKT).isoformat(),
    ) == "blocked"


def test_an_error_marks_it_blocked_even_if_the_state_field_disagrees():
    assert verdict(state="fresh", error="HTTP 403 from upstream bot management") == "blocked"


def test_the_frozen_derived_fields_are_not_believed():
    """The 2026-10-30 case, exactly.

    `psx.stocks` carried `overdue_seconds: 0`, `on_schedule: true` and
    `state: "fresh"` while sitting five hours past its own deadline — those
    three are computed when the site is built and a static file cannot tick.
    A watchdog that reads them sees a healthy service and says nothing.
    """
    stale = entry(
        state="fresh",
        on_schedule=True,
        overdue_seconds=0,
        stale_at=(NOW - timedelta(hours=5)).astimezone(PKT).isoformat(),
    )
    row = watchdog.classify("psx.stocks", stale, NOW)
    assert row["verdict"] == "late"
    assert row["overdue"] == timedelta(hours=5)


def test_stale_at_wins_over_next_refresh_at():
    """`stale_at` carries the schedule's grace; `next_refresh_at` does not."""
    row = watchdog.classify(
        "psx.stocks",
        entry(
            stale_at=(NOW + timedelta(hours=2)).astimezone(PKT).isoformat(),
            next_refresh_at=(NOW - timedelta(hours=6)).astimezone(PKT).isoformat(),
        ),
        NOW,
    )
    assert row["verdict"] == "ok"


def test_an_old_envelope_without_stale_at_still_works():
    """Published before the field existed. Falls back, and applies the grace."""
    old = entry(next_refresh_at=(NOW - timedelta(hours=1)).astimezone(PKT).isoformat())
    del old["stale_at"]
    assert watchdog.classify("psx.stocks", old, NOW)["verdict"] == "ok"

    older = entry(next_refresh_at=(NOW - timedelta(hours=9)).astimezone(PKT).isoformat())
    del older["stale_at"]
    assert watchdog.classify("psx.stocks", older, NOW)["verdict"] == "late"


def test_grace_is_wide_enough_to_ignore_github_being_late():
    """GitHub delivers this repository's crons hours late as a matter of course.

    Alerting on that would alert on the system working normally, which trains
    whoever reads the alerts to stop reading them.
    """
    assert watchdog.GRACE >= timedelta(hours=2)


@pytest.mark.parametrize(
    "name, expected",
    [("psx.stocks", "psx"), ("psx.indices", "psx"), ("mufap.funds", "mufap")],
)
def test_each_dataset_knows_which_scrape_to_ask_for(name, expected):
    assert watchdog.classify(name, entry(), NOW)["domain"] == expected


def test_a_bare_date_is_read_as_pakistan_time():
    """MUFAP dates `data_as_of` as a plain date. Reading it as UTC would age it
    five hours and could tip a healthy dataset over its deadline."""
    assert watchdog.moment("2026-10-28").utcoffset() == timedelta(hours=5)


def test_unparseable_instants_do_not_raise():
    for value in (None, "", "not a date", 42, {}):
        assert watchdog.moment(value) is None


def test_a_dataset_with_no_timestamps_at_all_is_not_called_healthy():
    row = watchdog.classify("psx.stocks", {"state": "unavailable"}, NOW)
    assert row["verdict"] != "ok"


def test_render_names_every_dataset_it_was_given():
    rows = [
        watchdog.classify("psx.stocks", entry(), NOW),
        watchdog.classify("mufap.funds", entry(state="degraded"), NOW),
    ]
    table = watchdog.render(rows, NOW, "https://example.test")
    assert "psx.stocks" in table
    assert "mufap.funds" in table
    assert "BLOCKED" in table
