#!/usr/bin/env python3
"""Run one domain's refresh as a batch job and leave the result on disk.

This is the microservice's scheduled half. The long-running deployment keeps a
scheduler in-process; a GitHub Actions run is instead a fresh process that
starts, fetches once, writes a snapshot and exits. The pipeline in between —
fetch, parse, per-record validation, the batch publication gate, the freshness
envelope — is the same code the HTTP service runs. Nothing is reimplemented
here; this file only supplies the process lifecycle around it.

    python scripts/scrape.py --domain psx
    python scripts/scrape.py --domain mufap --min-interval-minutes 45

Exit codes
    0   a snapshot was published, or the run was skipped because one happened
        within the minimum interval, or the fetch failed but last-known-good is
        still being served (reported as a warning, not a failure — the site is
        serving correct, clearly-labelled data and a red run would be noise)
    1   there is no usable data for this dataset at all
    2   the arguments were wrong
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOMAINS = ("psx", "mufap")

# The MUFAP window runs 18:00 → 00:00 PKT, so the run that fires at midnight
# belongs to the session that just ended, not to the date that just began.
# Shifting the clock back six hours puts every run in the window on the same
# session date without a calendar.
SESSION_DAY_OFFSET = timedelta(hours=6)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", required=True, choices=DOMAINS,
                        help="which upstream to refresh")
    parser.add_argument("--data-dir", default=os.getenv("SNAPSHOT_DIR", "data"),
                        help="directory holding one JSON snapshot per dataset")
    parser.add_argument("--min-interval-minutes", type=float, default=0.0,
                        help="skip if the last successful fetch is younger than "
                             "this. Guards against a dense cron double-fetching, "
                             "NOT against the data looking current — a fetch is "
                             "always made once the interval has elapsed, whatever "
                             "date the source reports")
    parser.add_argument("--force", action="store_true",
                        help="fetch regardless of when the last one happened")
    parser.add_argument("--max-memory-mb", type=int,
                        default=int(os.getenv("MAX_MEMORY_MB", "0") or 0),
                        help="hard address-space ceiling; 0 disables it")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


# ── GitHub Actions reporting ──────────────────────────────────────────────────
# Both are no-ops outside Actions, so the script behaves identically when run
# by hand.

def annotate(level: str, message: str) -> None:
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{message}", flush=True)


def write_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # a summary that cannot be written must not fail the job


def set_output(**values: object) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
    except OSError:
        pass


# ── the run ───────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> int:
    # Configuration is environment-driven, and `get_settings()` is cached on
    # first call, so everything the run depends on is set before the app
    # package is imported.
    os.environ["SNAPSHOT_STORE"] = "file"
    os.environ["SNAPSHOT_DIR"] = args.data_dir
    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ.setdefault("LOG_FORMAT", "text")
    os.environ["LOG_LEVEL"] = args.log_level

    from app.infra import limits
    from app.infra.browser_http import close_browser_client
    from app.infra.config import get_settings, now_pkt
    from app.infra.http import close_client
    from app.infra.logging_setup import configure_logging
    from app.infra.store import get_store

    configure_logging()
    log = logging.getLogger("scrape")
    cap = limits.apply_memory_cap(args.max_memory_mb)

    settings = get_settings()
    session_date = (now_pkt() - SESSION_DAY_OFFSET).date().isoformat()
    store = get_store()

    datasets = {"psx": ("psx.stocks", "psx.indices"), "mufap": ("mufap.funds",)}[args.domain]
    primary = datasets[0]

    # ── interval check ────────────────────────────────────────────────────
    # The cron fires far more often than the data changes, deliberately: GitHub
    # drops most scheduled fires, so asking for many is the only way to land
    # near the intended times. This is what keeps the extra fires from turning
    # into extra load on PSX and MUFAP.
    if args.min_interval_minutes > 0 and not args.force:
        existing = await store.get(primary)
        age_minutes = None
        if existing and existing.rows and not existing.error:
            age = existing.age_seconds()
            age_minutes = age / 60 if age is not None else None

        if age_minutes is not None and age_minutes < args.min_interval_minutes:
            wait = args.min_interval_minutes - age_minutes
            message = (f"{args.domain}: last fetch was {age_minutes:.0f}m ago, "
                       f"minimum interval is {args.min_interval_minutes:.0f}m — "
                       f"next fetch due in {wait:.0f}m")
            log.info("scrape_not_due", extra={"dataset": primary,
                                              "age_minutes": round(age_minutes)})
            annotate("notice", message)
            write_summary([f"### ⏱️ {args.domain.upper()} not due yet", "",
                           f"Last fetched **{age_minutes:.0f} minutes** ago; the minimum "
                           f"interval is **{args.min_interval_minutes:.0f} minutes**.",
                           f"Records held: **{existing.count:,}** · Upstream requests: **0**",
                           ""])
            set_output(status="skipped", records=existing.count,
                       as_of=existing.data_as_of or "")
            print(json.dumps({"domain": args.domain, "status": "skipped",
                              "reason": "not due", "age_minutes": round(age_minutes),
                              "records": existing.count}))
            return 0

    # ── refresh ───────────────────────────────────────────────────────────
    results: dict[str, dict[str, object]] = {}
    try:
        if args.domain == "psx":
            from app.psx import service as psx

            stocks = await psx.refresh_stocks(force=args.force)
            results["psx.stocks"] = _describe(stocks)
            # Released before the second fetch so the two datasets' rows are
            # never both resident: this is the run's high-water mark otherwise.
            del stocks
            limits.release()

            indices = await psx.refresh_indices(force=args.force)
            results["psx.indices"] = _describe(indices)
            del indices
        else:
            from app.mufap import service as mufap

            funds = await mufap.refresh_funds(force=args.force)
            results["mufap.funds"] = _describe(funds)
            del funds
    finally:
        await close_client()
        close_browser_client()
        limits.release()

    peak = limits.peak_rss_mb()
    log.info("scrape_complete", extra={"domain": args.domain, "peak_rss_mb": peak,
                                       "memory_cap_mb": cap})

    primary_result = results[primary]
    has_data = bool(primary_result["records"])
    degraded = bool(primary_result["error"])

    _report(args.domain, results, peak=peak, cap=cap,
            store_dir=settings.snapshot_dir, session_date=session_date)
    print(json.dumps({"domain": args.domain, "datasets": results,
                      "peak_rss_mb": peak}, default=str))

    if not has_data:
        annotate("error", f"{args.domain}: no usable data — "
                          f"{primary_result['error'] or 'the upstream returned nothing'}")
        set_output(status="failed", records=0)
        return 1

    if degraded:
        annotate("warning",
                 f"{args.domain}: upstream refresh failed; continuing to publish the "
                 f"last good snapshot ({primary_result['records']} records, "
                 f"as of {primary_result['as_of']}). Reason: {primary_result['error']}")
        set_output(status="degraded", records=primary_result["records"])
        return 0

    set_output(status="ok", records=primary_result["records"],
               as_of=primary_result["as_of"] or "")
    return 0


def _describe(snapshot: object) -> dict[str, object]:
    return {
        "records": getattr(snapshot, "count", 0),
        "as_of": getattr(snapshot, "data_as_of", None),
        "fetched_at": getattr(snapshot, "fetched_at", None),
        "error": getattr(snapshot, "error", None),
    }


def _report(domain: str, results: dict[str, dict[str, object]], *,
            peak: float | None, cap: int | None, store_dir: str,
            session_date: str) -> None:
    ok = all(not r["error"] and r["records"] for r in results.values())
    icon = "✅" if ok else ("⚠️" if any(r["records"] for r in results.values()) else "❌")

    lines = [f"### {icon} {domain.upper()} refresh", "",
             "| Dataset | Records | Data as of | State |",
             "| --- | ---: | --- | --- |"]
    for name, result in results.items():
        state = "degraded" if result["error"] else ("fresh" if result["records"] else "unavailable")
        lines.append(f"| `{name}` | {int(result['records'] or 0):,} | "
                     f"{result['as_of'] or '—'} | {state} |")

    footer = [f"Session date `{session_date}` · snapshots in `{store_dir}/`"]
    if peak is not None:
        footer.append(f"peak RSS **{peak} MiB**" + (f" (cap {cap} MiB)" if cap else ""))
    lines += ["", " · ".join(footer), ""]

    for result in results.values():
        if result["error"]:
            lines += [f"> Upstream error: `{result['error']}`", ""]

    write_summary(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except MemoryError:
        annotate("error", "the run exceeded its memory ceiling "
                          f"({args.max_memory_mb} MiB) — raise MAX_MEMORY_MB or "
                          "investigate what grew")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
