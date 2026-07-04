"""File I/O for input, done, failed, and remaining files."""
from __future__ import annotations

import sys
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
