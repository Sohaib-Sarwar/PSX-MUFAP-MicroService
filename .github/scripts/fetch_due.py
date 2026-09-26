#!/usr/bin/env python3
"""Decide whether a scrape is due, before the job installs anything.

GitHub drops most of this repository's scheduled fires and delays the rest by
hours, so the cron deliberately asks for far more fires than the data needs.
That only works if a fire nobody needs is cheap, and it is only cheap if the
decision is made before `setup-python` and `pip install` — which is why this is
a standard-library script run by the runner's preinstalled python rather than
part of scripts/scrape.py.

The question it answers is "has a fetch succeeded recently", not "does the
published data look current". Those are different: a MUFAP correction issued at
22:00 to a NAV struck at 18:00 leaves the data looking perfectly current while
being wrong, which is exactly why the date-based check was removed.

Writes `due`, `status`, `records` and `age_minutes` to GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

# The dataset whose fetch time speaks for the whole domain.
PRIMARY = {"psx": "psx.stocks", "mufap": "mufap.funds"}


def emit(**values: object) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    rendered = " ".join(f"{k}={v}" for k, v in values.items())
    print(f"-> {rendered}")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
    except OSError:
        # A job that cannot write its outputs should still run the scrape
        # rather than silently decide not to.
        pass


def annotate(message: str) -> None:
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::notice::{message}", flush=True)


def summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, choices=sorted(PRIMARY))
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--min-interval-minutes", default="0")
    parser.add_argument("--force", default="false")
    args = parser.parse_args()

    forced = str(args.force).strip().lower() in {"1", "true", "yes", "on"}
    try:
        interval = float(args.min_interval_minutes or 0)
    except ValueError:
        interval = 0.0

    if forced or interval <= 0:
        emit(due="true", status="", records="")
        print(f"{args.domain}: fetching ({'forced' if forced else 'no interval set'})")
        return 0

    snapshot = Path(args.data_dir) / f"{PRIMARY[args.domain]}.json"
    if not snapshot.is_file():
        emit(due="true", status="", records="")
        print(f"{args.domain}: no published snapshot — fetching")
        return 0

    try:
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # An unreadable snapshot is a reason to fetch, not a reason to stop.
        emit(due="true", status="", records="")
        print(f"{args.domain}: snapshot unreadable ({exc}) — fetching")
        return 0

    rows = payload.get("rows") or []
    fetched_at = payload.get("fetched_at")
    if not rows or payload.get("error") or not fetched_at:
        emit(due="true", status="", records="")
        print(f"{args.domain}: last snapshot is empty or degraded — fetching")
        return 0

    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        emit(due="true", status="", records="")
        print(f"{args.domain}: unparseable fetched_at {fetched_at!r} — fetching")
        return 0

    age_minutes = (datetime.now(fetched.tzinfo) - fetched).total_seconds() / 60
    if age_minutes >= interval:
        emit(due="true", status="", records="", age_minutes=round(age_minutes))
        print(f"{args.domain}: last fetch {age_minutes:.0f}m ago "
              f"(interval {interval:.0f}m) — fetching")
        return 0

    waiting = interval - age_minutes
    emit(due="false", status="skipped", records=len(rows), age_minutes=round(age_minutes))
    annotate(f"{args.domain}: fetched {age_minutes:.0f}m ago, next due in "
             f"{waiting:.0f}m — nothing to do")
    summary([f"### ⏱️ {args.domain.upper()} not due", "",
             f"Last fetch was **{age_minutes:.0f} minutes** ago; the minimum "
             f"interval is **{interval:.0f} minutes**.",
             f"Next fetch due in **{waiting:.0f} minutes**. "
             f"Records held: **{len(rows):,}**. Upstream requests: **0**.", ""])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
