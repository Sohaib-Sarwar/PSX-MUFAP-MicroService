"""Resource ceilings and accounting for the batch jobs.

A scheduled scrape runs on a shared runner with a fixed budget. Two things
matter there that do not matter in a long-running server:

  1. A runaway allocation should fail loudly and immediately, not grow until
     the kernel OOM-kills the process. An OOM kill surfaces as exit code 137
     with no traceback and no indication of which step was responsible;
     `RLIMIT_AS` turns the same event into a MemoryError at the exact
     allocation that crossed the line.

  2. The job should report what it actually used, so the ceiling can be set
     from measurement rather than guesswork.

Both are POSIX facilities. On Windows every function here is a no-op that
returns None, so the same code path runs unchanged during local development.
"""

from __future__ import annotations

import gc
import logging
import sys

logger = logging.getLogger(__name__)

try:  # POSIX only — absent on Windows, which is a supported dev platform.
    import resource
except ImportError:  # pragma: no cover - platform dependent
    resource = None  # type: ignore[assignment]


def apply_memory_cap(megabytes: int) -> int | None:
    """Cap this process's address space. Returns the cap applied, or None.

    A value of 0 or less disables the cap. The hard limit is left untouched
    where one already exists, so this can only tighten the ceiling, never
    raise it above what the environment allows.
    """
    if megabytes <= 0 or resource is None:
        return None

    limit = megabytes * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ValueError, OSError) as exc:
        # Some sandboxes refuse RLIMIT_AS outright. That is not a reason to
        # abandon the run — the cap is a guard rail, not the job.
        logger.warning("memory_cap_unavailable", extra={"error": str(exc)})
        return None

    logger.info("memory_cap_applied", extra={"megabytes": megabytes})
    return megabytes


def peak_rss_mb() -> float | None:
    """Peak resident set size for this process, in MiB."""
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes; macOS reports bytes.
    divisor = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return round(usage / divisor, 1)


def release() -> None:
    """Hand freed memory back between pipeline stages.

    Called after a large intermediate — a megabyte of upstream HTML, a parsed
    document tree — goes out of scope. The allocator would get there on its
    own eventually; doing it at the stage boundary keeps the peak, which is
    what the ceiling is measured against, close to the true working set.
    """
    gc.collect()
