"""Exception categorization for retry and reporting."""
from __future__ import annotations

import re

import requests
from simple_revert.common import HTTPError, RevertError


_WRAPPED_HTTP_CODE_RE = re.compile(r"HTTPError\((\d+),")


def _http_code_is_transient(code: int) -> bool:
    return code == 429 or 500 <= code < 600


def is_transient(exc: BaseException) -> bool:
    """True iff the error is worth retrying (transient network/server issue)."""
    if isinstance(exc, HTTPError):
        return _http_code_is_transient(exc.code)
    if isinstance(exc, RevertError):
        # download_changesets wraps HTTPError into RevertError; detect it.
        m = _WRAPPED_HTTP_CODE_RE.search(exc.message)
        if m:
            return _http_code_is_transient(int(m.group(1)))
        return False
    if isinstance(exc, requests.RequestException):
        return True
    return False


def classify_error(exc: BaseException) -> str:
    """Return a short human-readable label for logging and failed.txt."""
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}: {exc.message[:200]}"
    if isinstance(exc, RevertError):
        return exc.message
    if isinstance(exc, requests.RequestException):
        return f"Network: {exc}"
    return f"Unexpected: {type(exc).__name__}: {exc}"
