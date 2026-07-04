"""Tests for upload_changes_with_auth."""
from __future__ import annotations

import pytest

from simple_revert.common import HTTPError

from osm_revert_batch.upload import upload_changes_with_auth


class _FakeAuth:
    """Records auth_request calls and drives responses."""

    def __init__(self, *, create_resp="42", upload_exc=None, close_exc=None):
        self.create_resp = create_resp
        self.upload_exc = upload_exc
        self.close_exc = close_exc
        self.calls: list[tuple[str, str]] = []  # (endpoint, method)


@pytest.fixture
def stub_auth_request(monkeypatch):
    """Patch simple_revert.common.auth_request to drive from _FakeAuth."""

    def _install(fake: _FakeAuth):
        def fake_auth_request(auth, endpoint, method="GET", **kwargs):
            fake.calls.append((endpoint, method))
            if endpoint == "changeset/create":
                return fake.create_resp
            if endpoint.endswith("/upload"):
                if fake.upload_exc:
                    raise fake.upload_exc
                return None
            if endpoint.endswith("/close"):
                if fake.close_exc:
                    raise fake.close_exc
                return None
            raise AssertionError(f"unexpected endpoint {endpoint}")

        monkeypatch.setattr(
            "osm_revert_batch.upload.auth_request", fake_auth_request
        )
        return fake

    return _install


@pytest.fixture
def sample_change():
    return {
        "type": "node",
        "id": "1",
        "version": 5,
        "deleted": False,
        "coords": ("139.7", "35.6"),
        "tags": {"amenity": "cafe"},
    }


def test_success_creates_and_closes(stub_auth_request, sample_change):
    fake = stub_auth_request(_FakeAuth())
    upload_changes_with_auth(auth=None, changes=[sample_change], changeset_tags={"comment": "x"})
    endpoints = [ep for ep, _ in fake.calls]
    assert endpoints == ["changeset/create", "changeset/42/upload", "changeset/42/close"]


def test_upload_412_reraises(stub_auth_request, sample_change):
    exc = HTTPError(412, "Node 1 is still used by ways 5,6")
    fake = stub_auth_request(_FakeAuth(upload_exc=exc))
    with pytest.raises(HTTPError) as caught:
        upload_changes_with_auth(auth=None, changes=[sample_change], changeset_tags={"comment": "x"})
    assert caught.value.code == 412
    # close should still be attempted (best-effort)
    endpoints = [ep for ep, _ in fake.calls]
    assert "changeset/42/close" in endpoints


def test_upload_500_reraises(stub_auth_request, sample_change):
    exc = HTTPError(500, "server error")
    fake = stub_auth_request(_FakeAuth(upload_exc=exc))
    with pytest.raises(HTTPError) as caught:
        upload_changes_with_auth(auth=None, changes=[sample_change], changeset_tags={"comment": "x"})
    assert caught.value.code == 500


def test_close_failure_does_not_raise(stub_auth_request, sample_change):
    fake = stub_auth_request(_FakeAuth(close_exc=HTTPError(500, "boom")))
    # Upload succeeded; close is best-effort. Must not raise.
    upload_changes_with_auth(auth=None, changes=[sample_change], changeset_tags={"comment": "x"})
    assert any(ep.endswith("/close") for ep, _ in fake.calls)


def test_empty_changes_short_circuit(stub_auth_request):
    fake = stub_auth_request(_FakeAuth())
    upload_changes_with_auth(auth=None, changes=[], changeset_tags={"comment": "x"})
    # No auth_request calls at all
    assert fake.calls == []
