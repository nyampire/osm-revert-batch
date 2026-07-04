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
