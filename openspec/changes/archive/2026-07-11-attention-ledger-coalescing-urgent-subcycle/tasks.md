# Tasks — attention-ledger-coalescing-urgent-subcycle (bu-o8233, slice 4)

## 1. Same-window coalescing at the notify() boundary (spec: core-notify)

- [x] 1.1 `_group_due_deferred_notifications()`: group due rows by
  (channel, recipient); a `None` recipient is its own group (never merged
  with an explicit recipient).
- [x] 1.2 `_format_deferred_digest()`: compose >1 same-target rows into one
  `notify.v1` envelope (digest-style message body, mirrors the insight
  broker's `_format_digest`).
- [x] 1.3 `_tick_deferred_notification_pass()`: solo-row groups delivered
  unchanged (verbatim envelope); multi-row groups delivered via one composed
  `notify_fn` call; a group's rows are marked `delivered` together on
  success, left `pending` together on failure (no partial delivery).
- [x] 1.4 Ledger recording added to the flush pass (previously absent):
  `outcome="delivered"` for a solo send, `outcome="coalesced"` (one row per
  underlying notification) for a composed digest.
- [x] 1.5 Tests: `tests/core/test_temporal_intelligence.py` (N due
  same-target notifications -> 1 composed send + all marked delivered;
  different recipients never coalesced; failed composed send keeps the
  whole group pending) and
  `tests/integration/test_attention_ledger_roundtrip.py`
  (`record_attention_event(source="notify", outcome="coalesced", ...)`
  round-trips against the real CHECK constraint).

## 2. Hourly urgent sub-cycle (spec: proactive-insight-engine)

- [x] 2.1 `delivery_cycle(..., urgent_only: bool = False)`: candidate
  selection narrows to `priority >= URGENT_PRIORITY_THRESHOLD` from the
  start; quiet-hours/context-bus consult skipped outright; no daily budget
  cap; end-of-cycle maintenance (`cleanup_old_rows`,
  `check_total_disengagement_auto_off`) skipped; `verbosity=off` still
  applies unchanged.
- [x] 2.2 `_run_switchboard_insight_urgent_subcycle_job`: wires the same
  production `notify_fn` factory as the daily job, with `urgent_only=True`.
- [x] 2.3 New schedule: `roster/switchboard/butler.toml`
  `insight-urgent-subcycle` (`30 * * * *`, `job_name=insight_urgent_subcycle`).
- [x] 2.4 Tests: `tests/modules/test_insight_attention_ledger.py`
  (`TestUrgentOnlySubCycle` — urgent selected/routine untouched, quiet-hours
  bypassed without querying the context bus, no daily budget cap delivers
  all eligible urgent as one digest, `verbosity=off` still respected, a
  no-urgent-pending cycle is a cheap no-op, a later daily cycle never
  re-delivers an already-urgent-delivered candidate) and
  `tests/jobs/test_insight_delivery_job.py`
  (`TestSwitchboardInsightUrgentSubcycleJobWiring` — job passes
  `urgent_only=True` and a non-None `notify_fn`, shares the daily job's
  `notify_fn` factory).

## 3. Close-out

- [x] 3.1 `openspec validate attention-ledger-coalescing-urgent-subcycle --strict`
- [x] 3.2 Archive on merge; update `core-notify` / `proactive-insight-engine`
  main specs. (Note: `attention-ledger-broker`'s own slices 1-2 main-spec
  merge, task 6.2 there, is still pending as of this change and is out of
  scope here — it belongs to bu-qvnce.8, not bu-o8233.)

## Deferred (unchanged from attention-ledger-broker, still not part of this change)

- Slice 3: convert finance's direct-notify prompt-cron tasks to insight
  candidates.
- Slice 5: dashboard attention-ledger panel under Trust Console.
