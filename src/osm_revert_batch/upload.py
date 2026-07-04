"""Fork of simple_revert.common.upload_changes with:

- external `auth` parameter (no read_auth())
- HTTP errors on upload are re-raised (upstream had `pass` blocks that
  silently swallowed 412 Precondition Failed)
- changeset close is best-effort (matches upstream)
"""
from __future__ import annotations

from simple_revert.common import (
    HTTPError,
    auth_request,
    changes_to_osc,
    changeset_xml,
)


def upload_changes_with_auth(
    auth,
    changes: list[dict],
    changeset_tags: dict[str, str],
) -> None:
    """Upload a list of change dicts as a single changeset using the given auth.

    Raises HTTPError on any upload-time server failure. Changeset close
    failures are silently ignored (upstream behavior — server auto-closes
    after an hour).
    """
    if not changes:
        return

    # 1. Create changeset
    changeset_id = int(
        auth_request(
            auth,
            "changeset/create",
            "PUT",
            raw_result=True,
            data=changeset_xml(changeset_tags),
        )
    )

    # 2. Upload contents
    osc = changes_to_osc(changes, changeset_id)
    upload_exc: BaseException | None = None
    try:
        auth_request(
            auth,
            f"changeset/{changeset_id}/upload",
            "POST",
            data=osc,
        )
    except HTTPError as e:
        upload_exc = e
    except BaseException as e:
        upload_exc = e

    # 3. Best-effort close (swallow errors)
    try:
        auth_request(auth, f"changeset/{changeset_id}/close", "PUT")
    except BaseException:
        pass

    if upload_exc is not None:
        raise upload_exc
