"""Tests for BatchRunner (batch.run)."""
from __future__ import annotations

from pathlib import Path

from simple_revert.common import HTTPError, RevertError

from osm_revert_batch.batch import (
    CIRCUIT_BREAKER_THRESHOLD,
    RunOptions,
    build_tags,
    run,
)


def _write_input(tmp_path: Path, ids: list[int]) -> Path:
    p = tmp_path / "changesets.txt"
    p.write_text("\n".join(str(i) for i in ids) + "\n", encoding="utf-8")
    return p


def _make_deps(behaviors: dict[int, str], upload_exc: dict[int, BaseException] | None = None):
    """Build (downloader, reverter, uploader) triples for the run() DI slots.

    behaviors[cs_id] is one of: 'ok', 'no_diffs', 'no_changes', 'download_error:<msg>',
    'revert_error:<msg>'.
    """
    upload_exc = upload_exc or {}

    def downloader(ids, print_status):
        cs = ids[0]
        b = behaviors[cs]
        if b == "no_diffs":
            return {}, {cs: "alice"}
        if b.startswith("download_error:"):
            raise RevertError(b.removeprefix("download_error:"))
        return {("node", "1"): {"changes": True}}, {cs: "alice"}

    def reverter(diffs, print_status):
        # single deterministic behavior derived from the sole caller's context;
        # for 'no_changes', downloader would have returned diffs but reverter returns []
        # Real caller sets that via a wrapper below.
        return [{"type": "node", "id": "1", "version": 5, "deleted": False,
                 "tags": {}, "coords": ("0", "0")}]

    def uploader(auth, changes, tags):
        cs = int(tags["comment"].split()[1])  # extracted from "Reverting <cs> by <user>"
        if cs in upload_exc:
            raise upload_exc[cs]

    return downloader, reverter, uploader


def test_build_tags_default_comment():
    got = build_tags(12345, "alice", None)
    assert got == {
        "created_by": "simple_revert.py",
        "comment": "Reverting 12345 by alice",
    }


def test_build_tags_custom_comment():
    got = build_tags(12345, "alice", "cleanup")
    assert got["comment"] == "cleanup"
    assert got["created_by"] == "simple_revert.py"


def test_run_all_success(tmp_path):
    ids = [100, 200, 300]
    p = _write_input(tmp_path, ids)
    behaviors = {i: "ok" for i in ids}
    dl, rv, up = _make_deps(behaviors)

    opts = RunOptions(input_file=p, comment=None, dry_run=False,
                      out_dir=tmp_path / "osc", retry_done=False)
    result = run(opts, auth="fake", downloader=dl, reverter=rv, uploader=up)

    assert result.total == 3
    assert result.success == 3
    assert result.failures == []
    assert result.aborted is False
    done_txt = (p.parent / (p.name + ".done.txt")).read_text(encoding="utf-8")
    for i in ids:
        assert str(i) in done_txt


def test_run_mixed_success_and_permanent_failure(tmp_path):
    ids = [100, 200, 300]
    p = _write_input(tmp_path, ids)
    behaviors = {100: "ok", 200: "download_error:Cannot revert redactions", 300: "ok"}
    dl, rv, up = _make_deps(behaviors)

    opts = RunOptions(input_file=p, comment=None, dry_run=False,
                      out_dir=tmp_path / "osc", retry_done=False)
    result = run(opts, auth="fake", downloader=dl, reverter=rv, uploader=up)

    assert result.total == 3
    assert result.success == 2
    assert len(result.failures) == 1
    assert result.failures[0][0] == 200
    assert "redactions" in result.failures[0][1]
    assert result.aborted is False
    failed_path = p.parent / (p.name + ".failed.txt")
    assert failed_path.exists()
    assert "200" in failed_path.read_text(encoding="utf-8")
    assert result.failed_path is not None
    assert result.failed_path == failed_path


