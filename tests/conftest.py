"""pytest fixtures shared across test modules."""
from __future__ import annotations

import pytest

from simple_revert.common import HTTPError, RevertError


@pytest.fixture
def http_error():
    """Factory for HTTPError instances with a chosen code and message."""
    def _make(code: int, message: str = "") -> HTTPError:
        return HTTPError(code, message)
    return _make


@pytest.fixture
def revert_error():
    """Factory for RevertError instances."""
    def _make(message: str) -> RevertError:
        return RevertError(message)
    return _make
