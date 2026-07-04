"""Main batch loop: reads input, drives revert per changeset, records outcomes."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests
from simple_revert.common import HTTPError, RevertError, changes_to_osc
from simple_revert.simple_revert import (
    download_changesets as _real_download_changesets,
    revert_changes as _real_revert_changes,
    print_status as _real_print_status,
)

from .errors import classify_error, is_transient
from .io import (
    append_done,
    parse_input,
    read_done_ids,
    write_failed_txt,
    write_remaining_txt,
)
from .retry import with_retry
from .upload import upload_changes_with_auth as _real_upload_changes_with_auth


CIRCUIT_BREAKER_THRESHOLD: int = 5


@dataclass
class RunOptions:
    input_file: Path
    comment: str | None
    dry_run: bool
    out_dir: Path
    retry_done: bool


@dataclass
class RunResult:
    total: int = 0
    skipped: int = 0
    success: int = 0
    failures: list[tuple[int, str]] = field(default_factory=list)
    remaining: list[int] = field(default_factory=list)
    aborted: bool = False
    aborted_at: tuple[int, int] | None = None  # (line_no, cs_id)
    abort_reason: str | None = None


def build_tags(cs_id: int, user: str, user_comment: str | None) -> dict[str, str]:
    return {
        "created_by": "simple_revert.py",
        "comment": user_comment or f"Reverting {cs_id} by {user}",
    }


def _default_log(msg: str) -> None:
    print(msg, file=sys.stderr)


def run(
    options: RunOptions,
    *,
    auth=None,
    downloader: Callable = _real_download_changesets,
    reverter: Callable = _real_revert_changes,
    uploader: Callable = _real_upload_changes_with_auth,
    print_status: Callable = _real_print_status,
    log: Callable[[str], None] = _default_log,
) -> RunResult:
    """Drive the revert loop and produce a RunResult.

    All external calls (download/revert/upload) are injectable so tests can
    run without the network. `auth` is required for non-dry-run mode.
    """
    result = RunResult()
    raw_items = parse_input(options.input_file)
    result.total = len(raw_items)

    done_path = options.input_file.with_suffix(options.input_file.suffix + ".done.txt")
    if options.retry_done:
        items = raw_items
    else:
        done = read_done_ids(done_path)
        items = [it for it in raw_items if it[1] not in done]
        result.skipped = len(raw_items) - len(items)
        if result.skipped:
            log(f"skipping {result.skipped} already-done changesets (from {done_path.name})")

    if options.dry_run:
        options.out_dir.mkdir(parents=True, exist_ok=True)

    consecutive_transient = 0
    aborted_index: int | None = None
    abort_reason: str | None = None

    for i, (line_no, cs_id) in enumerate(items):
        try:
            diffs, ch_users = with_retry(
                lambda: downloader([cs_id], print_status),
                log=log,
            )
            if not diffs:
                result.success += 1
                consecutive_transient = 0
                continue

            changes = reverter(diffs, print_status)
            if not changes:
                result.success += 1
                consecutive_transient = 0
                continue

            if options.dry_run:
                osc_path = options.out_dir / f"{cs_id}.osc"
                osc_path.write_bytes(changes_to_osc(changes))
                result.success += 1
                # Do NOT append to done.txt in dry-run.
            else:
                tags = build_tags(cs_id, ch_users.get(cs_id, "?"), options.comment)
                with_retry(
                    lambda: uploader(auth, changes, tags),
                    log=log,
                )
                result.success += 1
                append_done(done_path, cs_id)
            consecutive_transient = 0

        except KeyboardInterrupt:
            aborted_index = i
            abort_reason = "keyboard interrupt (Ctrl+C)"
            break
        except BaseException as e:
            result.failures.append((cs_id, classify_error(e)))
            if is_transient(e):
                consecutive_transient += 1
            else:
                consecutive_transient = 0

        if consecutive_transient >= CIRCUIT_BREAKER_THRESHOLD:
            aborted_index = i + 1  # everything AFTER this index is remaining
            abort_reason = (
                f"{consecutive_transient} consecutive transient errors "
                f"(circuit breaker threshold={CIRCUIT_BREAKER_THRESHOLD})"
            )
            break

    if aborted_index is not None:
        result.aborted = True
        result.abort_reason = abort_reason
        if aborted_index < len(items):
            aborted_line, aborted_cs = items[aborted_index]
            result.aborted_at = (aborted_line, aborted_cs)
            result.remaining = [cs for _, cs in items[aborted_index:]]

    # Write sidecar files
    if result.failures:
        failed_path = options.input_file.with_suffix(options.input_file.suffix + ".failed.txt")
        write_failed_txt(
            failed_path,
            source_name=options.input_file.name,
            total_count=result.total,
            success_count=result.success,
            failures=result.failures,
        )
    if result.aborted and result.remaining:
        remaining_path = options.input_file.with_suffix(options.input_file.suffix + ".remaining.txt")
        write_remaining_txt(
            remaining_path,
            source_name=options.input_file.name,
            aborted_at_line=result.aborted_at[0] if result.aborted_at else 0,
            aborted_at_cs_id=result.aborted_at[1] if result.aborted_at else 0,
            reason=result.abort_reason or "unknown",
            remaining=result.remaining,
        )

    return result
