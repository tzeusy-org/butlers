# Read pytest's interrupted exit against its own counts, so red runs say FAILED

## Why

`bu-5hp74` gave `scripts/pytest_gate.py` its verdict contract: a run's outcome
comes from positive evidence that it finished, never from the absence of a
failure line. It pinned exit `2` -- along with `3`, `4`, `5`, and every
`128+signal` value -- as UNKNOWN, because a process that did not exit `0` or `1`
did not render a verdict.

That reading of exit `2` was right about the exit code and wrong about this
repository's runs. pytest exits `2` when the session is **interrupted**, and
under xdist `--maxfail=1` interrupts on the first ordinary test failure: the
controller sets `shouldstop` and raises `Interrupted`
(`xdist/dsession.py`), where a serial run would raise `Failed` and exit `1`.

Both halves hold on the normal parallel path. `pyproject.toml`'s `addopts`
carries `-n 3 --dist loadfile`, so nearly every pytest invocation runs under
xdist by default; `make test-qg-serial` deliberately overrides that with `-n 0`.
Every quality-gate invocation passes `--maxfail=1`: `QG_PYTEST_ARGS` in the
`Makefile`, and the low-context snippet in `CLAUDE.md`. So the moment
`bu-ecizp` wired the gate into the default `make test-qg`, genuine red parallel
runs started reporting UNKNOWN. Observed on that bead's first full run:

    1 failed, 6482 passed ... ## pytest-gate exit=2

Nothing is wrongly credited green -- exit `2` is nonzero, the `&&` chain still
fails closed. The defect is the *label*, and on this tool the label is the whole
product. A gate that cries UNKNOWN on the most ordinary failure there is teaches
its readers that UNKNOWN means "probably just a real failure". That is precisely
the reflex `bu-5hp74` exists to prevent, and it only works while UNKNOWN is
rare.

## Baseline provenance

`bu-5hp74` / PR #3861 added this requirement directly to
`openspec/specs/testing/spec.md`, with its four positive-terminator scenarios.
Its dependent `bu-ecizp` / PR #3869 then added the
`Quality-gate make targets produce a receipt and a verdict` scenario directly
to that same baseline requirement. Neither merge had a change-local delta.

This is the sole active `## MODIFIED Requirements` block for the shared
requirement. The four baseline scenarios unrelated to the exit-`2` refinement
(including the quality-gate receipt) remain verbatim; the existing Sentinel
scenario's exit-`2` clause is intentionally refined below. A second retroactive
same-name block would collide at archive, so recording the earlier direct edits
here preserves their provenance without introducing another future baseline
rewrite. This maintenance leaves the canonical baseline untouched.

## The decision

> Exit `2` is resolved against the log's last pytest summary line. Counts
> reporting `failed` or `error` make it FAILED. No summary line, or counts
> reporting neither, leave it UNKNOWN. The summary may take exit `2` **down** to
> FAILED and never **up** to PASS.

This does not weaken the positive-terminator rule, it applies it twice. The
summary line is already one of the two terminators the contract names; exit `2`
is the one status where the two disagree in a way that matters, because an
interrupted run still printed its counts on the way out. Reading both is
strictly more evidence than reading either.

The one-way direction is the load-bearing half. A `Ctrl-C` partway through a
green run also exits `2` and also prints a summary with no failures in it, and
that run never reached the tests it was interrupted before. Letting counts
promote exit `2` to PASS would reopen the exact hole this tool was built to
close.

## What deliberately does not change

- **Exit `5` stays UNKNOWN, never PASS.** Nothing was collected, so nothing was
  verified, whatever any stale summary line in the log says.
- **Exit `> 128` stays UNKNOWN, never FAILED.** A killed run is not a red run,
  and its counts describe only the part that happened before the signal.
- **Exits `3` and `4` stay UNKNOWN.** An internal error or a usage error means
  the gate misfired; there is no suite verdict to read.
- **A log with no positive terminator is still never a pass.**

Only exit `2` consults the summary. The other nonzero statuses are not
"incomplete verdicts waiting for corroboration" -- they are statements that no
verdict exists, and a counts line cannot contradict them.

## Rejected alternatives

- **Drop `--maxfail` from the gate invocations.** Trades a mislabelled verdict
  for a 25-minute wait on every red run, and leaves exit `2` mislabelled for
  anyone who passes `-x` by hand.
- **Classify exit `2` as FAILED on the code alone.** Would call a killed or
  `Ctrl-C`-ed run red, replacing a too-loud UNKNOWN with a false FAILED --
  and a false FAILED sends someone hunting a test that never ran.
- **Prefer the summary line over the sentinel whenever both exist.** Reverses
  the contract's ordering for every status, so a green summary from an earlier
  phase could outvote a nonzero exit.

## Impact

- `scripts/pytest_gate.py`: `classify` passes the log's last summary body to
  `_exit_code_verdict`, which routes exit `2` through a new
  `_interrupted_verdict`. The `failed`/`error` test is factored into
  `_reports_failures`, shared with `_summary_verdict`.
- `tests/scripts/test_pytest_gate.py`: the interrupted-run cases, plus
  regression cover pinning exits `0`, `1`, `4`, `5`, and `128+signal` as
  unaffected by any summary line in the log.
- No caller changes. `make test-qg`, `make test-qg-serial`, and the `CLAUDE.md`
  snippet keep their arguments and their exit statuses; only an exit-`2` red run
  on the default parallel path changes its printed verdict, from `UNKNOWN` to
  `FAILED`.
