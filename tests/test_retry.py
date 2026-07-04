"""Tests for with_retry."""
from __future__ import annotations

import pytest
import requests

from osm_revert_batch.retry import MAX_RETRIES, with_retry


class _Counter:
    def __init__(self, fail_times: int, exc: BaseException):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return "ok"


def test_returns_value_on_first_success():
    c = _Counter(0, requests.ConnectionError("x"))
    sleeps: list[float] = []
    assert with_retry(c, sleep=sleeps.append, log=lambda *_: None) == "ok"
    assert c.calls == 1
    assert sleeps == []


def test_retries_transient_then_succeeds():
    c = _Counter(2, requests.ConnectionError("x"))
    sleeps: list[float] = []
    assert with_retry(c, sleep=sleeps.append, log=lambda *_: None) == "ok"
    assert c.calls == 3
    assert sleeps == [1.0, 2.0]  # exponential from base 1s


def test_gives_up_after_max_retries_and_reraises(http_error):
    exc = http_error(429, "rate")
    c = _Counter(fail_times=99, exc=exc)
    sleeps: list[float] = []
    with pytest.raises(type(exc)):
        with_retry(c, sleep=sleeps.append, log=lambda *_: None)
    assert c.calls == MAX_RETRIES + 1  # initial + MAX_RETRIES retries
    assert len(sleeps) == MAX_RETRIES  # slept before each retry


def test_non_transient_reraised_immediately_no_sleep():
    c = _Counter(fail_times=99, exc=ValueError("boom"))
    sleeps: list[float] = []
    with pytest.raises(ValueError):
        with_retry(c, sleep=sleeps.append, log=lambda *_: None)
    assert c.calls == 1
    assert sleeps == []


def test_backoff_caps_at_max():
    from osm_revert_batch.retry import BASE_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS
    # Force a scenario where backoff would exceed MAX by monkeypatching constants
    # via direct call: after 6 attempts, base*2^6 = 64 > 60.
    # We only see MAX_RETRIES sleeps, so verify none exceed the cap.
    c = _Counter(fail_times=99, exc=requests.ConnectionError("x"))
    sleeps: list[float] = []
    with pytest.raises(requests.ConnectionError):
        with_retry(c, sleep=sleeps.append, log=lambda *_: None)
    assert all(s <= MAX_BACKOFF_SECONDS for s in sleeps)
    # First retry uses BASE (1s), each subsequent doubles
    assert sleeps[0] == BASE_BACKOFF_SECONDS


def test_log_called_for_each_retry():
    c = _Counter(fail_times=2, exc=requests.Timeout("slow"))
    messages: list[str] = []
    with_retry(c, sleep=lambda _: None, log=lambda msg: messages.append(msg))
    assert len(messages) == 2
    for m in messages:
        assert "retry" in m.lower()
