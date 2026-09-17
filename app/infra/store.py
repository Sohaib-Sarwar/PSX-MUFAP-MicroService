"""Snapshot store — the single source of truth a request ever reads.

A request handler never touches the network. It reads the current snapshot,
which is always either good data or explicitly marked unavailable.

Two backends, chosen by configuration:

  memory  a long-running process (Docker, Railway, local uvicorn). Holds the
          current snapshot plus the last-known-good one. That is the entire
          memory bound: two snapshots per dataset, nothing accumulates.

  redis   serverless (Vercel), where process memory does not survive between
          invocations. Backed by Upstash's REST API, so it needs no TCP
          connection pool and works inside a serverless function.

  file    batch jobs (GitHub Actions). One JSON file per dataset, written
          atomically. A workflow run reads the snapshot its predecessor
          committed and writes the one its successor will read, so the
          last-known-good contract survives across processes with no database.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import orjson

from .config import get_settings, now_pkt

logger = logging.getLogger(__name__)

FreshnessState = Literal["fresh", "stale", "degraded", "unavailable"]


@dataclass(slots=True)
class Snapshot:
    """An immutable, validated view of one dataset at one point in time."""

    dataset: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    # When we fetched it.
    fetched_at: str = ""
    # When the SOURCE says the data is from. For PSX this is the last trade
    # timestamp; for MUFAP the NAV validity date. This is the number that
    # actually matters, and it is not the same as fetched_at.
    data_as_of: str | None = None
    # Set when a refresh failed and we are serving the previous good snapshot.
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.rows)

    def to_json(self) -> bytes:
        return orjson.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: bytes | str) -> "Snapshot":
        return cls(**orjson.loads(raw))

    def age_seconds(self) -> float | None:
        if not self.fetched_at:
            return None
        try:
            return (now_pkt() - datetime.fromisoformat(self.fetched_at)).total_seconds()
        except ValueError:
            return None

    def freshness(self, stale_after_s: int, market_status: str | None = None) -> dict[str, Any]:
        """The envelope attached to every response that serves this snapshot.

        A consumer can always distinguish fresh / stale / degraded / unavailable
        without guessing from a timestamp.
        """
        age = self.age_seconds()
        if not self.rows:
            state: FreshnessState = "unavailable"
        elif self.error:
            state = "degraded"
        elif age is not None and age > stale_after_s:
            state = "stale"
        else:
            state = "fresh"

        env: dict[str, Any] = {
            "state": state,
            "data_as_of": self.data_as_of,
            "fetched_at": self.fetched_at or None,
            "age_seconds": round(age, 1) if age is not None else None,
            "record_count": self.count,
        }
        if market_status is not None:
            env["market_status"] = market_status
        if self.error:
            env["error"] = self.error
        return env


class SnapshotStore:
    """Backend-agnostic interface."""

    async def get(self, dataset: str) -> Snapshot | None:  # pragma: no cover - interface
        raise NotImplementedError

    async def put(self, snapshot: Snapshot) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:
        return None


class MemoryStore(SnapshotStore):
    """Holds exactly two snapshots per dataset: current and last-known-good."""

    def __init__(self) -> None:
        self._current: dict[str, Snapshot] = {}
        self._last_good: dict[str, Snapshot] = {}
        self._lock = asyncio.Lock()

    async def get(self, dataset: str) -> Snapshot | None:
        return self._current.get(dataset)

    async def get_last_good(self, dataset: str) -> Snapshot | None:
        return self._last_good.get(dataset)

    async def put(self, snapshot: Snapshot) -> None:
        async with self._lock:
            if snapshot.rows and not snapshot.error:
                self._last_good[snapshot.dataset] = snapshot
            self._current[snapshot.dataset] = snapshot

    async def datasets(self) -> list[str]:
        return sorted(self._current)


class FileStore(SnapshotStore):
    """One JSON file per dataset, written atomically.

    Used by the scheduled scrapers, where each run is a separate process. The
    write goes to a temporary file and is then renamed over the target, so a
    run killed mid-write (a cancelled workflow, a runner timeout) can never
    leave a half-written snapshot behind for the next run to read.

    Nothing is cached beyond the snapshot currently in play: `put` replaces the
    entry `get` loaded, so the previous version is released as soon as the new
    one exists rather than both being held for the life of the process.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._cache: dict[str, Snapshot] = {}

    def _path(self, dataset: str) -> Path:
        # Dataset names are internal literals ("psx.stocks"), never user input.
        return self._dir / f"{dataset}.json"

    async def get(self, dataset: str) -> Snapshot | None:
        cached = self._cache.get(dataset)
        if cached is not None:
            return cached

        path = self._path(dataset)
        if not path.is_file():
            return None
        try:
            snapshot = Snapshot.from_json(path.read_bytes())
        except Exception as exc:
            # A corrupt file must not stop the run. Treating it as "no previous
            # snapshot" means this run republishes from scratch, which is the
            # recoverable outcome; raising would leave the site frozen forever.
            logger.error("file_read_failed", extra={"dataset": dataset,
                                                    "path": str(path),
                                                    "error": str(exc)})
            return None
        self._cache[dataset] = snapshot
        return snapshot

    async def get_last_good(self, dataset: str) -> Snapshot | None:
        return await self.get(dataset)

    async def put(self, snapshot: Snapshot) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(snapshot.dataset)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(snapshot.to_json())
        tmp.replace(path)  # atomic on POSIX and on Windows (same directory)
        self._cache[snapshot.dataset] = snapshot

    async def datasets(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))


