"""Publication gate — decides whether a freshly parsed batch may replace the
current snapshot, and whether an individual record is trustworthy.

This is the guard that stops a parser regression from silently replacing 500
stocks with 3. The previous implementation only checked `df.empty`, so any
non-empty result was published as a success.

Two levels, deliberately separated:

  REJECT   data that is impossible — a negative price, a low above a high, a
           move far outside any circuit breaker. These are parser errors.

  FLAG     data that is unusual but real. An illiquid PSX instrument routinely
           shows a `current` price outside the session's high/low, because
           `current` falls back to the last traded price while high/low cover
           only today's (possibly zero) trades. NAGC on the verified sample had
           high=0, low=0, current=77.00. Rejecting these would discard
           legitimate quotes for thinly traded stocks, which is its own
           correctness bug — so they are annotated and counted, never dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .metrics import metrics

logger = logging.getLogger(__name__)

# Wider than PSX's ±10% circuit breaker on purpose: rights issues, newly listed
# scrips and post-corporate-action quotes legitimately move further, and a rule
# change upstream should not start discarding good data.
MAX_PLAUSIBLE_CHANGE_PCT = 100.0


@dataclass(slots=True)
class GateResult:
    accepted: bool
    reason: str | None = None


def gate_batch(
    dataset: str,
    new_count: int,
    previous_count: int | None,
    *,
    min_rows: int,
    max_drop_ratio: float,
) -> GateResult:
    """Decide whether `new_count` rows may replace `previous_count` rows."""
    if new_count == 0:
        return _reject(dataset, "empty batch")

    if new_count < min_rows:
        return _reject(dataset, f"only {new_count} rows, below the minimum of {min_rows}")

    if previous_count:
        drop = (previous_count - new_count) / previous_count
        if drop > max_drop_ratio:
            return _reject(
                dataset,
                f"row count fell {drop:.0%} ({previous_count} -> {new_count}), "
                f"above the {max_drop_ratio:.0%} limit",
            )

    metrics.incr("batch_accepted_total", dataset=dataset)
    return GateResult(accepted=True)


def _reject(dataset: str, reason: str) -> GateResult:
    logger.error("batch_rejected", extra={"dataset": dataset, "reason": reason})
    metrics.incr("batch_rejected_total", dataset=dataset)
    return GateResult(accepted=False, reason=reason)


# ── per-record checks ─────────────────────────────────────────────────────────

def validate_quote(row: dict) -> str | None:
    """Return a rejection reason, or None. Only impossible data is rejected."""
    for field in ("open", "high", "low", "current", "ldcp"):
        value = row.get(field)
        if value is not None and value < 0:
            return f"negative {field}"

    volume = row.get("volume")
    if volume is not None and volume < 0:
        return "negative volume"

    low, high = row.get("low"), row.get("high")
    if low and high and low > high:
        return f"low {low} above high {high}"

    change_pct = row.get("change_pct")
    if change_pct is not None and abs(change_pct) > MAX_PLAUSIBLE_CHANGE_PCT:
        return f"change_pct {change_pct} implausible"

    if row.get("current") is None:
        return "missing current price"

    return None


def quote_anomalies(row: dict) -> list[str]:
    """Unusual-but-real observations, attached to the record rather than dropped."""
    flags: list[str] = []
    low, high, current = row.get("low"), row.get("high"), row.get("current")

    # Only meaningful once the instrument actually traded a range today.
    if low and high and current is not None and high > 0:
        if not (low - 0.01 <= current <= high + 0.01):
            flags.append("current_outside_day_range")

    change, ldcp = row.get("change"), row.get("ldcp")
    if None not in (change, current, ldcp) and ldcp:
        implied = current - ldcp
        if abs(implied - change) > max(0.05, abs(implied) * 0.02):
            flags.append("change_does_not_reconcile")

    if (row.get("volume") or 0) == 0 and row.get("traded"):
        flags.append("zero_volume")

    return flags


def validate_fund(row: dict) -> str | None:
    """Return a rejection reason, or None."""
    nav = row.get("nav")
    if nav is None:
        return "missing nav"
    if nav <= 0:
        return f"non-positive nav {nav}"
    if nav > 10_000_000:
        return f"nav {nav} implausible"
    if not row.get("fund_name"):
        return "missing fund_name"
    return None


def fund_anomalies(row: dict) -> list[str]:
    """For an open-end fund the offer price sits at or above NAV (it carries any
    front-end load) and the repurchase price at or below it. Deviations are
    worth surfacing but are not proof of a parse error."""
    flags: list[str] = []
    nav = row.get("nav") or 0
    if not nav:
        return flags

    offer = row.get("offer_price")
    if offer is not None and offer > 0 and offer < nav * 0.95:
        flags.append("offer_below_nav")

    repurchase = row.get("repurchase_price")
    if repurchase is not None and repurchase > 0 and repurchase > nav * 1.05:
        flags.append("repurchase_above_nav")

    if not row.get("validity_date"):
        flags.append("missing_validity_date")

    return flags


def apply_record_validation(dataset: str, rows: list[dict], validator,
                            annotator=None) -> list[dict]:
    """Drop impossible records; annotate merely unusual ones with `anomalies`."""
    kept: list[dict] = []
    dropped = 0
    flagged = 0

    for row in rows:
        reason = validator(row)
        if reason is not None:
            dropped += 1
            if dropped <= 5:  # log a sample, never the whole batch
                logger.warning(
                    "record_rejected",
                    extra={"dataset": dataset, "reason": reason,
                           "key": row.get("symbol") or row.get("fund_name")},
                )
            continue

        if annotator is not None:
            flags = annotator(row)
            if flags:
                row["anomalies"] = flags
                flagged += 1

        kept.append(row)

    if dropped:
        metrics.incr("records_rejected_total", dropped, dataset=dataset)
        logger.warning("records_rejected_summary",
                       extra={"dataset": dataset, "dropped": dropped, "kept": len(kept)})
    if flagged:
        metrics.incr("records_flagged_total", flagged, dataset=dataset)
        logger.info("records_flagged", extra={"dataset": dataset, "flagged": flagged})

    return kept
