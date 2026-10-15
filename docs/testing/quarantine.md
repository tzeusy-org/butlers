# Flaky-Test Quarantine

> **Scope:** How a test that fails intermittently is taken out of the blocking CI lanes, where it
> still runs, and what it takes to bring it back.
> **Belongs here:** The registry, the plugin, the nightly job, the rules for adding and removing
> entries.
> **Does NOT belong here:** Why an individual test flakes (that lives in the bead the entry names).

## Why a single flake is expensive here

Every pull request and every merge-queue entry runs the unit and integration lanes as eleven shards.
Unit shards run with `--maxfail=1`, a rerun of only the failed jobs is refused (the inventory
receipts no longer match), and the merge queue re-runs every shard for every entry. So one test that
fails 5% of the time fails a noticeable share of whole workflows, and each failure costs a full
re-run. Fixing the cause is always the goal; quarantine exists for the gap between finding a flake
and being able to remove its cause.

## How it works

`tests/quarantine.json` lists quarantined tests. Each entry has three fields:

| Field | Meaning |
| --- | --- |
| `node` | A whole test, `path/to/test_file.py::test_name` or `...::Class::test_name`. No `[param]` suffix: every parameter of a listed test is quarantined. |
| `bead` | The bead that owns the root cause and the exit criteria. |
| `reason` | One sentence on why the test flakes. |

`tests/quarantine.py` is a pytest plugin that `pyproject.toml` `addopts` load with
`-p tests.quarantine`, so it applies to every pytest invocation that reads the repo config: CI
shards, the inventory collector, `make` targets and local runs.

- **Default run:** listed tests are deselected (they appear in the `deselected` count, and the
  session header says how many are quarantined). The CI inventory, shard manifests and test budget
  count only selected tests, so they stay consistent.
- **`--quarantined`:** runs only the listed tests.
- **Malformed registry:** a missing field, an unknown key, a parameter suffix, a bead id that is not
  `bu-...`, or a duplicate stops the run with a usage error.

The plugin lives under `tests/`, not in the `butlers` package, because pytest imports `-p` plugins
before the root `conftest.py`, whose guard explains a `butlers` package that resolves outside the
checkout. A plugin importing from `butlers` would turn that explanation into a bare `ImportError`.

The nightly workflow's `quarantined` job runs
`pytest tests/ roster/ --ignore=tests/e2e --quarantined -n 0` on a runner of its own, so a
quarantined test keeps producing a signal. That job is not a required check.

`tests/testing/test_quarantine.py` checks that every entry names a test that exists and that the
repo configuration really deselects them.

## Rules

- **Quarantine needs a cause and a bead.** Diagnose first. The bead records the failure signature,
  the run and job ids, the cause, and what removing the cause means.
- **No automatic reruns, no raised timeouts, no weakened assertions** as a substitute. Each of those
  hides the next real regression in the same test.
- **Quarantine through the registry, not by editing the test.** The condensation ledger guard
  refuses marker, decorator and statement changes to existing tests and any `conftest.py` change
  without a proof, so a `nightly` marker or a conftest skip cannot land without proof work anyway.
  The registry touches no test file.
- **Un-quarantine by fixing the cause.** The PR that removes the cause deletes the entry. Nothing
  else should bring a test back into the blocking lanes.

## Related Pages

- [Markers and Fixtures](markers-and-fixtures.md)
- [Condensation Evidence](condensation-evidence.md)
