## Amendment status

The ten checked entries below record the ORIGINAL draft's completed specification
work, not acceptance of the goal-runtime conversion or completion of any runtime
work. Their checkbox states are retained as historical evidence. Goal-runtime
conversion acceptance is PENDING independent exact-head review and required gates.
The sixteen unchecked future entries stay unchecked; this spec-only amendment does
not activate, implement or archive their outcomes.

## 1. Historical draft evidence (checked states retained)

- [x] 1.1 Historical draft specified `chat_id`/`external_thread_id` affinity extraction.
  Target affinity retires; current conversation/reply identity follows design D1.
- [x] 1.2 Historical draft specified a Telegram 24h affinity TTL independent of email.
  Affinity TTL/reclassification retire; the 15-minute/30-message history bound survives.
- [x] 1.3 Historical draft specified unavailable-pinned-target classification fallback.
  No target pin or classification fallback survives in the goal runtime (design D3).
- [x] 1.4 Historical draft specified conflict-then-reclassify for concurrent turns.
  No affinity conflict or guessed goal join survives (design D4).
- [x] 1.5 Historical draft documented inherited Telegram ingest idempotency.
  Replay/dedup and canonical request lineage survive in goal admission (design D5).
- [x] 1.6 Historical draft extended `correct_route` and its MODIFIED delta.
  The entire Misroute Correction Re-dispatch contract now has an explicit REMOVED
  disposition matching generalist; this check is not a claim that it remains live.
- [x] 1.7 Historical draft specified bounded target-side context and its two carriers.
  Bounds survive in goal context; both classification/affinity carriers retire.
- [x] 1.8 Historical draft specified untrusted-data banner and per-message code fencing.
  Fencing survives together with per-item stamps and exposure (design D8).
- [x] 1.9 Historical draft specified no competing thread store and an email boundary.
  No new store is authorized; target-state email follows generalist (design D9).
- [x] 1.10 Historical draft recorded `bu-p6v8k` / `bu-d2ft5` sequencing.
  Sticky routing is obsolete at the switch; continuity survives under the goal runtime.
  No Beads lifecycle state is changed or inferred by this amendment.

## 2. Review and separate future authorities

- [ ] 2.1 Obtain independent exact-head semantic review of this conformance amendment.
- [ ] 2.2 Record any separately required future implementation/adoption authority.
  Adopted-RFC-conforming text needs no owner exact-text sign-off; this entry creates
  no such gate for the present canonical spec-only amendment.
- [ ] 2.3 Keep implementation, migrations, credentials and live Telegram calls subject
  to their own separate authority; this amended packet performs none of them.

## 3. Future implementation after separate authorization

- [ ] 3.1 Use the existing `external_conversation_id` and separate reply-target contract
  for continuity; introduce no target-affinity cache, TTL setting or identity table.
- [ ] 3.2 Carry bounded conversation continuity into deterministic goal admission and
  context, with no classification, affinity bypass or route dispatch.
- [ ] 3.3 Ensure no misroute re-dispatch/re-pinning or replacement domain session is
  reintroduced; use only the existing goal/new-goal/Stop and capability contracts.
- [ ] 3.4 Carry the deterministic bounded/fenced history with item stamps and sender
  provenance under existing goal-context/read boundaries, never a domain prompt.
- [ ] 3.5 Retain accepted message/request lineage and exact owner-reply binding rules;
  same-chat history neither authenticates the sender nor selects/joins a goal.

## 4. Future verification after separate authorization

- [ ] 4.1 Verify current conversation identity, Telegram forum-topic isolation and
  distinct reply targets, without reintroducing `event.external_thread_id`.
- [ ] 4.2 Verify new/follow-up/concurrent/unavailable-domain cases all obey ordinary
  admission, without sticky targets, TTL reclassification or guessed goal joins.
- [ ] 4.3 Verify correction cannot re-dispatch a message or launch a domain session;
  original request/sender evidence remains in its actual admission contract.
- [ ] 4.4 Verify canonical Telegram replay and crash-safe ingest acceptance resolve
  duplicate envelopes to the existing goal with no second admission or affinity write.
- [ ] 4.5 Verify the 15-minute/30-message bound, fences, per-item stamps and monotonic
  exposure for third-party, forwarded/quoted, tainted or unreadably stamped context.
- [ ] 4.6 Verify no competing thread store, cross-chat/topic leakage, new grant or
  unintended live email behavior change; target-state email follows generalist.
- [ ] 4.7 Run the separately authorized implementation's named affected tests, guards,
  strict OpenSpec, hygiene, independent exact-head review and terminal hosted CI.
  Report that implementation's actual test delta separately; this prose delta is zero.

## 5. Future archive after separate authority

- [ ] 5.1 After separately approved implementation/release and archive prerequisites
  are satisfied, reconcile the adopted deltas with generalist tasks.md 4.2 and archive
  under the actual release authority. No current sync/archive/deploy is claimed here.
