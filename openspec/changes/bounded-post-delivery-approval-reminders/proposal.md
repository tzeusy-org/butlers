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
  acknowledgement ledger and a 24-hour reachability-evidence freshness model
  whose observational channel inventory is separate from episode-local
  reminder eligibility.
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
- Preserve the one-digest burst shape while requiring the digest channel to be
  eligible and unused for every admitted member; incompatible members hold
  without joining, consuming an ordinal, sending directly, or creating another
  digest.
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
implementation release are settled. On 2026-10-27, the owner adopted Choice A
of the independently reviewed conformance resolution (`bu-4zqomm`). This
amendment applies the exact approved specification, RFC 0035, and design
artifacts. Their retained draft metadata is historical; this record establishes
specification adoption, with implementation review and activation authority
still separate.

This change is specification-only. It performs no source implementation,
migration, notification/provider call, credential access, action decision,
deployment, runtime activation, rollout-flag change, OpenSpec archive, or live
effect. Tests: +0 ~0 -0.

## Review record and open owner questions

Independent security and product review (bu-8cdl1.23) covered commit
`a09fb3ce28437aa5bd5ac8013b3cdff1e0d419e2`; all findings S1-S7 and P1-P7 are
resolved in this change. The post-resolution text was independently re-reviewed
at the exact head of the resolution PR (second pass bu-ijsbde; the exact SHA is
in the bead and the PR, since a commit cannot name itself). That review remains
provenance for the later independently reviewed Choice A conformance successor
adopted on 2026-10-27 as recorded above.

Owner questions (not adopted here):

1. Should a reminder ever carry one-tap approve/reject/defer controls? This
   draft proposes link-only, content-blind reminders because a reminder lands on
   a different channel than the original and email is a weaker trust surface.
