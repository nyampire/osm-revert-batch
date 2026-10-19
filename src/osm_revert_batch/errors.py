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


def _one_line(text: str) -> str:
    return " ".join(text.split())


def classify_error(exc: BaseException) -> str:
    """Return a short single-line label for logging and failed.txt."""
    if isinstance(exc, HTTPError):
        # HTML のエラーページなど複数行の本文を 1 行にしてから切り詰める
        return f"HTTP {exc.code}: {_one_line(exc.message)[:200]}"
    if isinstance(exc, RevertError):
        # simple_revert は先頭に改行を付けてメッセージを作る
        return _one_line(exc.message)
    if isinstance(exc, requests.RequestException):
        return f"Network: {_one_line(str(exc))}"
    return f"Unexpected: {type(exc).__name__}: {_one_line(str(exc))}"
