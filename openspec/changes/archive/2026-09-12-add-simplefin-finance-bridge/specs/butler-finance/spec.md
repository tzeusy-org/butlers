## MODIFIED Requirements

### Requirement: Finance Butler Schedules
The finance butler SHALL run bill/anomaly/budget/subscription intelligence as deterministic jobs that propose insight candidates through the switchboard insight broker, rather than prompt-mode tasks that notify directly, and SHALL run the deterministic SimpleFIN bridge as a separate ledger-sync job.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it SHALL execute four `dispatch_mode = "job"` schedules for intelligence-driven alerts: `insight-scan` (`0 7 * * *`), `bill-reconciliation-sweep` (`15 21 * * 0`), `anomaly-insight-scan` (`0 21 * * *`), and `monthly-finance-digest` (`0 9 1 * *`)
- **AND** each intelligence job SHALL propose candidates via `propose_insight_candidate()` for the switchboard's insight broker to dedup/cooldown/budget/deliver, rather than calling `notify()` directly
- **AND** the finance butler SHALL additionally execute the daily off-top-of-hour `simplefin-sync` (`17 4 * * *`) task with `dispatch_mode = "job"` and `job_name = "simplefin_sync"`
- **AND** `simplefin-sync` SHALL deterministically synchronize its Finance-owned ledger without calling `propose_insight_candidate()`, `notify()`, Switchboard routing, or an LLM runtime
- **AND** this replaced six prior prompt-mode tasks that called `notify()` directly — `upcoming-bills-check` (15 21 * * 0), `subscription-renewal-alerts` (20 21 * * 0), `monthly-spending-summary` (0 9 1 * *), `anomaly-digest` (0 21 * * *), `budget-status-check` (0 9 * * 1), and `subscription-audit-monthly` (0 10 1 * *) — via the bu-rvz2o migration (PR #2991, merged 678a29596); see `finance-alerts/spec.md` "Alert Scheduled Task Definitions" for the full old-task -> new-job mapping and dedup-key/priority details
- **AND** the finance butler additionally runs `daily_briefing_contribution` (`55 6 * * *`) and `calendar_overlay_contribution` (`50 6 * * *`), which are unrelated to the bu-rvz2o migration (pre-existing cross-butler briefing/calendar contributions, not direct-notify alert tasks)
