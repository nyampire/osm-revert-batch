"""Tests for input file parser."""
from __future__ import annotations

from pathlib import Path

import pytest

from osm_revert_batch.io import parse_input


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "changesets.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_parses_simple_ids(tmp_path):
    p = _write(tmp_path, "12345\n12346\n12347\n")
    assert parse_input(p) == [(1, 12345), (2, 12346), (3, 12347)]


def test_ignores_blank_lines_and_full_comments(tmp_path):
    text = "# header\n\n12345\n\n# section 2\n12346\n"
    p = _write(tmp_path, text)
    assert parse_input(p) == [(3, 12345), (6, 12346)]


def test_strips_inline_comments(tmp_path):
    p = _write(tmp_path, "12345  # first\n12346#second\n")
    assert parse_input(p) == [(1, 12345), (2, 12346)]


def test_no_trailing_newline(tmp_path):
    p = _write(tmp_path, "12345\n12346")
    assert parse_input(p) == [(1, 12345), (2, 12346)]


def test_raises_on_non_integer(tmp_path):
    p = _write(tmp_path, "12345\nnot-a-number\n12347\n")
    with pytest.raises(ValueError) as exc:
        parse_input(p)
    assert "line 2" in str(exc.value)


def test_raises_on_negative(tmp_path):
    p = _write(tmp_path, "12345\n-1\n")
    with pytest.raises(ValueError) as exc:
        parse_input(p)
    assert "line 2" in str(exc.value)
