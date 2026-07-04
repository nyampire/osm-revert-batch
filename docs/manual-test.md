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
