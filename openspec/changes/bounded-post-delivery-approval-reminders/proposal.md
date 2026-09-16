## Why

RFC 0023 now durably recovers an approval presentation and distinguishes
provider-confirmed delivery from an ambiguous provider effect. It intentionally
does not prove that the owner read the request, and it permits a successor
presentation only after authenticated dashboard defer. The owner selected
Direction B on 2026-09-15: an independently bounded reminder may follow
provider-confirmed delivery when action-specific owner acknowledgement is still
absent.

The direction alone is not an executable policy. Exact acknowledgement
evidence, channels, ordering, timing, count, missing/stale evidence behavior,
safe hold, homecoming, and disengagement accounting must be specified before
implementation. The successor must preserve original expiry, RFC 0021 quiet
hours and burst control, and RFC 0023 ambiguous-effect no-resend.

## What Changes

- Add a proposed `approval-attention-reminders` capability with an explicit
  acknowledgement ledger and a 24-hour reachability-evidence freshness model.
- Allow at most two automatic reminder ordinals over an action's lifetime,
  due 4 hours and 24 hours after the current owner-requested presentation is
  provider-confirmed, and only on distinct eligible channels.
- Limit initial eligibility to the two channels registered end-to-end today:
  `telegram` and `email`. Resolve the owner's eligible preferred channel first,
  then use fixed `telegram`, `email` fallback order. WhatsApp and every future
  channel remain excluded pending a reviewed amendment.
- Keep policy reminders separate from RFC 0023 recovery. Safe retry keeps the
  same key and channel; ambiguous effect permits reconciliation only and blocks
  every fresh reminder or homecoming key.
- Define `safe_hold` as monotonic attention state, never `ActionStatus`. It does
  not alter approval status, arguments, expiry, decisions, or execution.
- Add one content-blind, bounded homecoming summary per owner-presence epoch,
  excluding ambiguous actions and never reviving expired actions.
- Preserve the actual proactive-insight denominator. Approval deliveries,
  reminders, safe holds, and homecoming summaries never write
  `insight_engagement` or the insight count fields in
  `attention_daily_rollup`.

## Capabilities

### New Capabilities

- `approval-attention-reminders`: Action-specific acknowledgement evidence,
  registered-channel selection, bounded policy reminder admission, safe hold,
  reachability truth, homecoming deduplication, concurrency/replay, and
  disengagement isolation.

### Modified Capabilities

None in this draft. The RFC 0023 change remains active and must be archived and
synced before implementation of this successor. Future implementation must then
reconcile the successor into the affected approval delivery, module approvals,
dashboard approvals, core notify, and insight-delivery baselines without
overwriting unrelated clauses.

## Authority and impact

Owner Direction B is settled. RFC 0023 adoption and its prior repository
implementation release are settled. This exact successor is not adopted yet.
It requires independent security and product review followed by owner adoption
of the reviewed artifact.

This change is specification-only. It performs no source implementation,
migration, notification/provider call, credential access, action decision,
deployment, runtime activation, rollout-flag change, OpenSpec archive, or live
effect. Tests: +0 ~0 -0.
