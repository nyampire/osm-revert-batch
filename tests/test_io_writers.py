"""Tests for .failed.txt and .remaining.txt writers."""
from __future__ import annotations

from pathlib import Path

from osm_revert_batch.io import (
    parse_input,
    write_failed_txt,
    write_remaining_txt,
)


def test_write_failed_txt_basic(tmp_path):
    p = tmp_path / "changesets.txt.failed.txt"
    write_failed_txt(
        p,
        source_name="changesets.txt",
        total_count=7,
        success_count=5,
        failures=[
            (12346, "Cannot revert redactions, see version 3 at ..."),
            (12348, "HTTP 412: Node 999 is still used"),
        ],
    )
    text = p.read_text(encoding="utf-8")
    assert "changesets.txt" in text
    assert "7" in text and "5" in text
    assert "# FAIL: Cannot revert redactions" in text
    assert "12346" in text
    assert "# FAIL: HTTP 412" in text
    assert "12348" in text


def test_write_failed_txt_roundtrip(tmp_path):
    p = tmp_path / "f.txt"
    write_failed_txt(
        p,
        source_name="input.txt",
        total_count=3,
        success_count=1,
        failures=[(200, "reason A"), (300, "reason B")],
    )
    got = parse_input(p)
    assert [cs for _, cs in got] == [200, 300]


def test_write_remaining_txt_basic(tmp_path):
    p = tmp_path / "changesets.txt.remaining.txt"
    write_remaining_txt(
        p,
        source_name="changesets.txt",
        aborted_at_line=42,
        aborted_at_cs_id=99999,
        reason="5 consecutive transient errors",
        remaining=[100000, 100001, 100002],
    )
    text = p.read_text(encoding="utf-8")
    assert "aborted" in text.lower()
    assert "line 42" in text or "line: 42" in text
    assert "99999" in text
    assert "5 consecutive transient errors" in text
    assert "osm-revert-batch" in text  # resume hint
    # payload IDs present
    for cs in (100000, 100001, 100002):
        assert str(cs) in text


def test_write_remaining_txt_roundtrip(tmp_path):
    p = tmp_path / "r.txt"
    write_remaining_txt(
        p,
        source_name="input.txt",
        aborted_at_line=5,
        aborted_at_cs_id=500,
        reason="ctrl-c",
        remaining=[600, 700, 800],
    )
    got = parse_input(p)
    assert [cs for _, cs in got] == [600, 700, 800]
