#!/usr/bin/env python3
"""Notice when the data has stopped arriving, and say why.

On 2026-10-30 this service served a five-hour-old PSX board and a two-day-old
MUFAP snapshot, and nothing anywhere reported a problem. Both trigger paths had
failed at once — the Cloudflare cron stopped firing after 2026-10-29T19:00Z and
GitHub's own schedule delivered nothing that day — and the only reason anyone
found out was a person looking at the dashboard. Every individual workflow run
had reported success, because a run that keeps the previous snapshot when the
fetch fails *is* succeeding at what it was asked to do.

So this does not ask "did the last run pass". It asks the only question that
matters to a consumer: **is the published data as recent as it promises to be**,
and if not, which of the two very different reasons is it.

The two reasons need opposite responses, which is the whole point of separating
them:

  LATE     Nothing fetched when it should have. The upstream is fine; the
           trigger did not fire. Re-dispatching is the correct repair and this
           script asks for one.

  BLOCKED  A fetch ran and the upstream refused it. Re-dispatching achieves
           nothing except more requests at a host that has already said no, so
           this script explicitly does not ask for one. It reports, and a human
           decides.

Derived fields in the published envelope are not trusted. `state`,
`overdue_seconds` and `on_schedule` are computed when the site is built and
then frozen into a static file, so hours later they describe the moment of the
build rather than now — at the time of writing `psx.stocks` carried
`overdue_seconds: 0` while being five hours overdue by its own
`next_refresh_at`. Only the absolute instants are read: `fetched_at`,
`next_refresh_at`, `data_as_of`.

Writes `verdict`, `late`, `blocked`, `healthy` and `report` to GITHUB_OUTPUT.
Exit status is always 0 — a watchdog that fails its own job must not look like
the thing it is watching.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Which domain to re-dispatch for a given dataset.
DOMAIN_OF = {
    "psx.stocks": "psx",
    "psx.indices": "psx",
    "psx.session": "psx",
    "mufap.funds": "mufap",
}

# How far past `next_refresh_at` is tolerable before it is worth saying so.
#
# Generous on purpose. GitHub's scheduler is the backstop and it routinely runs
# this repository's jobs hours late, so a tighter grace would alert on the
# normal behaviour of the system rather than on a fault. Three hours is longer
# than any delivery delay measured here and far shorter than the two days the
# MUFAP outage went unnoticed.
GRACE = timedelta(hours=3)

# Past this, the data is not merely late, it is not worth serving as current.
SEVERE = timedelta(hours=12)


def emit(**values: object) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    for key, value in values.items():
        print(f"-> {key}={value}")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                rendered = str(value)
                if "\n" in rendered:
                    # Multi-line values need the heredoc form or the runner
                    # silently truncates them at the first newline.
                    handle.write(f"{key}<<WATCHDOG_EOF\n{rendered}\nWATCHDOG_EOF\n")
                else:
                    handle.write(f"{key}={rendered}\n")
    except OSError:
        pass


def summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


def moment(value: object) -> datetime | None:
    """Parse an instant from the envelope, tolerating a bare date."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A bare `data_as_of` date is a Pakistan date; reading it as UTC would
        # make it look five hours older than it is.
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=5)))
    return parsed


