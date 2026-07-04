"""Tests for .done.txt read/append."""
from __future__ import annotations

from pathlib import Path

from osm_revert_batch.io import append_done, read_done_ids


def test_read_missing_file_returns_empty(tmp_path):
    assert read_done_ids(tmp_path / "nope.done.txt") == set()


def test_read_parses_ids(tmp_path):
    p = tmp_path / "d.txt"
    p.write_text("# header\n12345\n12346\n\n# more\n12347\n", encoding="utf-8")
    assert read_done_ids(p) == {12345, 12346, 12347}


def test_read_skips_invalid_line_with_warning(tmp_path, capsys):
    p = tmp_path / "d.txt"
    p.write_text("12345\nbad\n12346\n", encoding="utf-8")
    got = read_done_ids(p)
    assert got == {12345, 12346}
    err = capsys.readouterr().err
    assert "line 2" in err
    assert "bad" in err


def test_append_creates_file_with_header(tmp_path):
    p = tmp_path / "d.txt"
    append_done(p, 12345)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("#")           # header comment present
    assert "12345" in text
    assert text.rstrip().endswith("12345")


def test_append_appends_without_touching_existing(tmp_path):
    p = tmp_path / "d.txt"
    p.write_text("# pre-existing\n12300\n", encoding="utf-8")
    append_done(p, 12345)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert "# pre-existing" in lines
    assert "12300" in lines
    assert lines[-1] == "12345"


def test_append_then_read_roundtrip(tmp_path):
    p = tmp_path / "d.txt"
    for cs in (12345, 12346, 12347):
        append_done(p, cs)
    assert read_done_ids(p) == {12345, 12346, 12347}
