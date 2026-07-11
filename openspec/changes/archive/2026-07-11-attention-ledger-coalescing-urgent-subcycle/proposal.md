# Same-Window Coalescing + Hourly Urgent Sub-Cycle

## Why

`attention-ledger-broker` (bu-qvnce.8, slices 1-2, merged via PR #2944)
deliberately deferred two pieces of move 8 rather than half-implement them:

1. **Same-window coalescing.** `notify()`'s per-butler quiet-hours batching
   (`{schema}.delivery_preferences`/`deferred_notifications`, RFC:
   `openspec/specs/time-aware-delivery/spec.md`) already lets a butler defer
   medium/low-priority sends to one daily `batch_delivery_time`. But the flush
   pass (`_tick_deferred_notification_pass`) delivered every due row with its
   own `notify_fn` call — a butler that deferred 5 notifications to the same
   batch window still produced 5 separate Telegram pings at flush time, not
   one. The anti-spam intent of `batch_low_priority` was structurally present
   but not actually honoured at the one place it mattered.
2. **Hourly urgent sub-cycle.** The insight-delivery-cycle's existing
   priority-urgent bypass (RFC 0011 Amendment 1) only ever affects
   quiet-hours/context-bus *suppression* — it does not change the cycle's
   *cadence*. With one daily cron slot (08:00 UTC), a priority>=90 candidate
   proposed at 08:05 could sit `pending` for nearly 24h before the next
   opportunity to fire, contradicting "priority>=90 means hours, not one
   daily slot" (2026-07-04 JARVIS pursuit, move 8).

This change (bu-o8233, JARVIS pursuit move 8 slice 4) implements both,
without introducing a new egress path — all delivery still routes through the
same two existing choke points (`notify()`'s deferred-notification flush and
`delivery_cycle()`), consistent with the broker doctrine established in
`attention-ledger-broker`.

## What Changes

- **`_tick_deferred_notification_pass` composes same-target due notifications
  into one message.** Due rows (`status='pending' AND deliver_at <= now`) are
  grouped by delivery target (`channel`, `recipient` — a `None` recipient,
  i.e. "resolve the owner's default channel", is its own group, never merged
  with an explicit recipient). A group of exactly one row is delivered
  unchanged (its stored envelope, verbatim) — solo-item behaviour and ledger
  semantics are unaffected. A group of >1 rows is composed into one digest
  envelope (mirrors the insight broker's `_format_digest` style) and
  delivered via a single `notify_fn` call; all rows in the group are marked
  `delivered` together, or all stay `pending` together on failure (no partial
  delivery within a coalesced group).
- **Flush-time ledger recording, extended to the coalesced outcome.** The
  flush pass now calls `record_attention_event(source="notify", ...)` on
  every successful send — `outcome="delivered"` for a solo row,
  `outcome="coalesced"` (one row per underlying notification) for a composed
  digest. This is the same outcome vocabulary the insight engine already uses
  for its own digests (`public.attention_ledger.outcome` already permits
  `'coalesced'` for any `source` — no CHECK-constraint or migration change
  needed).
- **`delivery_cycle(urgent_only=True)`.** A new opt-in mode on the existing
  insight delivery pipeline: candidate selection narrows to
  `priority >= URGENT_PRIORITY_THRESHOLD` (90) from the start; the
  quiet-hours/context-bus consult is skipped outright (urgent always bypasses
  both, so querying them would be pure overhead); the daily adaptive budget
  cap does not apply (every eligible urgent candidate delivers this cycle,
  not just the top-B); end-of-cycle maintenance (`cleanup_old_rows`,
  disengagement auto-off) is skipped (daily-cadence concerns the regular
  cycle already covers). The existing `verbosity=off` opt-out still applies
  unchanged — it is a hard user preference, not a time-based deferral the
  urgent bypass is meant to override.
- **New hourly schedule.** `roster/switchboard/butler.toml` gains
  `insight-urgent-subcycle` (`30 * * * *`, `job_name=insight_urgent_subcycle`),
  wired in `src/butlers/scheduled_jobs.py` to
  `delivery_cycle(pool, notify_fn=..., urgent_only=True)` using the exact same
  production `notify_fn` factory as the daily cycle.
- **Idempotency.** No new bookkeeping needed for either mechanism: a
  delivered `insight_candidates`/`deferred_notifications` row transitions to
  `status='delivered'` as part of its own successful-delivery step, and every
  due-fetch query in both pipelines filters `WHERE status = 'pending'` — the
  row's own status is the guard against double-send, exactly as it already
  was pre-slice-4.

Non-goals (unchanged from `attention-ledger-broker`'s deferred list, still
not part of this change): converting finance's direct-notify prompt-cron
tasks to insight candidates (slice 3); the dashboard attention-ledger panel
(slice 5).

## Capabilities

### Modified Capabilities

- `core-notify`: the deferred-notification flush composes multiple due
  same-target sends into one message instead of one ping per row, and
  records the flush outcome (`delivered`/`coalesced`) to the attention
  ledger — previously the flush recorded nothing.
- `proactive-insight-engine`: `delivery_cycle()` gains an `urgent_only` mode
  used by a new hourly schedule, so a priority>=90 candidate is delivered
  within the hour rather than waiting for the next daily cycle.

## Impact

- **DB**: none. `public.attention_ledger.outcome` already permits
  `'coalesced'` for any `source` (`core_160`'s CHECK constraint is not
  scoped by source) — verified against a real migrated Postgres instance in
  `tests/integration/test_attention_ledger_roundtrip.py`.
- **Backend**: `src/butlers/core/scheduler.py`
  (`_tick_deferred_notification_pass`, `_group_due_deferred_notifications`,
  `_format_deferred_digest`); `roster/switchboard/tools/insight/broker.py`
  (`delivery_cycle`'s `urgent_only` parameter); `src/butlers/scheduled_jobs.py`
  (`_run_switchboard_insight_urgent_subcycle_job` + registry entry);
  `roster/switchboard/butler.toml` (new hourly schedule).
- **No frontend changes.**