class RedisStore(SnapshotStore):
    """Upstash Redis over its REST API — works inside a serverless function.

    Uses the REST transport rather than the Redis wire protocol deliberately:
    a serverless invocation cannot keep a TCP connection pool alive between
    requests, so the REST API is both simpler and faster here.
    """

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0),
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return self._client

    @staticmethod
    def _key(dataset: str) -> str:
        return f"snapshot:{dataset}"

    async def get(self, dataset: str) -> Snapshot | None:
        try:
            resp = await self._http().get(f"{self._url}/get/{self._key(dataset)}")
            resp.raise_for_status()
            payload = resp.json().get("result")
            if not payload:
                return None
            return Snapshot.from_json(payload)
        except Exception as exc:  # a cache miss must never take the request down
            logger.error("redis_get_failed", extra={"dataset": dataset, "error": str(exc)})
            return None

    async def get_last_good(self, dataset: str) -> Snapshot | None:
        return await self.get(dataset)

    async def put(self, snapshot: Snapshot) -> None:
        try:
            # POST body form so the payload is not URL-length limited.
            resp = await self._http().post(
                f"{self._url}/set/{self._key(snapshot.dataset)}",
                content=snapshot.to_json(),
                headers={"Content-Type": "application/octet-stream"},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("redis_put_failed", extra={"dataset": snapshot.dataset,
                                                    "error": str(exc)})

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


_store: SnapshotStore | None = None


def get_store() -> SnapshotStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.snapshot_store == "file":
            logger.info("snapshot_store_selected",
                        extra={"backend": "file", "dir": s.snapshot_dir})
            _store = FileStore(s.snapshot_dir)
        elif s.snapshot_store == "redis":
            if not (s.redis_url and s.redis_token):
                raise RuntimeError(
                    "SNAPSHOT_STORE=redis requires UPSTASH_REDIS_REST_URL and "
                    "UPSTASH_REDIS_REST_TOKEN"
                )
            logger.info("snapshot_store_selected", extra={"backend": "redis"})
            _store = RedisStore(s.redis_url, s.redis_token)
        else:
            logger.info("snapshot_store_selected", extra={"backend": "memory"})
            _store = MemoryStore()
    return _store


async def reset_store() -> None:
    """Test hook — drop the process-wide store."""
    global _store
    if _store is not None:
        await _store.close()
    _store = None
