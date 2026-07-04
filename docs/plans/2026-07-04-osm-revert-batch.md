# osm-revert-batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI (`osm-revert-batch`) that reads a text file of OSM changeset IDs, reverts each in order using `simple_revert` as a library, and handles multi-run workflows (skip-done, resume-after-abort, retry-on-transient).

**Architecture:** Thin orchestration layer over `simple_revert`'s Python API. One process = one OAuth prompt = many changeset reverts. Failure states are split across three sidecar files (`.done.txt`, `.failed.txt`, `.remaining.txt`) so the same input can be run repeatedly and converge toward completion.

**Tech Stack:** Python 3.12, `simple_revert >= 1.2.0` (PyPI), `pytest` (dev). No other runtime dependencies beyond stdlib.

## Global Constraints

Applies to every task:

- **Python 3.12+** (3.13 also OK). Do not add compatibility shims for 3.11 or earlier.
- **No new runtime deps** beyond `simple_revert` and the Python stdlib. `pytest` is dev-only.
- **`created_by` tag = `"simple_revert.py"`** on all uploaded changesets (upstream compat, chosen deliberately).
- **Comments in Japanese** where they add value; identifiers, function names, and log messages in English (project convention).
- **No emojis** in files.
- **No `Co-Authored-By:` trailer** in commit messages (per user memory).
- **TDD**: every task starts with a failing test. Do not write implementation before the test fails for the right reason.
- **Frequent commits**: one commit per task, message follows the pattern shown in each task's final step.
- Design reference: [docs/design.md](../design.md). If a task step conflicts with the spec, follow the spec unless the plan explicitly says "this supersedes design.md §X".

## File Structure

Files created by this plan:

```
osm-revert-batch/
├── pyproject.toml
├── README.md                         # updated in Task 10
├── .gitignore                        # already exists
├── docs/
│   ├── design.md                     # already exists (may be tweaked in Task 6)
│   └── plans/
│       └── 2026-07-04-osm-revert-batch.md   # this file
├── src/
│   └── osm_revert_batch/
│       ├── __init__.py               # empty
│       ├── io.py                     # parsers + writers
│       ├── errors.py                 # is_transient + classify_error
│       ├── retry.py                  # with_retry
│       ├── upload.py                 # upload_changes_with_auth (fork)
│       ├── batch.py                  # BatchRunner (main loop)
│       └── cli.py                    # argparse + entrypoint
└── tests/
    ├── __init__.py                   # empty
    ├── conftest.py                   # tmp_path helpers, fake HTTPError etc.
    ├── test_io_parse.py
    ├── test_io_done.py
    ├── test_io_writers.py
    ├── test_errors.py
    ├── test_retry.py
    ├── test_upload.py
    └── test_batch.py
```

The spec called for a 2-file layout (`cli.py` + `batch.py`). This plan splits into 6 modules (`io`, `errors`, `retry`, `upload`, `batch`, `cli`) so each unit fits in a single test file. `batch.py` orchestrates the others.

---

## Task 1: Repo scaffolding & pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `src/osm_revert_batch/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Package name `osm_revert_batch` importable via `pip install -e .`
  - `pytest` discovers tests under `tests/`
  - Console entrypoint `osm-revert-batch` (wired to `osm_revert_batch.cli:main`, which will exist by Task 9)

- [ ] **Step 1: Create pyproject.toml**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "osm-revert-batch"
version = "0.1.0"
description = "Batch wrapper around simple_revert with retry, skip-done, and resume"
readme = "README.md"
requires-python = ">=3.12"
license = {text = "ISC"}
authors = [{name = "nyampire"}]
dependencies = [
    "simple_revert>=1.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7",
]

[project.scripts]
osm-revert-batch = "osm_revert_batch.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Create empty package files**

Create `src/osm_revert_batch/__init__.py` with a single line:

```python
"""Batch runner for simple_revert."""
```

Create `tests/__init__.py` as an empty file (0 bytes).

- [ ] **Step 3: Create tests/conftest.py with shared fixtures**

Create `tests/conftest.py`:

```python
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
```

- [ ] **Step 4: Install the package in editable mode and verify pytest discovers zero tests**

Run:

```bash
cd ~/git/osm-revert-batch
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Expected output ends with: `no tests ran in 0.XXs`. If `pip install` fails because `simple_revert` is not on PyPI, fall back to `pip install git+https://github.com/Zverik/simple-revert@master#egg=simple_revert` and update `pyproject.toml`'s dependency entry to match.

