## 1. Author the delta

- [x] 1.1 Verify the shipped MoM behavior against `roster/finance/jobs/finance_jobs.py` (`_month_over_month_trend`, `run_monthly_finance_digest`) and the trend constants (`_MONTHLY_TREND_SWING_PCT=20`, `_MONTHLY_TREND_MAX_NOTABLE=5`)
- [x] 1.2 Write a `## MODIFIED Requirements` delta for `finance-alerts` "Automated Periodic Summaries": replace the pending-decision MoM scenario with the shipped scenario; carry forward the other three scenarios unchanged; add a cross-reference bullet to "Monthly finance digest"

## 2. Re-verify sibling specs (bu-vkyps)

- [x] 2.1 Confirm `butler-finance` and `finance-crud-operations` already describe the current four-job architecture (old-task names appear only in historical migration notes) — no edit needed
- [x] 2.2 Correct the stale `roster/finance/AGENTS.md` notes (MoM-omitted claim + "specs need a spec-sync pass" flag + the `spending_trends` note)

## 3. Validate and archive

- [x] 3.1 `openspec validate finance-alerts-restore-mom-trend --strict` green
- [x] 3.2 `openspec archive finance-alerts-restore-mom-trend -y` (merge the delta into the canonical spec)
- [x] 3.3 `openspec validate finance-alerts --strict` green