def test_run_circuit_break_on_consecutive_transient(tmp_path):
    # THRESHOLD consecutive 429s trip the breaker.
    ids = list(range(100, 100 + CIRCUIT_BREAKER_THRESHOLD + 3))
    p = _write_input(tmp_path, ids)
    behaviors = {i: "ok" for i in ids}
    dl, rv, up = _make_deps(
        behaviors,
        upload_exc={i: HTTPError(429, "rate") for i in ids},
    )

    opts = RunOptions(input_file=p, comment=None, dry_run=False,
                      out_dir=tmp_path / "osc", retry_done=False)
    result = run(opts, auth="fake", downloader=dl, reverter=rv, uploader=up,
                 log=lambda *_: None)

    assert result.aborted is True
    assert result.abort_reason is not None and "transient" in result.abort_reason
    assert len(result.remaining) == 3  # THRESHOLD failed, 3 unattempted
    remaining_path = p.parent / (p.name + ".remaining.txt")
    assert remaining_path.exists()
    assert result.remaining_path is not None
    assert result.remaining_path == remaining_path


def test_run_permanent_failure_resets_transient_counter(tmp_path):
    # transient, transient, permanent, transient, transient — should NOT abort,
    # because permanent failure resets the counter.
    ids = [10, 20, 30, 40, 50]
    p = _write_input(tmp_path, ids)
    behaviors = {
        10: "ok",
        20: "ok",
        30: "download_error:Cannot revert redactions",  # permanent
        40: "ok",
        50: "ok",
    }
    dl, rv, up = _make_deps(
        behaviors,
        upload_exc={10: HTTPError(429, "x"), 20: HTTPError(429, "x"),
                    40: HTTPError(429, "x"), 50: HTTPError(429, "x")},
    )

    opts = RunOptions(input_file=p, comment=None, dry_run=False,
                      out_dir=tmp_path / "osc", retry_done=False)
    result = run(opts, auth="fake", downloader=dl, reverter=rv, uploader=up,
                 log=lambda *_: None)

    # With THRESHOLD=5, four transients broken by one permanent -> no abort
    assert result.aborted is False
    assert len(result.failures) == 5   # all 5 fail, but not aborted


def test_run_skips_done_ids(tmp_path):
    ids = [100, 200, 300]
    p = _write_input(tmp_path, ids)
    done_path = p.parent / (p.name + ".done.txt")
    done_path.write_text("100\n200\n", encoding="utf-8")

    behaviors = {300: "ok"}  # only 300 should be attempted
    dl, rv, up = _make_deps(behaviors)

    opts = RunOptions(input_file=p, comment=None, dry_run=False,
                      out_dir=tmp_path / "osc", retry_done=False)
    result = run(opts, auth="fake", downloader=dl, reverter=rv, uploader=up)

    assert result.skipped == 2
    assert result.total == 3
    assert result.success == 1


def test_run_retry_done_ignores_skip(tmp_path):
    ids = [100, 200]
    p = _write_input(tmp_path, ids)
    done_path = p.parent / (p.name + ".done.txt")
    done_path.write_text("100\n200\n", encoding="utf-8")

    behaviors = {100: "ok", 200: "ok"}
    dl, rv, up = _make_deps(behaviors)

    opts = RunOptions(input_file=p, comment=None, dry_run=False,
                      out_dir=tmp_path / "osc", retry_done=True)
    result = run(opts, auth="fake", downloader=dl, reverter=rv, uploader=up)

    assert result.skipped == 0
    assert result.success == 2


def test_run_dry_run_does_not_append_done(tmp_path):
    ids = [100, 200]
    p = _write_input(tmp_path, ids)
    behaviors = {100: "ok", 200: "ok"}
    dl, rv, up = _make_deps(behaviors)
    out_dir = tmp_path / "osc"

    opts = RunOptions(input_file=p, comment=None, dry_run=True,
                      out_dir=out_dir, retry_done=False)
    result = run(opts, auth=None, downloader=dl, reverter=rv, uploader=up)

    assert result.success == 2
    done_path = p.parent / (p.name + ".done.txt")
    assert not done_path.exists()  # dry-run does NOT append
    # osc files were written
    assert (out_dir / "100.osc").exists()
    assert (out_dir / "200.osc").exists()
