## ADDED Requirements

### Requirement: Broker Catch-Up Cycle at Suppression End
When the delivery cycle fully suppresses a routine (non-urgent) cycle —
no candidate at or above `URGENT_PRIORITY_THRESHOLD` pending, and the cycle
returns `skipped=True` with an `outcome="suppressed"` attention-ledger row —
it SHALL reconcile a deterministic one-shot scheduled task that re-invokes
the delivery cycle at the suppression's own computed end instant, instead of
relying solely on the next regularly scheduled cron tick. The suppression
end SHALL be computed as: the Owner Attention Policy's end-exclusive quiet
window boundary when the active suppression is `quiet_hours`; or the
suppressing context-bus signal's `set_at` plus that signal's own max-hold TTL
(per "Context-Bus Gating of the Delivery Cycle") when the active suppression
is a context-bus signal. Reconciliation SHALL be idempotent, keyed by a
single deterministic task identity so a suppressed cycle re-run before the
catch-up fires reschedules the existing task to a materially different
target rather than duplicating it, and best-effort/fail-open: a scheduling
failure SHALL NOT abort or alter the suppressed cycle's return.

A cycle that delivers at least one urgent candidate this tick (the
Priority-Urgent Bypass) was never fully suppressed and SHALL NOT reconcile a
catch-up task. A `daily_hold_mode` cycle that bypasses suppression via the
hard fallback deadline delivers this tick and SHALL NOT reconcile a catch-up
task either. A `daily_hold_mode` cycle that defers on a travel day
(`reason="travel_day_defer"`) IS a fully suppressed skip and SHALL reconcile
a catch-up task for `traveling`'s own max-hold end, exactly like any other
suppressed skip.

#### Scenario: Quiet-hours suppression schedules a catch-up at the policy's end boundary
- **WHEN** the delivery cycle is fully suppressed by the Owner Attention
  Policy quiet-hours window, with no urgent candidate pending
- **THEN** a one-shot catch-up task is reconciled for the exact instant the
  quiet window ends in the policy's configured timezone

#### Scenario: A context-bus signal schedules a catch-up at its max-hold end
- **WHEN** the delivery cycle is fully suppressed by an active context-bus
  signal (`dnd`, `meeting`, `sleeping`, or `traveling`), with no urgent
  candidate pending
- **THEN** a one-shot catch-up task is reconciled for that signal's `set_at`
  plus its own max-hold TTL

#### Scenario: A travel-day defer also schedules a catch-up
- **WHEN** `daily_hold_mode=True`, the active suppressing signal is
  `traveling`, and no urgent candidate is pending
- **THEN** the cycle still defers with `reason="travel_day_defer"` (per
  "Hold-Until-First-Active Daily Digest Cadence") AND a one-shot catch-up
  task is reconciled for `traveling`'s max-hold end, even though the hard
  fallback deadline never force-delivers this cycle

#### Scenario: An urgent-bypass cycle does not schedule a catch-up
- **WHEN** the delivery cycle delivers at least one urgent (priority >=
  `URGENT_PRIORITY_THRESHOLD`) candidate this tick, whether or not a
  suppression signal is also active
- **THEN** no catch-up task is reconciled — the cycle was not fully
  suppressed

#### Scenario: Repeated suppression before the catch-up fires reschedules rather than duplicates
- **WHEN** a suppressed cycle reconciles a catch-up task for one computed end
  instant, and a later suppressed cycle (before that task has fired)
  computes a materially different end instant for the same or a different
  active suppression
- **THEN** the existing catch-up task is rescheduled to the new instant
  rather than a second task being created

#### Scenario: Scheduling failure does not abort the suppressed cycle
- **WHEN** reconciling the catch-up task raises an error (e.g. the scheduler
  is unavailable)
- **THEN** the delivery cycle still returns `skipped=True` with its
  suppressed-outcome ledger row intact, exactly as if catch-up reconciliation
  had not been attempted
