# Perception first-switch checklist

The dark source for bu-dhloi1.21 is present. This checklist records the remaining
activation obligations owned by bu-enbrs2; it does not authorize the switch or
close that milestone. Owner release bu-276pli permits scheduling only.

Governing contracts: [RFC 0038 S3/S5](../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md),
[perception requirements 001-003](../../openspec/changes/generalist-lead-focused-delegation/specs/perception-registry/spec.md),
and [the dashboard /now requirement](../../openspec/specs/dashboard-api/spec.md#requirement-owner-now-situation-card-dark).
The [implementation guide](perception.md) and [consumer census](user-context-census.md)
provide the detailed interfaces and retirement mapping.

## Source and acceptance boundary

| Acceptance criterion | Dark source present | Evidence still needed |
| --- | --- | --- |
| Owner-only deterministic /now and four-state reads | `perception.situation`, `perception.now`, `/api/now`; fixed availability-only projection | Actual principal-derived audience, reader membership isolation, owner authentication and denied exposed/timeline access |
| Clock transitions, undo and DST/jump dedupe | `perception.clock` reducer, local-date brief key and time-basis parity | Durable state, notices/undo, atomic key persistence and scheduler restart/concurrent-delivery proof |
| Owner modes, defaults and step-up | Owner-mode API, entry/overlay writers, mode projection and recorded sick/absence/wake rules | Actual consumer behavior, real step-up transaction and RLS evidence; owner-delivery policy blockers resolved |
| Legacy context retirement | Bidirectional census and content-blind shadow comparator | Named successor/deferred owner for every reference, durable agreeing receipts and DND storage migration |
| Feeder replay, retention and retraction | Canonical .9/.17 identity, immutable history and dark 400-day place purge | Real-role replay/concurrency/retention proof, configured retention job and separately owned confirmed routine feed |

Pure reducers, lexical census checks and test pointers do not establish runtime
activation. Keep point-in-time commit inventories and test receipts in the external
worker/review report; collect fresh evidence against the actual switch tree.

## Activation gates

1. Record exact owner authority for the switch and its rollback. Keep all perception consumer
   flags off until that authority and each consumer's prerequisites are satisfied.
2. Derive situation audience from the server-held principal, never model arguments. Use
   `SET LOCAL ROLE` in a transaction. Availability runtimes must not also hold owner-reader
   membership. Verify the actual owner entity, reader DSN and denied exposed/timeline access.
3. Provision the separately authorized producer identities/profiles: `home.dots` and its place
   source refs, `context.clock` evidence sources/refs and finite freshness, `context.mode`
   owner/evidence/freshness, and named activity/suppression adapters. Missing evidence stays
   unknown. Confirmed routine feeding remains bu-xxfkhk's interface, not a private substitute.
4. Wire the clock reducer, owner notice/undo and brief scheduler with durable state and atomic
   local-date dedupe. Test restart, concurrent ticks, missed ticks and DST/east/west jumps at
   those production seams. Preserve source expiry during conversion.
5. Run mode mutations on the dashboard table-owner connection; keep step-up spend and write in
   one transaction. Grant the context projector only its entry read and schedule projection
   before freshness expires. Prove sw_078 RLS returns seeded active entries, not empty rows.
   Runtime readers must not add row locks without an appropriate UPDATE policy (including
   `WITH CHECK (false)`); otherwise RLS can silently hide rows and trigger default profiles.
6. Before enabling owner-delivery mode holds, resolve #6001's recorded activation limits:
   per-butler workers need a permitted overlay read (sw_078 only names Switchboard), and
   break-through senders versus urgency reason codes need an adopted vocabulary. Also record
   the unknown-posture policy. Held approvals retry until action expiry without a push, leaving
   the dashboard fallback. These issues are not evidence that mode holds are safe to enable.
7. Collect durable, content-blind shadow/parity receipts for each census reader and writer.
   Include late/retracted/out-of-page history, stale evidence and room/place mapping limits.
   Move the canonical DND storage before retiring `public.user_context`; an empty search or
   passing lexical census is insufficient.
8. Re-run migrated-PG situation/clock/presence, entry/overlay/step-up, projection, parity and
   retention suites plus affected scheduler/API/roster checks on the switch tree. Require the
   protected hosted `merge_group` gate and independent review of the actual change.
9. Provision the separately authorized `perception_retention` identity/connection and schedule
   `jobs.retention.prune_place_transitions` before enabling `PLACE_RETENTION_LIVE`. Prove
   400-day target/retraction/receipt behavior, preserve other kinds and raw-fix retention, and
   collect authorized runtime receipts. This reconciliation performs no live deletion.
10. Rollback holds the new consumer/reducer, retaining canonical events, targets, receipts and
    committed effects. Never restore competing clock/presence truth or broaden authority.