- [ ] **Step 5: Commit**

```bash
cd ~/git/osm-revert-batch
git add pyproject.toml src/osm_revert_batch/__init__.py tests/__init__.py tests/conftest.py
git commit -m "feat: package scaffolding with pyproject.toml and pytest config"
```

---

## Task 2: `io.py` — parse_input

**Files:**
- Create: `src/osm_revert_batch/io.py`
- Create: `tests/test_io_parse.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_input(path: Path) -> list[tuple[int, int]]`
    - Returns list of `(line_number_1_indexed, changeset_id)` pairs.
    - Ignores blank lines and `#`-comment lines.
    - Strips inline `#` comments (`12345  # note` → `(N, 12345)`).
    - Raises `ValueError` with a line-number-prefixed message on any non-integer content.

- [ ] **Step 1: Write the failing test**

Create `tests/test_io_parse.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_io_parse.py -v`
Expected: All 6 tests fail with `ModuleNotFoundError: No module named 'osm_revert_batch.io'`.

- [ ] **Step 3: Implement parse_input**

Create `src/osm_revert_batch/io.py`:

```python
"""File I/O for input, done, failed, and remaining files."""
from __future__ import annotations

from pathlib import Path


def parse_input(path: Path) -> list[tuple[int, int]]:
    """Parse a changeset-ID list file.

    Returns [(line_no, changeset_id), ...] with line numbers 1-indexed.
    Ignores blank lines and `#` comments (both full-line and inline).
    Raises ValueError with a line-number-prefixed message on invalid content.
    """
    items: list[tuple[int, int]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            # strip inline # comment
            head = raw.split("#", 1)[0].strip()
            if not head:
                continue
            try:
                cs_id = int(head)
            except ValueError as e:
                raise ValueError(f"line {line_no}: not an integer: {head!r}") from e
            if cs_id <= 0:
                raise ValueError(f"line {line_no}: changeset ID must be positive: {cs_id}")
            items.append((line_no, cs_id))
    return items
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_io_parse.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/io.py tests/test_io_parse.py
git commit -m "feat(io): parse_input handles comments, blanks, and validation"
```

---

## Task 3: `io.py` — read_done_ids + append_done

**Files:**
- Modify: `src/osm_revert_batch/io.py` (add two functions)
- Create: `tests/test_io_done.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `read_done_ids(path: Path) -> set[int]`
    - Returns set of IDs from `.done.txt`; empty set if file missing.
    - Same comment/blank rules as `parse_input`. On invalid line, prints a warning to stderr and skips (does NOT raise — we don't want a corrupt sidecar to block a real run).
  - `append_done(path: Path, cs_id: int) -> None`
    - Appends `{cs_id}\n` to the file. Creates it with a header comment if missing.
    - Calls `flush()` after write (no fsync).

- [ ] **Step 1: Write the failing test**

Create `tests/test_io_done.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_io_done.py -v`
Expected: 6 failures, all with `ImportError: cannot import name 'append_done'` or `'read_done_ids'`.

- [ ] **Step 3: Implement read_done_ids and append_done**

Append to `src/osm_revert_batch/io.py`:

```python
import sys


def read_done_ids(path: Path) -> set[int]:
    """Read a .done.txt file into a set of IDs.

    Returns empty set if the file does not exist. Skips (but warns to stderr)
    on invalid lines rather than raising, to avoid blocking a real run on
    a corrupt sidecar.
    """
    if not path.exists():
        return set()
    ids: set[int] = set()
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            head = raw.split("#", 1)[0].strip()
            if not head:
                continue
            try:
                ids.add(int(head))
            except ValueError:
                print(
                    f"warning: {path.name} line {line_no}: skipping invalid ID {head!r}",
                    file=sys.stderr,
                )
    return ids


def append_done(path: Path, cs_id: int) -> None:
    """Crash-safe append of a single changeset ID to .done.txt.

    Creates the file with a comment header if missing. Flushes after write;
    does not fsync (spec 20250704 §done.txt).
    """
    new_file = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new_file:
            f.write("# osm-revert-batch: successful reverts\n")
            f.write("# do not edit; delete this file or use --retry-done to reset\n")
            f.write("\n")
        f.write(f"{cs_id}\n")
        f.flush()
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_io_done.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/io.py tests/test_io_done.py
git commit -m "feat(io): read_done_ids and append_done for skip-on-rerun"
```

---

## Task 4: `io.py` — write_failed_txt + write_remaining_txt

**Files:**
- Modify: `src/osm_revert_batch/io.py` (add two functions)
- Create: `tests/test_io_writers.py`

**Interfaces:**
- Consumes: `parse_input` (used only in the round-trip test).
- Produces:
  - `write_failed_txt(path, source_name, total_count, success_count, failures) -> None`
    - `failures: list[tuple[int, str]]` — (changeset_id, reason).
    - Overwrites the file. Emits a header comment, then per-failure `# FAIL: <reason>` followed by the bare ID line.
  - `write_remaining_txt(path, source_name, aborted_at_line, aborted_at_cs_id, reason, remaining) -> None`
    - `remaining: list[int]` — unattempted IDs.
    - Overwrites the file. Emits header comments (timestamp, abort context, resume-command hint), then one ID per line.

- [ ] **Step 1: Write the failing test**

Create `tests/test_io_writers.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_io_writers.py -v`
Expected: 4 failures with `ImportError: cannot import name 'write_failed_txt'` (or `write_remaining_txt`).

- [ ] **Step 3: Implement the writers**

Append to `src/osm_revert_batch/io.py`:

```python
from datetime import datetime


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_failed_txt(
    path: Path,
    *,
    source_name: str,
    total_count: int,
    success_count: int,
    failures: list[tuple[int, str]],
) -> None:
    """Write failure list in same-format-as-input for review + retry.

    Overwrites the destination. Each failure gets a `# FAIL: <reason>`
    comment immediately before its bare-ID line.
    """
    failed_count = len(failures)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# generated by osm-revert-batch on {_timestamp()}\n")
        f.write(
            f"# input: {source_name} "
            f"({total_count} IDs, {success_count} succeeded, {failed_count} failed)\n"
        )
        f.write("\n")
        for cs_id, reason in failures:
            f.write(f"# FAIL: {reason}\n")
            f.write(f"{cs_id}\n")
            f.write("\n")


def write_remaining_txt(
    path: Path,
    *,
    source_name: str,
    aborted_at_line: int,
    aborted_at_cs_id: int,
    reason: str,
    remaining: list[int],
) -> None:
    """Write unattempted IDs to enable resumption after abort.

    Overwrites the destination. Header records the abort context and the
    exact command to resume.
    """
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# generated by osm-revert-batch on {_timestamp()}\n")
        f.write(
            f"# aborted at line {aborted_at_line} (changeset {aborted_at_cs_id}): {reason}\n"
        )
        f.write(f"# {len(remaining)} changesets remaining, resume by running:\n")
        f.write(f"#   osm-revert-batch {path.name}\n")
        f.write("\n")
        for cs_id in remaining:
            f.write(f"{cs_id}\n")
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_io_writers.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/io.py tests/test_io_writers.py
git commit -m "feat(io): write_failed_txt and write_remaining_txt with roundtrip format"
```

---

## Task 5: `errors.py` — is_transient + classify_error

**Files:**
- Create: `src/osm_revert_batch/errors.py`
- Create: `tests/test_errors.py`

**Interfaces:**
- Consumes: `simple_revert.common.HTTPError`, `simple_revert.common.RevertError`, `requests.ConnectionError`, `requests.Timeout`.
- Produces:
  - `is_transient(exc: BaseException) -> bool`
    - True for `HTTPError` with `code == 429` or `500 <= code < 600`.
    - True for `RevertError` whose message wraps such an HTTPError (regex `HTTPError\((\d+),`). This is required because `simple_revert.simple_revert.download_changesets` calls `api_request` with `sysexit_message=...`, which re-wraps HTTPError into RevertError.
    - True for `requests.ConnectionError`, `requests.Timeout`, and other `requests.RequestException` subclasses.
    - False otherwise.
  - `classify_error(exc: BaseException) -> str`
    - `HTTPError` → `f"HTTP {code}: {message[:200]}"`.
    - `RevertError` → `message` (unchanged).
    - `requests.ConnectionError` / `Timeout` / other `RequestException` → `f"Network: {exc}"`.
    - Anything else → `f"Unexpected: {type(exc).__name__}: {exc}"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_errors.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_errors.py -v`
Expected: All tests fail with `ModuleNotFoundError: No module named 'osm_revert_batch.errors'`.

- [ ] **Step 3: Implement errors.py**

Create `src/osm_revert_batch/errors.py`:

```python
"""Exception categorization for retry and reporting."""
from __future__ import annotations

import re

import requests
from simple_revert.common import HTTPError, RevertError


_WRAPPED_HTTP_CODE_RE = re.compile(r"HTTPError\((\d+),")


def _http_code_is_transient(code: int) -> bool:
    return code == 429 or 500 <= code < 600


def is_transient(exc: BaseException) -> bool:
    """True iff the error is worth retrying (transient network/server issue)."""
    if isinstance(exc, HTTPError):
        return _http_code_is_transient(exc.code)
    if isinstance(exc, RevertError):
        # download_changesets wraps HTTPError into RevertError; detect it.
        m = _WRAPPED_HTTP_CODE_RE.search(exc.message)
        if m:
            return _http_code_is_transient(int(m.group(1)))
        return False
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.RequestException):
        return True
    return False


def classify_error(exc: BaseException) -> str:
    """Return a short human-readable label for logging and failed.txt."""
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}: {exc.message[:200]}"
    if isinstance(exc, RevertError):
        return exc.message
    if isinstance(exc, requests.RequestException):
        return f"Network: {exc}"
    return f"Unexpected: {type(exc).__name__}: {exc}"
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_errors.py -v`
Expected: All 16 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/errors.py tests/test_errors.py
git commit -m "feat(errors): is_transient and classify_error with RevertError-wrap detection"
```

---

## Task 6: `retry.py` — with_retry

**Files:**
- Create: `src/osm_revert_batch/retry.py`
- Create: `tests/test_retry.py`
- Modify: `docs/design.md` (align spec with implementation — see Step 6)

**Interfaces:**
- Consumes: `is_transient` from `errors.py`.
- Produces:
  - `MAX_RETRIES: int = 3` (module-level)
  - `BASE_BACKOFF_SECONDS: float = 1.0`
  - `MAX_BACKOFF_SECONDS: float = 60.0`
  - `with_retry(fn: Callable[[], T], *, sleep=time.sleep, log=print_to_stderr) -> T`
    - Injectable `sleep` and `log` for testing.
    - Runs `fn()`, catches transient exceptions, sleeps, retries. Non-transient exceptions bubble up on first hit. Transient exceptions after `MAX_RETRIES` also bubble up.

- [ ] **Step 1: Write the failing test**

Create `tests/test_retry.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_retry.py -v`
Expected: 6 failures with `ModuleNotFoundError: No module named 'osm_revert_batch.retry'`.

- [ ] **Step 3: Implement retry.py**

Create `src/osm_revert_batch/retry.py`:

```python
"""Retry wrapper for transient HTTP/network failures."""
from __future__ import annotations

import sys
import time
from typing import Callable, TypeVar

from .errors import classify_error, is_transient


MAX_RETRIES: int = 3
BASE_BACKOFF_SECONDS: float = 1.0
MAX_BACKOFF_SECONDS: float = 60.0

T = TypeVar("T")


def _default_log(msg: str) -> None:
    print(msg, file=sys.stderr)


def with_retry(
    fn: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _default_log,
) -> T:
    """Call fn(), retrying on transient exceptions with exponential backoff.

    - Non-transient exceptions are re-raised on the first hit.
    - Transient exceptions are retried up to MAX_RETRIES times.
    - Backoff: BASE * 2**attempt, clamped to MAX_BACKOFF_SECONDS.
    - `sleep` and `log` are injectable for testing.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as e:
            if not is_transient(e) or attempt >= MAX_RETRIES:
                raise
            wait = min(BASE_BACKOFF_SECONDS * (2 ** attempt), MAX_BACKOFF_SECONDS)
            log(f"transient error: {classify_error(e)}, retry in {wait:.1f}s")
            sleep(wait)
            attempt += 1
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_retry.py -v`
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/retry.py tests/test_retry.py
git commit -m "feat(retry): with_retry with exponential backoff and injectable sleep"
```

