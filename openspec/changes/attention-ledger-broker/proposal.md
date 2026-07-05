# One Attention Ledger: All Proactive Owner Egress Through the Broker

## Why

Two independent quiet-hours gates already sit in front of the owner
(`public.approvals_policy` at the `notify()` owner-default path, and
`public.insight_settings` in the daily insight-delivery-cycle), plus a
context bus (`public.user_context`, RFC 0009) that already carries
authoritative `dnd`/`sleeping` signals. Three gaps made all of that
structural anti-spam machinery partially decorative:

1. **Quiet hours defaulted OPEN with zero owner setup.** Both singleton
   policy rows ship with `quiet_start_hour`/`quiet_end_hour` (resp.
   `quiet_start`/`quiet_end`) NULL — "always send" — and nothing seeds a
   sane default. A single-owner deployment (Asia/Singapore) never got quiet
   hours unless the owner manually configured two separate tables.
2. **The context bus was read by exactly one consumer** — `spawner_context.py`,
   for LLM system-prompt framing. Neither the `notify()` gate nor the
   insight-delivery-cycle ever checked `dnd`/`sleeping` before sending, so a
   butler could still page the owner mid-`dnd`.
3. **Suppressed/deferred notifications left no trace.** `notify()`'s
   quiet-hours suppression returns a status to the calling LLM runtime and
   nothing else — no durable record survives past that one tool call. A
   silently-dropped notification and a delivered one were indistinguishable
   after the fact — the exact failure mode the degraded-honesty doctrine
   (aggregation endpoints, `SourceDegradedNote`) already forbids elsewhere in
   this codebase, just not yet applied to notifications.
4. **Quiet hours had no urgent escape valve.** A priority-90+ (RFC 0011
   "time-critical") insight candidate was suppressed by the same blanket
   quiet-hours check as a background nudge — there was no distinction
   between "budgeted, deferrable" and "fail open, always deliver."

This is move 8 of the 2026-07-04 JARVIS pursuit (`docs/redesigns/2026-07-04-jarvis-pursuit.md`
§Ranked moves #8) — slices 1-2 of 5. It amends RFC 0011 (Proactive Insight
Delivery Protocol) rather than replacing it: the insight broker's
budget/dedup/cooldown/adaptive-ratchet machinery is unchanged; this change
adds the ledger, the seeded defaults, the context-bus consult, and the
priority-urgent bypass on top of it.

## What Changes

- **New `public.attention_ledger` table** (migration `core_160`): one durable
  row per proactive-egress decision at either choke point (`notify()` and
  `delivery_cycle()`), with a closed outcome vocabulary — `delivered`,
  `coalesced` (folded into a digest), `deferred`, `suppressed` — plus a
  machine-readable `reason`, `priority_label`/`priority_score` (see
  normalization below), `dedup_key`, and `notification_ref` for downstream
  correlation. This is additive observability — no existing table's shape or
  semantics changes.
- **Seeded owner-level quiet hours.** `core_160` seeds `public.approvals_policy`
  and `public.insight_settings` with 23:00-08:00 Asia/Singapore **only when
  currently unconfigured** (`WHERE quiet_start_hour IS NULL AND
  quiet_end_hour IS NULL`, and the `insight_settings` equivalent) — idempotent,
  never overwrites an owner's own configuration.
- **Context-bus gating at both choke points.** `notify()`'s owner-default
  path and `delivery_cycle()`'s quiet-hours check now also consult
  `public.user_context` for an active `dnd`/`sleeping` signal
  (`get_suppressing_context_signal()`), deterministically — no LLM in the
  read path. This is additive to the existing hour-based gates, not a
  replacement.
- **Priority-urgent bypass (fail-open for urgent, budgeted for routine).** A
  candidate/notification at or above `URGENT_PRIORITY_THRESHOLD` (90, RFC
  0011's "time-critical" floor) is never suppressed by quiet hours or the
  context bus. In `delivery_cycle()`, when at least one urgent candidate is
  pending during a would-be-suppressed cycle, the cycle proceeds for urgent
  candidates only — routine candidates stay `pending` for a later,
  non-suppressed cycle rather than being delivered early or silently
  dropped. `notify()`'s existing `priority="high"` already bypassed quiet
  hours; this change adds the same bypass for the context-bus check and
  normalizes `high`/`medium`/`low` onto the same 1-100 scale for ledger
  comparability (`high` pins to the urgent floor, 90).
- **Priority normalization for cross-path comparability.** `notify()` uses a
  3-level enum (`high`/`medium`/`low`); the insight pipeline uses 1-100.
  `normalize_priority()` maps both onto one `priority_score` so the ledger
  is queryable across both paths without per-path special-casing.

Non-goals (explicitly deferred to later slices/follow-ups, not half-implemented
here): same-window coalescing of multiple notify()-path sends into one
composed message (slice 4), an hourly urgent sub-cycle (slice 4), converting
finance's direct-notify prompt-cron tasks to insight candidates (slice 3),
and the dashboard attention-ledger panel (slice 5). No changes to identity
resolution (`relationship.entity_facts`) or to the insight broker's
budget/dedup/cooldown/adaptive-ratchet logic.

## Capabilities

### Modified Capabilities

- `core-notify`: the `notify()` owner-default quiet-hours gate additionally
  consults the context bus, and every suppressed/deferred/delivered decision
  at this boundary is recorded to the attention ledger.
- `proactive-insight-engine`: quiet-hours suppression gains a priority-urgent
  bypass and a context-bus consult; delivered/coalesced candidates are
  recorded to the attention ledger; both singleton quiet-hours policies gain
  seeded defaults.

## Impact

- **DB**: new migration `core_160_attention_ledger.py` (core chain) —
  `public.attention_ledger` table + grants, plus guarded data-only seeds for
  `public.approvals_policy` and `public.insight_settings`.
- **Backend**: new `src/butlers/core/attention_ledger.py` (writer + reader +
  context-bus consult + priority normalization, no notify()/insight-broker
  import — usable from either side without a circular-import risk); wiring
  in `src/butlers/core_tools/_notifications.py` (`notify()`) and
  `roster/switchboard/tools/insight/broker.py` (`delivery_cycle()`).
- **No frontend changes** in this slice (dashboard panel is slice 5, RFC
  0011 amendment already documents the ledger schema for that future work).
- **RFC 0011**: Amendment 1 documents the ledger schema, the seeded-defaults
  policy, the context-bus integration (previously noted as "optional,
  deferred to a follow-up" in RFC 0011's Integration section — this change
  is that follow-up), and the priority-urgent bypass semantics.
