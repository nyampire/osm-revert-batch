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
