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
    log(f"    Uploaded:     {result.uploaded}")
    log(f"    No-op:        {result.no_op}  (already reverted or current state matches restore target)")
    log(f"    Empty:        {result.empty}  (changeset had no revertible actions)")
    log(f"  Failed:         {len(result.failures)}")
    for cs_id, reason in result.failures:
        log(f"    - {cs_id}: {reason}")
    if result.aborted:
        at = f"line {result.aborted_at[0]}, cs {result.aborted_at[1]}" if result.aborted_at else "-"
        log(f"Aborted at:       {at} ({result.abort_reason})")
        log(f"Remaining:        {len(result.remaining)}")
        if result.remaining:
            log(f"  Unattempted IDs: {', '.join(str(cs) for cs in result.remaining[:10])}"
                + (f" ... (+{len(result.remaining) - 10} more)" if len(result.remaining) > 10 else ""))
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
    except OSError as e:
        # sidecar file / out_dir I/O failure (permission, full disk, etc.)
        print(f"error: I/O failure: {e}", file=sys.stderr)
        return 2

    log = lambda msg: print(msg, file=sys.stderr)
    _print_summary(result, log)

    if result.failed_path is not None:
        log(f"Wrote {result.failed_path}")
    if result.remaining_path is not None:
        log(f"Wrote {result.remaining_path}")

    if result.aborted:
        return 3
    if result.failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
