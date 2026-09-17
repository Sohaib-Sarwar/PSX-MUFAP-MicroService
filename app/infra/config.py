"""Configuration — every tunable is an environment variable with a safe default.

Nothing here reads a secret from source. Secrets arrive via the environment only.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Literal

# Pakistan Standard Time. PKT is UTC+5 year-round — Pakistan abolished DST in 2009,
# so a fixed offset is correct (verified: no DST transitions since 2009).
PKT = timezone(timedelta(hours=5), name="PKT")


def now_pkt() -> datetime:
    """Current time in Pakistan Standard Time."""
    return datetime.now(PKT)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    """Process configuration. Instantiated once via `get_settings()`."""

    def __init__(self) -> None:
        # ── Service identity ────────────────────────────────────────────
        self.service_name: str = os.getenv("SERVICE_NAME", "pk-finance")
        self.environment: str = os.getenv("ENVIRONMENT", "development")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
        self.log_format: Literal["json", "text"] = (
            "json" if os.getenv("LOG_FORMAT", "json").lower() == "json" else "text"
        )

        # ── Snapshot store ──────────────────────────────────────────────
        # "memory" for a long-running process (Docker, Railway, local uvicorn).
        # "redis"  for serverless (Vercel), where process memory does not survive.
        # "file"   for the GitHub Actions scrapers: a workflow run is a fresh
        #          process on a fresh machine, so the previous snapshot arrives
        #          as a checked-out file and the new one leaves as a committed
        #          one. Same last-known-good contract, no database to pay for.
        # Auto-detect: if an Upstash URL is present, prefer redis.
        explicit = os.getenv("SNAPSHOT_STORE", "").strip().lower()
        self.redis_url: str = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
        self.redis_token: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if explicit in {"memory", "redis", "file"}:
            self.snapshot_store = explicit
        else:
            self.snapshot_store = "redis" if self.redis_url and self.redis_token else "memory"
        # Where SNAPSHOT_STORE=file keeps its JSON. One file per dataset.
        self.snapshot_dir: str = os.getenv("SNAPSHOT_DIR", "data").strip() or "data"

        # ── Resource ceiling ────────────────────────────────────────────
        # A hard address-space cap for the batch jobs. A runaway parse that
        # would otherwise swell until the runner OOM-kills it (and reports a
        # mystery exit 137) fails immediately with a MemoryError naming the
        # limit instead. 0 disables the cap. POSIX only; a no-op on Windows.
        self.max_memory_mb: int = _env_int("MAX_MEMORY_MB", 0)

        # ── Scheduler ───────────────────────────────────────────────────
        # Serverless has no background loop; Vercel Cron calls /internal/refresh.
        self.scheduler_enabled: bool = _env_bool("SCHEDULER_ENABLED", self.snapshot_store == "memory")
        # Adaptive cadence. PSX ticks only while the market is open.
        self.psx_interval_open_s: int = _env_int("PSX_INTERVAL_OPEN_SECONDS", 90)
        self.psx_interval_closed_s: int = _env_int("PSX_INTERVAL_CLOSED_SECONDS", 1800)
        # MUFAP publishes NAV once per business day; polling faster cannot make it fresher.
        self.mufap_interval_s: int = _env_int("MUFAP_INTERVAL_SECONDS", 3600)

        # ── Upstream HTTP ───────────────────────────────────────────────
        self.http_timeout_s: float = _env_float("HTTP_TIMEOUT_SECONDS", 20.0)
        self.http_connect_timeout_s: float = _env_float("HTTP_CONNECT_TIMEOUT_SECONDS", 5.0)
        self.http_max_retries: int = _env_int("HTTP_MAX_RETRIES", 2)
        self.http_backoff_s: float = _env_float("HTTP_BACKOFF_SECONDS", 0.5)
        self.http_max_connections: int = _env_int("HTTP_MAX_CONNECTIONS", 16)
        self.http_user_agent: str = os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # ── Data validation ─────────────────────────────────────────────
        # A scrape returning fewer rows than this, or a steep drop versus the
        # previous good snapshot, is rejected rather than published. This is the
        # guard that stops a parser regression from replacing 500 stocks with 3.
        self.psx_min_rows: int = _env_int("PSX_MIN_ROWS", 50)
        self.mufap_min_rows: int = _env_int("MUFAP_MIN_ROWS", 100)
        self.max_row_drop_ratio: float = _env_float("MAX_ROW_DROP_RATIO", 0.5)

        # ── Freshness ───────────────────────────────────────────────────
        # A snapshot older than this is reported as "stale" in the envelope.
        self.psx_stale_after_s: int = _env_int("PSX_STALE_AFTER_SECONDS", 600)
        self.mufap_stale_after_s: int = _env_int("MUFAP_STALE_AFTER_SECONDS", 86_400)
        # Market is considered open if the index feed ticked within this window.
        self.market_open_tick_window_s: int = _env_int("MARKET_OPEN_TICK_WINDOW_SECONDS", 600)

        # ── API surface ─────────────────────────────────────────────────
        self.cors_origins: list[str] = _env_list("CORS_ORIGINS", ["*"])
        self.cors_allow_credentials: bool = _env_bool("CORS_ALLOW_CREDENTIALS", False)
        self.max_page_size: int = _env_int("MAX_PAGE_SIZE", 2000)
        self.default_page_size: int = _env_int("DEFAULT_PAGE_SIZE", 100)
        # Shared secret for /internal/* (manual refresh, Vercel Cron). When unset,
        # those routes are disabled entirely rather than left open.
        self.internal_token: str = os.getenv("INTERNAL_TOKEN", "").strip()
        self.rate_limit_per_minute: int = _env_int("RATE_LIMIT_PER_MINUTE", 120)
        self.serve_static: bool = _env_bool("SERVE_STATIC", True)

        # ── MUFAP client ────────────────────────────────────────────────
        # MUFAP is behind Cloudflare bot management that rejects the request on
        # its TLS fingerprint. "curl_cffi" negotiates a standard browser TLS
        # profile, which is what makes the (robots.txt-permitted) request work.
        # "httpx" forces the plain client. PSX never uses this path.
        self.mufap_http_client: str = os.getenv("MUFAP_HTTP_CLIENT", "curl_cffi").strip().lower()
        self.mufap_impersonate: str = os.getenv("MUFAP_IMPERSONATE", "chrome").strip()
        # Backoff between impersonation profiles, in seconds. Deliberately an
        # order of magnitude larger than the generic HTTP backoff: a Cloudflare
        # challenge is the one failure that retrying quickly makes worse.
        self.mufap_retry_backoff_s: float = _env_float("MUFAP_RETRY_BACKOFF_SECONDS", 12.0)

        # ── Upstream endpoints ──────────────────────────────────────────
        self.psx_base: str = os.getenv("PSX_BASE_URL", "https://dps.psx.com.pk").rstrip("/")
        self.mufap_base: str = os.getenv("MUFAP_BASE_URL", "https://www.mufap.com.pk").rstrip("/")

    # -- derived upstream URLs ------------------------------------------------
    @property
    def psx_market_watch_url(self) -> str:
        return f"{self.psx_base}/market-watch"

    @property
    def psx_symbols_url(self) -> str:
        """JSON list of every listed instrument (~1020), with name/sector/flags."""
        return f"{self.psx_base}/symbols"

    @property
    def psx_indices_url(self) -> str:
        """Server-rendered index table. The homepage renders indices client-side."""
        return f"{self.psx_base}/indices"

    def psx_timeseries_url(self, kind: str, symbol: str) -> str:
        return f"{self.psx_base}/timeseries/{kind}/{symbol}"

    @property
    def mufap_returns_url(self) -> str:
        """tab=1 — NAV, rating, benchmark and 12 return periods."""
        return f"{self.mufap_base}/Industry/IndustryStatDaily?tab=1"

    @property
    def mufap_prices_url(self) -> str:
        """tab=3 — AMC, inception, offer/repurchase, sales loads, trustee."""
        return f"{self.mufap_base}/Industry/IndustryStatDaily?tab=3"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
