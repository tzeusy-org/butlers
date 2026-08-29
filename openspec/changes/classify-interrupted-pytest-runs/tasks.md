# Tasks

## 1. Contract
- [x] 1.0 Record the direct baseline provenance of `bu-5hp74` / PR #3861 and
      `bu-ecizp` / PR #3869 in this sole active same-name change. The four
      baseline scenarios unrelated to exit-`2` (including the quality-gate
      receipt) remain verbatim; the existing Sentinel scenario's exit-`2`
      clause is intentionally refined.
- [x] 1.1 MODIFY the `testing` requirement **Pytest Run Verdicts Require a
      Positive Terminator**, rebuilt on the live baseline so archiving it drops
      no clause.
- [x] 1.2 Narrow the sentinel scenario's UNKNOWN list to `3`, `4`, `5`, and
      `128+signal`, keeping its name (OpenSpec's strict validator rejects a
      scenario rename inside a MODIFIED block), while the live-baseline rebuild
      preserves the replaced clause without a frozen ratchet exception.
- [x] 1.3 Add the interrupted-run scenario: exit `2` plus counts reporting
      failures is FAILED; exit `2` with no such counts stays UNKNOWN; the
      summary may never promote exit `2` to PASS.
- [x] 1.4 Add the scenario pinning the other nonzero statuses as immune to a
      summary line, so the narrowing cannot be read as general.

## 2. Fix
- [x] 2.1 `classify` collects the summary lines before the sentinel branch and
      hands the last body to `_exit_code_verdict`.
- [x] 2.2 `_interrupted_verdict` decides exit `2` from that body: failures make
      it FAILED, absence or a clean count leaves it UNKNOWN.
- [x] 2.3 Factor the `failed`/`error` test into `_reports_failures`, shared with
      `_summary_verdict`, so the two paths cannot drift.
- [x] 2.4 Record in the module docstring why exit `2` alone reads both
      terminators, and that the summary may only convict, never acquit.

## 3. Tests
- [x] 3.1 Red first: a realistic `--maxfail` log (`1 failed, 6482 passed` with
      `exit=2`) is FAILED, not UNKNOWN.
- [x] 3.2 Exit `2` with an `N errors` summary is FAILED.
- [x] 3.3 Exit `2` with no summary line is UNKNOWN.
- [x] 3.4 Exit `2` with a passing summary is UNKNOWN, and never PASS.
- [x] 3.5 Exit `2` reads the last summary line, not an earlier one.
- [x] 3.6 Regression: exit `5` is UNKNOWN even with a green summary in the log.
- [x] 3.7 Regression: exit `4` is UNKNOWN even with a failing summary in the log.
- [x] 3.8 Regression: exit `143` is UNKNOWN, not FAILED, with a failing summary.
- [x] 3.9 Regression: exit `0` is PASS even with a stale failing summary.
- [x] 3.10 End-to-end against real pytest: an xdist `--maxfail=1` run over a
      failing file really does exit `2`, and the gate reads that log as
      FAILED. Pins the premise, so a pytest/xdist change breaks loudly
      instead of leaving the classification quietly stale.
