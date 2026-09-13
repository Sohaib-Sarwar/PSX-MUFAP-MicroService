"""One response envelope for every list endpoint, in both domains.

The previous API returned `total` from PSX and `total_available` from MUFAP for
the same concept, and documented only the latter. Every list response now has
the same shape, and every response carries freshness so a consumer never has to
infer staleness from a timestamp.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from .errors import BadRequest, DataUnavailable
from .store import Snapshot


def envelope(
    rows: Sequence[dict[str, Any]],
    *,
    snapshot: Snapshot,
    stale_after_s: int,
    total: int | None = None,
    offset: int = 0,
    limit: int | None = None,
    market_status: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "count": len(rows),
        "total_filtered": total if total is not None else len(rows),
        "total": snapshot.count,
        "offset": offset,
        "limit": limit,
        "freshness": snapshot.freshness(stale_after_s, market_status),
        "data": list(rows),
    }
    if extra:
        payload.update(extra)
    return payload


def single(
    record: dict[str, Any],
    *,
    snapshot: Snapshot,
    stale_after_s: int,
    market_status: str | None = None,
) -> dict[str, Any]:
    return {
        "freshness": snapshot.freshness(stale_after_s, market_status),
        "data": record,
    }


def require_rows(snapshot: Snapshot, what: str) -> list[dict[str, Any]]:
    """503 rather than 404 when the cache is still warming.

    404 means "this resource does not exist". A warming cache is a temporary
    condition of this server, which is what 503 is for — and it keeps uptime
    monitors from recording a warm-up as a client error.
    """
    if not snapshot.rows:
        reason = snapshot.error or "No data yet — the service is still loading."
        raise DataUnavailable(f"{what} is not available. {reason}")
    return snapshot.rows


def sort_rows(
    rows: list[dict[str, Any]],
    sort_by: str | None,
    ascending: bool,
    allowed: Iterable[str],
) -> list[dict[str, Any]]:
    """Sort by a whitelisted field.

    An unknown field is a 400, not a silent no-op. The previous implementation
    ignored an unrecognised `sort_by` and returned unsorted data with HTTP 200,
    so a typo produced a wrong-looking page with no indication anything was off.
    """
    if not sort_by:
        return rows
    allowed = set(allowed)
    if sort_by not in allowed:
        raise BadRequest(
            f"Cannot sort by '{sort_by}'. Sortable fields: {', '.join(sorted(allowed))}."
        )

    # None sorts last in both directions: a missing price is not a low price.
    def key(row: dict[str, Any]):
        value = row.get(sort_by)
        if value is None:
            return (1, 0)
        if isinstance(value, str):
            return (0, value.lower())
        return (0, value)

    missing = [r for r in rows if r.get(sort_by) is None]
    present = [r for r in rows if r.get(sort_by) is not None]
    present.sort(key=key, reverse=not ascending)
    return present + missing


def paginate(rows: list[dict[str, Any]], offset: int, limit: int) -> list[dict[str, Any]]:
    return rows[offset: offset + limit]


def contains(value: Any, needle: str) -> bool:
    return needle.lower() in str(value or "").lower()


def apply_filters(
    rows: list[dict[str, Any]],
    filters: list[Callable[[dict[str, Any]], bool]],
) -> list[dict[str, Any]]:
    if not filters:
        return rows
    return [r for r in rows if all(f(r) for f in filters)]


def round_money(value: float | None, places: int = 2) -> float | None:
    """Round at the serialisation boundary, to the precision the source publishes.

    Values are carried through the pipeline as exact float64 and rounded only
    here. The previous implementation downcast prices to float32 to save memory,
    which made 12.93 serialise as 12.930000305175781 and grew the payload 30%.
    """
    return None if value is None else round(value, places)
