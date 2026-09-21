"""Small helper for running independent provider calls concurrently.

How many run at once comes from the provider config (`concurrency:`), because
the limit belongs to the provider: Cloudflare is happy with several at a time,
the keyless Pollinations endpoint answers one request per IP, and a local GPU
pipeline must stay at one.
"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Sequence, Tuple

from shared.utils.logging import get_logger

log = get_logger("parallel")

Job = Tuple[Callable[..., Any], Sequence[Any]]


def run_jobs(jobs: List[Job], workers: int, label: str = "") -> List[Any]:
    """Run (fn, args) jobs with at most `workers` at a time, keeping their order."""
    if workers <= 1 or len(jobs) <= 1:
        return [fn(*args) for fn, args in jobs]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, *args) for fn, args in jobs]
        results = [f.result() for f in futures]
    log.info("%s: %d job(s) on %d workers in %.1fs", label or "batch", len(jobs), workers,
             time.monotonic() - started)
    return results
