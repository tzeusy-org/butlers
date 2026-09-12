## Why

`delivery_cycle()`'s quiet-hours/context-bus suppression handling (RFC 0011
Amendment 1, `openspec/specs/proactive-insight-engine/spec.md` "Quiet Hours
Suppression") leaves a fully suppressed routine cycle's pending candidates
"for the next non-quiet delivery cycle" — but the only thing that invokes
another cycle today is a regularly scheduled cron tick: the daily digest's
windowed cron (`roster/switchboard/butler.toml`'s `insight-delivery-cycle`,
06:15-11:45 UTC only) or, once that window closes for the day, tomorrow's.
A hold that starts near the end of that window, or outside it entirely (a
`dnd`/`meeting` signal firing mid-afternoon, say), gets no further check
until the next day's window opens — the suppressed candidates sit `pending`
for up to a day even after the suppressing condition itself has cleared.

This is Slice 3 of the bu-kqnum.3 "Park, never delete" epic (Slices 1-2 —
content-bearing deferred `notify()` envelopes, bu-kqnum.3.1/PR #3456; the
shared end-exclusive quiet-window predicate/config consolidation,
bu-s182c/PR #3459 — are already merged). Slice 3 is the epic's own
next-named move: "the insight broker's suppressed skip enqueues a catch-up
cycle at suppression end."

Evidence: `roster/switchboard/tools/insight/broker.py` `delivery_cycle`'s
suppressed-skip branches (the `daily_hold_mode and _suppression_signal ==
"traveling"` travel-day defer, and the general no-urgent-pending `else`)
both only `record_attention_event(outcome="suppressed", ...)` and
`return result` — no mechanism re-invokes the cycle before the next cron
tick. `roster/switchboard/butler.toml`'s `insight-delivery-cycle` schedule
comment documents the windowed-cron-only cadence this proposal supplements.

## What Changes

- **Broker catch-up reconciliation (new module):**
  `roster/switchboard/tools/insight/catchup.py` reconciles a single,
  deterministically-named (`insight-catchup`) one-shot `scheduled_tasks` row
  — `dispatch_mode="job"`, `job_name="insight_delivery_cycle"` (the existing
  production job handler, already wired with the real `notify_fn`) — timed
  to fire at a computed suppression-end instant. Mirrors the one-shot
  cron-pin + `until_at` auto-disable convention already used by
  `butlers.core.domain_event_wake`/`delegation_wake`, adapted to reschedule
  the same task in place (rather than reconcile by identity) since at most
  one suppression is active at a time.
- **Suppression-end computation:** `delivery_cycle`'s two fully-suppressed
  skip branches (the general no-urgent-pending `else`, and the
  `daily_hold_mode` travel-day defer) each call a new
  `_schedule_insight_catchup` helper, which computes the end instant —
  `policy_quiet_hours_deliver_at(policy, now=now)` for `quiet_hours` (already
  Slice 2 infrastructure), or the context-bus signal's own `set_at` plus its
  existing max-hold TTL (`dnd` 4h, `meeting` 2h, `sleeping` 10h, `traveling`
  6h) for a context-bus hold — and reconciles the catch-up task for it.
  `get_suppressing_context_signal` gains a sibling
  `_get_suppressing_context_signal_detail` that also returns the winning
  signal's `set_at`; the public, str-only function becomes a thin wrapper
  over it so every existing call site and test mock is unaffected.
- **Best-effort, fail-open:** `_schedule_insight_catchup` never raises — a
  scheduling hiccup logs a warning and the suppressed-cycle return proceeds
  exactly as it does today, matching this module's existing degraded-honesty
  convention for `record_attention_event`.
- **Self-correcting:** the catch-up job re-invokes the same
  `insight_delivery_cycle` job. If the suppression has genuinely cleared by
  its computed end, delivery proceeds normally; if a different (or renewed)
  suppression is active by then, the same suppressed-skip path fires again
  and reconciles the catch-up forward to the new end — no special-casing
  needed for a suppression that outlives its first computed boundary.
- **Out of scope:** urgent-priority-bypass cycles (never fully suppressed,
  so nothing to catch up on) and the daily_hold_mode hard-fallback-deadline
  bypass (already force-delivers within the window) are unaffected — no
  catch-up is reconciled on either path. Slices 4 (wake-evidence supersede +
  composed morning flush) and 5 (secrets_lifecycle single-park + cron
  re-anchor + always-suppressed lint) remain separately scoped on the parent
  epic.

## Impact

- Affected specs: `proactive-insight-engine` (new "Broker Catch-Up Cycle at
  Suppression End" requirement).
- Affected code: `roster/switchboard/tools/insight/broker.py` (new
  `_get_suppressing_context_signal_detail`, `_compute_catchup_deliver_at`,
  `_schedule_insight_catchup`; `get_suppressing_context_signal` becomes a
  thin wrapper); new `roster/switchboard/tools/insight/catchup.py`. No
  migration, no new table (`scheduled_tasks` is existing per-butler-schema
  infrastructure), no cross-butler schema change, no change to the daily
  cron/window configuration.
- Affected tests: `tests/modules/test_insight_catchup.py` (new — mocked-pool
  reconciliation unit tests), `tests/modules/test_insight_context_bus_suppression.py`
  (extended — `_get_suppressing_context_signal_detail` unit coverage,
  `_compute_catchup_deliver_at` boundary unit coverage),
  `tests/modules/test_insight_attention_ledger.py` (extended — new
  `TestBrokerCatchupCycle` integration coverage against the real suppression
  paths).
