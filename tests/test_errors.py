"""Tests for is_transient and classify_error."""
from __future__ import annotations

import requests

from osm_revert_batch.errors import classify_error, is_transient


def test_http_429_is_transient(http_error):
    assert is_transient(http_error(429, "Too Many")) is True


def test_http_503_is_transient(http_error):
    assert is_transient(http_error(503, "unavailable")) is True


def test_http_403_is_not_transient(http_error):
    assert is_transient(http_error(403, "Forbidden")) is False


def test_http_412_is_not_transient(http_error):
    assert is_transient(http_error(412, "precondition")) is False


def test_revert_error_wrapping_429_is_transient(revert_error):
    e = revert_error("Failed to download changeset 1: HTTPError(429, 'x')")
    assert is_transient(e) is True


def test_revert_error_wrapping_500_is_transient(revert_error):
    e = revert_error("Failed to download changeset 1: HTTPError(500, 'x')")
    assert is_transient(e) is True


def test_revert_error_plain_is_not_transient(revert_error):
    assert is_transient(revert_error("Cannot revert redactions")) is False


def test_revert_error_wrapping_403_is_not_transient(revert_error):
    e = revert_error("Failed to download changeset 1: HTTPError(403, 'x')")
    assert is_transient(e) is False


def test_connection_error_is_transient():
    assert is_transient(requests.ConnectionError("boom")) is True


def test_timeout_is_transient():
    assert is_transient(requests.Timeout("slow")) is True


def test_unrelated_exception_is_not_transient():
    assert is_transient(ValueError("no")) is False


def test_classify_http(http_error):
    assert classify_error(http_error(412, "Node 1 in use")) == "HTTP 412: Node 1 in use"


def test_classify_http_truncates_long_body(http_error):
    long_body = "x" * 500
    got = classify_error(http_error(500, long_body))
    assert got.startswith("HTTP 500:")
    assert len(got) < 250  # 200 char body + prefix


def test_classify_revert(revert_error):
    assert classify_error(revert_error("Cannot revert redactions")) == "Cannot revert redactions"


def test_classify_network():
    got = classify_error(requests.ConnectionError("dns fail"))
    assert got.startswith("Network:")
    assert "dns fail" in got


def test_classify_unknown():
    got = classify_error(RuntimeError("weird"))
    assert got.startswith("Unexpected: RuntimeError:")
    assert "weird" in got
