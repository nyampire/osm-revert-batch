"""Retry wrapper for transient HTTP/network failures."""
from __future__ import annotations

import sys
import time
from typing import Callable, TypeVar

from .errors import classify_error, is_transient


MAX_RETRIES: int = 3
BASE_BACKOFF_SECONDS: float = 1.0
MAX_BACKOFF_SECONDS: float = 60.0

T = TypeVar("T")


def _default_log(msg: str) -> None:
    print(msg, file=sys.stderr)


def with_retry(
    fn: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _default_log,
) -> T:
    """Call fn(), retrying on transient exceptions with exponential backoff.

    - Non-transient exceptions are re-raised on the first hit.
    - Transient exceptions are retried up to MAX_RETRIES times.
    - Backoff: BASE * 2**attempt, clamped to MAX_BACKOFF_SECONDS.
    - `sleep` and `log` are injectable for testing.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as e:
            if not is_transient(e) or attempt >= MAX_RETRIES:
                raise
            wait = min(BASE_BACKOFF_SECONDS * (2 ** attempt), MAX_BACKOFF_SECONDS)
            log(f"transient error: {classify_error(e)}, retry in {wait:.1f}s")
            sleep(wait)
            attempt += 1