def get_json(url: str, timeout: int = 30) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            # Published through a CDN that will happily hand back the copy
            # whose staleness is the thing being measured.
            "cache-control": "no-cache",
            "user-agent": "pk-finance-watchdog",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def describe(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{delta.total_seconds() / 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def classify(name: str, entry: dict, now: datetime) -> dict:
    """One dataset's verdict, derived from absolute instants only."""
    # `stale_at` is the deadline including the schedule's own grace, published
    # as an absolute instant precisely so a monitor can decide liveness with one
    # comparison. `next_refresh_at` is the fallback for an envelope built before
    # that field existed, and it has no grace in it — hence GRACE below.
    deadline = moment(entry.get("stale_at"))
    due = moment(entry.get("next_refresh_at"))
    fetched = moment(entry.get("fetched_at"))
    state = str(entry.get("state") or "unknown")
    error = entry.get("error")

    if deadline:
        overdue = (now - deadline) if now > deadline else timedelta(0)
        tolerance = timedelta(0)
    else:
        overdue = (now - due) if due and now > due else timedelta(0)
        tolerance = GRACE
    age = (now - fetched) if fetched else None

    # `degraded` means a fetch ran and the upstream would not serve it. That is
    # the source's decision, not a trigger failure, and more requests will not
    # change it.
    if state == "degraded" or error:
        verdict = "blocked"
    elif state == "unavailable":
        # No rows at all. Whatever should have produced them did not, and an
        # envelope in this state carries no deadline to be late against — so
        # without this it reads as "nothing overdue", which is how an empty
        # dataset would have been reported as healthy.
        verdict = "late"
    elif not deadline and not due:
        # Nothing to measure against. Freshness cannot be demonstrated, and a
        # watchdog that treats "I cannot tell" as "fine" is not a watchdog.
        verdict = "late"
    elif overdue > tolerance:
        verdict = "late"
    else:
        verdict = "ok"

    return {
        "dataset": name,
        "domain": DOMAIN_OF.get(name, name.split(".")[0]),
        "verdict": verdict,
        "state": state,
        "severe": overdue > SEVERE,
        "overdue": overdue,
        "age": age,
        "due_at": due.isoformat() if due else None,
        "error": str(error)[:400] if error else None,
        "fetched_at": fetched.isoformat() if fetched else None,
        "records": entry.get("record_count"),
    }


def render(rows: list[dict], now: datetime, site: str) -> str:
    lines = [
        "| Dataset | Verdict | Overdue | Last fetch | Rows |",
        "|---|---|---|---|---|",
    ]
    mark = {"ok": "ok", "late": "**LATE**", "blocked": "**BLOCKED**"}
    for row in sorted(rows, key=lambda r: r["dataset"]):
        overdue = describe(row["overdue"]) if row["overdue"] else "—"
        age = f"{describe(row['age'])} ago" if row["age"] else "never"
        lines.append(
            f"| `{row['dataset']}` | {mark[row['verdict']]} | {overdue} | {age} | "
            f"{row['records'] or '—'} |"
        )
    lines.append("")
    lines.append(f"Checked {now.isoformat(timespec='seconds')} against {site}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default=os.getenv("SITE_URL", ""))
    args = parser.parse_args()

    site = args.site.rstrip("/")
    if not site:
        print("no --site given; nothing to check")
        emit(verdict="unknown", late="", blocked="", healthy="false",
             report="Watchdog could not run: no site URL configured.")
        return 0

    url = f"{site}/api/freshness.json"
    now = datetime.now(timezone.utc)

    try:
        body = get_json(url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        # The site being unreachable is itself the alarm, and a louder one than
        # anything inside it. Reported, never raised: an exception here would
        # read as "the watchdog is broken".
        report = (
            f"**The published API could not be read.**\n\n"
            f"`GET {url}` failed: `{type(exc).__name__}: {exc}`\n\n"
            f"Until this resolves, nothing can be said about data freshness — "
            f"the site itself may be down."
        )
        print(report)
        emit(verdict="unreachable", late="", blocked="", healthy="false", report=report)
        summary("## Watchdog\n\n" + report)
        return 0

    datasets = (body or {}).get("datasets") or {}
    if not isinstance(datasets, dict) or not datasets:
        report = f"`{url}` carried no datasets. The publish step may be writing an empty envelope."
        emit(verdict="empty", late="", blocked="", healthy="false", report=report)
        summary("## Watchdog\n\n" + report)
        return 0

    rows = [classify(name, entry, now) for name, entry in datasets.items()
            if isinstance(entry, dict)]

    late = sorted({r["domain"] for r in rows if r["verdict"] == "late"})
    blocked = sorted({r["domain"] for r in rows if r["verdict"] == "blocked"})
    healthy = not late and not blocked

    table = render(rows, now, site)
    if healthy:
        verdict = "ok"
        report = "Every dataset is within its promised refresh window.\n\n" + table
    else:
        verdict = "degraded"
        parts = []
        if late:
            worst = max((r["overdue"] for r in rows if r["verdict"] == "late"),
                        default=timedelta(0))
            parts.append(
                f"**Late — nothing fetched when it should have ({', '.join(late)}).** "
                f"Worst case is {describe(worst)} past due. The upstream is not "
                f"implicated: no fetch was attempted. This is the trigger path, so "
                f"the watchdog has asked for a re-run."
            )
        if blocked:
            reasons = {r["error"] for r in rows if r["verdict"] == "blocked" and r["error"]}
            detail = ""
            if reasons:
                quoted = "\n".join(f"> {reason}" for reason in sorted(reasons))
                detail = "\n\nWhat the source said:\n\n" + quoted
            parts.append(
                f"**Blocked — a fetch ran and the source refused it "
                f"({', '.join(blocked)}).** Re-running achieves nothing except more "
                f"requests at a host that has already declined them, so no re-run has "
                f"been requested. This needs a decision about how the data is "
                f"obtained, not a retry." + detail
            )
        report = "\n\n".join(parts) + "\n\n" + table

    print(report)
    emit(
        verdict=verdict,
        late=",".join(late),
        blocked=",".join(blocked),
        healthy="true" if healthy else "false",
        report=report,
    )
    summary("## Watchdog\n\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