- [ ] **Step 6: Align design.md with the "no Retry-After" reality**

`simple_revert`'s `api_request` discards response headers (only body text survives via `HTTPError.message`), so honoring `Retry-After` would require patching upstream. This plan opts out. Update the spec.

Open `docs/design.md`. Find the section `### with_retry(fn: Callable) -> T` and locate the bullet mentioning `Retry-After`. Change:

```
- HTTP 429 で `Retry-After` ヘッダが付いていればそれを使う。無ければ
  `BASE_BACKOFF_SECONDS * (2 ** attempts)` (= 1, 2, 4 秒)
```

to:

```
- backoff は常に `BASE_BACKOFF_SECONDS * (2 ** attempts)` (= 1, 2, 4 秒)。
  `Retry-After` ヘッダは `simple_revert.common.api_request` が
  response.text しか保持しないため参照不能。将来必要なら api_request の
  monkey-patch で対応する
```

Then commit:

```bash
git add docs/design.md
git commit -m "docs: use exponential backoff only; Retry-After needs api_request patch"
```

---

## Task 7: `upload.py` — upload_changes_with_auth (fork)

**Files:**
- Create: `src/osm_revert_batch/upload.py`
- Create: `tests/test_upload.py`

**Interfaces:**
- Consumes:
  - `simple_revert.common.auth_request` (unchanged from upstream — used for changeset create/upload/close)
  - `simple_revert.common.changes_to_osc`
  - `simple_revert.common.changeset_xml`
  - `simple_revert.common.HTTPError`
