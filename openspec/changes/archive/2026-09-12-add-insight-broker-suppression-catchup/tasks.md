## 1. Suppression-end computation

- [x] 1.1 `_get_suppressing_context_signal_detail(pool, *, now=None)` returns
      `(signal_type, set_at)` for the winning context-bus signal;
      `get_suppressing_context_signal` becomes a thin wrapper over it, so
      every existing call site and mock is unaffected.
- [x] 1.2 `_compute_catchup_deliver_at` — pure function: `policy_quiet_hours_deliver_at`
      for `quiet_hours`, `set_at + max_hold` for a context-bus signal, `None`
      (fail open) otherwise.
- [x] 1.3 Unit tests (no Docker): boundary computation per suppression type,
      no-usable-policy/no-set-at/unknown-signal fail-open paths.

## 2. Catch-up task reconciliation

- [x] 2.1 New `roster/switchboard/tools/insight/catchup.py`:
      `reconcile_catchup_task(pool, *, deliver_at, reason)` — deterministic
      one-shot cron-pin (`schedule_create`/`schedule_update`, mirroring
      `domain_event_wake`'s one-shot convention), reschedules the same
      `insight-catchup` task in place rather than duplicating, handles the
      create-race `ValueError` by re-reading.
- [x] 2.2 Unit tests (mocked pool, no Docker): create-when-absent,
      already-scheduled-is-a-no-op (tolerance window), materially-different-
      target reschedules, disabled/already-fired task re-enables, create-race
      reconciliation.

## 3. Wiring into delivery_cycle

- [x] 3.1 `_schedule_insight_catchup` helper: resolves the suppression's
      `deliver_at` and calls `reconcile_catchup_task`, catching and logging
      any exception (fail-open — never aborts the suppressed-cycle return).
- [x] 3.2 Call it from both fully-suppressed skip branches: the general
      no-urgent-pending `else`, and the `daily_hold_mode` travel-day defer.
      Not called from the urgent-bypass or hard-fallback-deadline-bypass
      paths (never fully suppressed — nothing to catch up on).
- [x] 3.3 Integration tests (Docker, real suppression paths against
      `insight_pool`): quiet-hours suppression schedules a catch-up at the
      policy's end boundary; a context-bus signal schedules one at its
      max-hold end; the travel-day defer also schedules one; an urgent-bypass
      cycle does not; a `reconcile_catchup_task` failure does not abort the
      suppressed-cycle return or propagate; no pending candidates schedules
      nothing.

## 4. Contract and verification

- [x] 4.1 Add the `proactive-insight-engine` spec delta (new "Broker
      Catch-Up Cycle at Suppression End" requirement).
- [x] 4.2 Run `openspec validate --strict` on the changed spec.
- [x] 4.3 Run backend lint and the affected insight/scheduler test files.
