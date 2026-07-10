## Why

The `finance-alerts` spec's "Automated Periodic Summaries" requirement still
carries a placeholder scenario ("Month-over-month trend content — pending
decision (bu-7hogl)") that asserts *no* requirement and says "resolve via
bu-7hogl first". That decision shipped: bu-7hogl was decided **RESTORE** and
PR #3024 (`ab5b27b8e`) added the month-over-month "notable changes" trend back
to the `monthly-finance-digest` job (`_month_over_month_trend` +
`run_monthly_finance_digest`). The spec is the only place still calling it open.

Re-verifying the three finance specs that `roster/finance/AGENTS.md` flagged as
stale (finance-alerts, butler-finance, finance-crud-operations): they were
**already synced** to the four-job architecture by bu-rvz2o (PR #2991) — old
task names survive only as historical "this replaced X" notes. This MoM
scenario is the last genuinely stale spec statement, so the AGENTS.md flag is
retired here too.

## What Changes

- **`finance-alerts`: replace the pending-decision MoM scenario with the shipped
  behavior.** The "Automated Periodic Summaries" requirement is modified so that
  its month-over-month scenario asserts the restored, implemented behavior
  instead of deferring to bu-7hogl. The three sibling scenarios under that
  requirement (Monthly finance digest, Budget thresholds via the daily insight
  scan, Per-transaction anomaly candidates) are carried forward unchanged; the
  "Monthly finance digest" scenario gains one cross-reference bullet pointing at
  the trend scenario. Wording verified line-by-line against
  `_month_over_month_trend` / `run_monthly_finance_digest`.

- **`roster/finance/AGENTS.md`: retire the now-stale flags.** The "notes to self"
  bullet claiming the digest omits the MoM trend and that the three specs "still
  describe the old six-task schedule verbatim ... and need a spec-sync pass" is
  corrected to reflect that the trend is restored and the specs are synced. The
  earlier `spending_trends` note (line ~237) is likewise corrected.

## Impact

- Specs only. No code, no migrations, no schema changes.
- `openspec/specs/finance-alerts/spec.md`: one requirement modified (one scenario
  replaced, one cross-reference bullet added).
- `roster/finance/AGENTS.md`: two stale notes corrected.
- No behavior change — the code already implements the described behavior
  (PR #3024). This change makes the spec and the agent notes tell the truth.

## Out of Scope

- **Editing `butler-finance` or `finance-crud-operations` specs.** They are
  already accurate for the current four-job architecture; rewording them would
  violate the meaning-preserving bar with no factual gain.
- **Removing the three now-dead `finance_jobs.py` functions**
  (`run_upcoming_bills_check`, `run_subscription_renewal_alerts`,
  `run_monthly_spending_summary`). That is a code change, out of scope for a
  spec-sync bead; the AGENTS.md note about them is left in place.