- Produces:
  - `upload_changes_with_auth(auth, changes: list[dict], changeset_tags: dict[str, str]) -> None`
    - Fork of `simple_revert.common.upload_changes` with three differences:
      1. Takes `auth` as parameter (does not call `read_auth()`).
      2. On changeset upload HTTPError, re-raises (upstream had `pass` blocks that silently swallowed 412).
      3. Wraps 412 into a more descriptive HTTPError (same code, same message — no semantic change; kept for symmetry with upstream but explicit).
    - Best-effort `changeset/close` — swallows errors from close but not from upload.

- [ ] **Step 1: Write the failing test**

Create `tests/test_upload.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_upload.py -v`
Expected: 5 failures, all `ModuleNotFoundError: No module named 'osm_revert_batch.upload'`.

- [ ] **Step 3: Implement upload.py**

Create `src/osm_revert_batch/upload.py`:

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_upload.py -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/upload.py tests/test_upload.py
git commit -m "feat(upload): fork upload_changes to propagate HTTP errors and take auth param"
```

---

## Task 8: `batch.py` — BatchRunner (main loop)

**Files:**
- Create: `src/osm_revert_batch/batch.py`
- Create: `tests/test_batch.py`

**Interfaces:**
- Consumes:
  - `io.parse_input`, `io.read_done_ids`, `io.append_done`, `io.write_failed_txt`, `io.write_remaining_txt`
  - `errors.classify_error`, `errors.is_transient`
  - `retry.with_retry`
  - `upload.upload_changes_with_auth`
  - `simple_revert.simple_revert.download_changesets`, `revert_changes`, `print_status`
  - `simple_revert.common.changes_to_osc`
- Produces:
  - `CIRCUIT_BREAKER_THRESHOLD: int = 5`
  - `RunOptions` dataclass with `input_file: Path`, `comment: str | None`, `dry_run: bool`, `out_dir: Path`, `retry_done: bool`.
  - `RunResult` dataclass with `total: int`, `skipped: int`, `success: int`, `failures: list[tuple[int, str]]`, `remaining: list[int]`, `aborted: bool`, `aborted_at: tuple[int, int] | None`, `abort_reason: str | None`.
  - `build_tags(cs_id: int, user: str, user_comment: str | None) -> dict[str, str]`
  - `run(options: RunOptions, *, auth=None, downloader=None, reverter=None, uploader=None, log=print_to_stderr) -> RunResult`
    - Dependency-inject the three external calls (`download_changesets`, `revert_changes`, `upload_changes_with_auth`) so tests never touch the network. Defaults wire to the real ones.
    - Writes `<input>.done.txt` incrementally as successes happen.
    - At return time, writes `<input>.failed.txt` if failures exist and `<input>.remaining.txt` if `aborted`.
    - Does NOT call `sys.exit` — the CLI layer does that from `RunResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch.py`:

```python
"""Tests for BatchRunner (batch.run)."""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_batch.py -v`
Expected: 8 failures with `ModuleNotFoundError: No module named 'osm_revert_batch.batch'`.

- [ ] **Step 3: Implement batch.py**

Create `src/osm_revert_batch/batch.py`:

```python
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
```

Note: `with_retry` receives lambdas that capture `cs_id`, `changes`, `tags`, etc. — verify the enclosing scope doesn't shadow them (each iteration has its own bindings). Python closes late-binding late over the loop variable; that's fine here because the lambda is called synchronously within the same iteration.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_batch.py -v`
Expected: All 8 tests pass. If `test_run_dry_run_does_not_append_done` fails because `out_dir` didn't get created, add the `mkdir(parents=True, exist_ok=True)` — the implementation already does this above.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/batch.py tests/test_batch.py
git commit -m "feat(batch): main loop with circuit breaker, skip-done, and dry-run"
```

---

## Task 9: `cli.py` — argparse + main

**Files:**
- Create: `src/osm_revert_batch/cli.py`

**Interfaces:**
- Consumes: `batch.RunOptions`, `batch.run`, `simple_revert.common.read_auth`.
- Produces:
  - `main(argv: list[str] | None = None) -> int`
    - Parses argv, calls `read_auth()` (unless `--dry-run`), calls `batch.run`, prints summary, returns exit code (0/1/2/3 per spec).
  - Wired to console script `osm-revert-batch` via `pyproject.toml` (already done in Task 1).

- [ ] **Step 1: Implement cli.py**

Note: this task has no unit tests. It's a thin glue layer; the manual test in Task 10 exercises it end-to-end. If you want a smoke test, `pytest --co` should still find nothing wrong.

Create `src/osm_revert_batch/cli.py`:

```python
"""argparse entrypoint for osm-revert-batch."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from simple_revert.common import read_auth

from . import batch


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="osm-revert-batch",
        description=(
            "Batch wrapper around simple_revert. Reads a text file of "
            "OSM changeset IDs and reverts each one, tracking done/failed/"
            "remaining state in sidecar files."
        ),
    )
    p.add_argument("input_file", type=Path,
                   help="Text file, one changeset ID per line, # for comments")
    p.add_argument("--comment", default=None,
                   help="Shared comment for all revert changesets "
                        "(default: 'Reverting {id} by {user}')")
    p.add_argument("--dry-run", action="store_true",
                   help="Do not authenticate or upload; write per-changeset .osc "
                        "files to --out-dir instead")
    p.add_argument("--out-dir", type=Path, default=Path("./osc-out"),
                   help="Dry-run output directory (default: ./osc-out)")
    p.add_argument("--retry-done", action="store_true",
                   help="Ignore <input>.done.txt and reattempt every ID "
                        "(append continues, history preserved)")
    return p


def _print_summary(result: batch.RunResult, log) -> None:
    log("=== Summary ===")
    log(f"Total input:      {result.total} changesets ({result.skipped} skipped via .done.txt)")
    log(f"Processed:        {result.total - result.skipped - len(result.remaining)}")
    log(f"  Succeeded:      {result.success}")
    log(f"  Failed:         {len(result.failures)}")
    if result.aborted:
        at = f"line {result.aborted_at[0]}, cs {result.aborted_at[1]}" if result.aborted_at else "-"
        log(f"Aborted at:       {at} ({result.abort_reason})")
        log(f"Remaining:        {len(result.remaining)}")
    else:
        log("Aborted at:       -")
        log(f"Remaining:        0")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.input_file.exists():
        print(f"error: input file not found: {args.input_file}", file=sys.stderr)
        return 2

    try:
        options = batch.RunOptions(
            input_file=args.input_file,
            comment=args.comment,
            dry_run=args.dry_run,
            out_dir=args.out_dir,
            retry_done=args.retry_done,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    auth = None
    if not args.dry_run:
        try:
            auth = read_auth()
        except Exception as e:
            print(f"error: OAuth failed: {e}", file=sys.stderr)
            return 2

    try:
        result = batch.run(options, auth=auth)
    except ValueError as e:
        # parse_input raises ValueError with line-numbered message
        print(f"error: {e}", file=sys.stderr)
        return 2

    log = lambda msg: print(msg, file=sys.stderr)
    _print_summary(result, log)

    if result.failures:
        failed_path = args.input_file.with_suffix(args.input_file.suffix + ".failed.txt")
        log(f"Wrote {failed_path}")
    if result.aborted and result.remaining:
        remaining_path = args.input_file.with_suffix(args.input_file.suffix + ".remaining.txt")
        log(f"Wrote {remaining_path}")

    if result.aborted:
        return 3
    if result.failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-test the CLI on a bogus input file**

Run:

```bash
cd ~/git/osm-revert-batch
source .venv/bin/activate
osm-revert-batch /nonexistent/path.txt ; echo "exit: $?"
```

Expected: `error: input file not found: /nonexistent/path.txt` on stderr, `exit: 2` on stdout.

- [ ] **Step 3: Smoke-test parse failure exit code**

Run:

```bash
echo "not-a-number" > /tmp/bad_input.txt
osm-revert-batch /tmp/bad_input.txt ; echo "exit: $?"
```

Expected: `error: line 1: not an integer: 'not-a-number'` on stderr, `exit: 2`.

- [ ] **Step 4: Smoke-test dry-run with an empty-diff scenario (no network needed if input is bogus IDs)**

This step exists only to prove the argparse and dry-run wiring works. Real dry-run testing on live IDs is Task 10.

Run:

```bash
echo "12345" > /tmp/dry.txt
osm-revert-batch /tmp/dry.txt --dry-run --out-dir /tmp/osc-out ; echo "exit: $?"
```

Expected: the process will actually reach `simple_revert.download_changesets`, which will hit the OSM API. If your network is up and changeset 12345 exists, an .osc file (or a failure recorded to `/tmp/dry.txt.failed.txt`) will be produced. If offline, you'll see a `Network:` failure recorded. Either outcome demonstrates the CLI wiring works.

Do NOT commit `/tmp/dry.txt` or `/tmp/osc-out` — they're outside the repo.

- [ ] **Step 5: Commit**

```bash
git add src/osm_revert_batch/cli.py
git commit -m "feat(cli): argparse entrypoint with summary and exit-code mapping"
```

---

## Task 10: README and manual test protocol

**Files:**
- Modify: `README.md`
- Create: `docs/manual-test.md`

**Interfaces:**
- Consumes: nothing.
- Produces: user-facing usage docs and a repeatable manual-test protocol for exercising the tool against real OSM.

- [ ] **Step 1: Rewrite README.md**

Replace `README.md` contents with:

```markdown
# osm-revert-batch

Batch wrapper around [`simple_revert`](https://github.com/Zverik/simple-revert)
for reverting many OSM changesets from a text file, with:

- Single OAuth prompt per run (no per-changeset browser popup)
- Retry with exponential backoff on transient errors (HTTP 429 / 5xx / net)
- Circuit breaker on 5 consecutive transient failures
- Skip already-processed IDs on re-run via `<input>.done.txt`
- Failure summary in `<input>.failed.txt` (same format as input, re-runnable)
- Resume file `<input>.remaining.txt` when aborted

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

Input file (one changeset ID per line, `#` for comments):

```
# 2024-11 mass-tag mistake
12345
12346

# 2024-12 revert requests
12348
```

Run:

```bash
osm-revert-batch changesets.txt
```

For dry-run (no OAuth, generates `.osc` files for JOSM):

```bash
osm-revert-batch changesets.txt --dry-run --out-dir ./review
```

To reset skip state and reattempt everything:

```bash
osm-revert-batch changesets.txt --retry-done
```

## Sidecar files

For input `changesets.txt`:

| File | Purpose | Lifecycle |
|---|---|---|
| `changesets.txt.done.txt` | IDs successfully reverted | Appended per success; persists across runs |
| `changesets.txt.failed.txt` | IDs that failed in the last run, with reasons | Overwritten each run |
| `changesets.txt.remaining.txt` | IDs unattempted due to abort | Written only on abort |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All processed IDs succeeded (differences skipped included) |
| 1 | Ran to completion but 1+ failures |
| 2 | Pre-run error (missing input, parse failure, OAuth denied) |
| 3 | Aborted (circuit breaker or Ctrl+C); `.remaining.txt` written |

## Design & manual test

- Design: [docs/design.md](docs/design.md)
- Manual test protocol: [docs/manual-test.md](docs/manual-test.md)
```

- [ ] **Step 2: Create docs/manual-test.md**

Create `docs/manual-test.md`:

```markdown
# Manual Test Protocol

Automated E2E against the real OSM API is intentionally not in CI: reverts
have real side effects on OSM data. Before releasing changes, run this
protocol manually against a small set of your own changesets.

## Prerequisites

1. An OSM account with recent changesets you're willing to revert twice
   (once via this tool, once to un-revert).
2. `pip install -e .[dev]` completed.
3. `pytest` shows all green.

## Test 1: Dry-run against real IDs

Create `/tmp/mytest.txt` with 2-3 of your recent changeset IDs.

```bash
osm-revert-batch /tmp/mytest.txt --dry-run --out-dir /tmp/mytest-osc
```

Verify:

- No OAuth browser popup.
- `/tmp/mytest-osc/<id>.osc` exists for each ID that had non-empty changes.
- `/tmp/mytest.txt.done.txt` does NOT exist (dry-run doesn't append).
- Open a `.osc` in JOSM to visually confirm the revert is what you expect.

## Test 2: Real upload with one benign ID

Create `/tmp/single.txt` with ONE changeset ID (a small, low-risk one).

```bash
osm-revert-batch /tmp/single.txt --comment "manual test of osm-revert-batch"
```

Verify:

- Exactly one OAuth browser popup at start.
- On OSM website, your new revert changeset is visible with:
  - Comment: `manual test of osm-revert-batch`
  - `created_by=simple_revert.py` tag
- `/tmp/single.txt.done.txt` was created with the ID.
- Exit code 0 (`echo $?`).

## Test 3: Re-run the same input skips the done ID

```bash
osm-revert-batch /tmp/single.txt --comment "should skip"
```

Verify:

- Log line: `skipping 1 already-done changesets (from single.txt.done.txt)`.
- No OAuth popup? Actually there will still be one (OAuth token is not
  persisted across processes). But no upload happens.
- Exit code 0.
- No new OSM changeset created.

## Test 4: Redaction failure captured

Find a changeset that touches an object whose earlier version has been
redacted (OSM history shows a "hidden" version). Add its ID to
`/tmp/redacted.txt`.

```bash
osm-revert-batch /tmp/redacted.txt
```

Verify:

- `/tmp/redacted.txt.failed.txt` exists with `# FAIL: Cannot revert redactions...`
- Exit code 1.
- The failed ID is NOT in `/tmp/redacted.txt.done.txt`.

## Cleanup

Delete the sidecar files if you don't need them:

```bash
rm -f /tmp/mytest.txt* /tmp/single.txt* /tmp/redacted.txt* /tmp/dry.txt* /tmp/bad_input.txt
rm -rf /tmp/mytest-osc /tmp/osc-out
```

Un-revert your test changesets from Test 2 by reverting the revert (yes,
recursively).
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/manual-test.md
git commit -m "docs: README rewrite + manual test protocol against live OSM"
```

- [ ] **Step 4: Full test suite passes**

Run:

```bash
cd ~/git/osm-revert-batch
source .venv/bin/activate
pytest -v
```

Expected: all tests from Tasks 2-8 pass (roughly 40+ tests total). If any fail, fix before considering the plan complete.

- [ ] **Step 5: Final verification of file structure**

Run:

```bash
cd ~/git/osm-revert-batch
find . -not -path './.venv/*' -not -path './.git/*' -not -path '*/__pycache__/*' -type f | sort
```

Expected output (approximately):

```
./.gitignore
./README.md
./docs/design.md
./docs/manual-test.md
./docs/plans/2026-07-04-osm-revert-batch.md
./pyproject.toml
./src/osm_revert_batch/__init__.py
./src/osm_revert_batch/batch.py
./src/osm_revert_batch/cli.py
./src/osm_revert_batch/errors.py
./src/osm_revert_batch/io.py
./src/osm_revert_batch/retry.py
./src/osm_revert_batch/upload.py
./tests/__init__.py
./tests/conftest.py
./tests/test_batch.py
./tests/test_errors.py
./tests/test_io_done.py
./tests/test_io_parse.py
./tests/test_io_writers.py
./tests/test_retry.py
./tests/test_upload.py
```

Anything missing from this list = a task was skipped or a file was
misplaced. Anything extra = investigate before shipping.

---

## Notes for the implementing engineer

- **When tests fail with `ImportError` for `simple_revert.common`**: the
  package is not installed. Re-run `pip install -e '.[dev]'`.
- **When `test_upload.py` fails despite a correct implementation**: check
  that `monkeypatch.setattr` is patching `osm_revert_batch.upload.auth_request`
  and NOT `simple_revert.common.auth_request` — the former is the reference
  actually called (imported by name at module load).
- **When batch tests fail on Path handling**: the sidecar filename pattern
  is `input.txt.done.txt`, not `input.done.txt`. Do NOT use `path.with_suffix`
  naively — that strips the original suffix. Use
  `path.with_suffix(path.suffix + ".done.txt")` or `Path(str(path) + ".done.txt")`.
- **When TDD feels slow**: the discipline is protecting you from the
  upstream weirdness (RevertError-wraps-HTTPError, silent pass on 412).
  Don't skip it.
