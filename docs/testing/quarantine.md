# Flaky-Test Quarantine

> **Scope:** How a test that fails intermittently is taken out of the blocking CI lanes, where it
> still runs, and what it takes to bring it back.
> **Belongs here:** The registry, the plugin, the nightly job, the rules for adding and removing
> entries.
> **Does NOT belong here:** Why an individual test flakes (that lives in the bead the entry names).

## Why a single flake is expensive here

Every pull request and every merge-queue entry runs the unit and integration lanes as eleven groups.
Unit groups run with `--maxfail=1`, a rerun of only the failed jobs is refused (the lane totals
are checked over one run), and the merge queue re-runs every group for every entry. So one test that
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
lane groups, the lane totals' `--collect-only` count, `make` targets and local runs.

- **Default run:** listed tests are deselected (they appear in the `deselected` count, and the
  session header says how many are quarantined). The lane groups, lane totals and test budget
  count only selected tests, so they stay consistent.
- **`--quarantined`:** runs only the listed tests within the requested scope. Use it for diagnostics,
  including a single parameter; registry owners outside that scope need not be collected.
- **`--quarantined-full`:** runs the complete registry for monitoring. Every registered whole owner
  must survive actual collection and final selection, including marker and keyword filters. Every
  collected parameter of those owners must remain selected. A stale owner, partially stale registry,
  or filtered owner or parameter stops the run with a usage error. Pass whole paths, not `::` node
  selectors; scoped diagnostics use `--quarantined` instead.
- **Empty full registry:** a real, schema-valid registry with an empty `tests` list succeeds with a
  validated-empty notice only after complete successful collection. A missing registry is an error in
  full mode; ordinary default and diagnostic runs retain their missing-registry behavior.
- **Malformed registry:** a missing field, an unknown key, a parameter suffix, a bead id that is not
  `bu-...`, or a duplicate stops the run with a usage error.

The plugin lives under `tests/`, not in the `butlers` package, because pytest imports `-p` plugins
before the root `conftest.py`, whose guard explains a `butlers` package that resolves outside the
checkout. A plugin importing from `butlers` would turn that explanation into a bare `ImportError`.

The nightly workflow's `quarantined` job runs
`pytest tests/ roster/ --ignore=tests/e2e --quarantined-full -n 0` on a runner of its own, so a
quarantined test keeps producing a signal. It propagates pytest's status directly: arbitrary exit5,
test failures, collection or usage errors and timeouts are not empty-registry success. Existing
default nightly, bench and perf exclusions still apply; an excluded registered owner makes full
monitoring fail. That job is not a required check.

`tests/testing/test_quarantine.py` checks that every entry names a test that exists and that the
repo configuration really deselects them. Real pytest subprocess cases also exercise the actual
nightly command's completeness and exit behavior. Local synthetic controls establish this selection
contract; they do not establish that the current scheduled job ran, fix the quarantined causes, or
restore those owners to blocking coverage.

## Rules

- **Quarantine needs a cause and a bead.** Diagnose first. The bead records the failure signature,
  the run and job ids, the cause, and what removing the cause means.
- **No automatic reruns, no raised timeouts, no weakened assertions** as a substitute. Each of those
  hides the next real regression in the same test.
- **Quarantine through the registry, not by editing the test.** The registry touches no test
  file, so the quarantined test keeps its assertions, markers and fixtures exactly as they were,
  and removing the entry restores it unchanged.
- **Un-quarantine by fixing the cause.** The PR that removes the cause deletes the entry. Nothing
  else should bring a test back into the blocking lanes.

Quarantine is temporary: every entry waits on the fix its bead owns.

## Related Pages

- [Markers and Fixtures](markers-and-fixtures.md)
