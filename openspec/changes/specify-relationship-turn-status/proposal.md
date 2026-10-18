## Why

The owner wants a weekly "people" view they can believe, and part of it is whose turn it is with
each person (bu-h40h2b.14, run-13 move 14). Today that fact cannot be stated honestly.

- Standing is reciprocity-gated and direction-weighted, but the direction evidence is thin.
  `interaction_sync` reads `switchboard.message_inbox` for inbound rows and writes an `outgoing`
  fact only when the owner also spoke in the same chat on the same day. Email has no owner-side
  evidence, so an email-only friend can never show an owner reply and sits at tier 1500.
- The premise that Gmail never sees SENT is partly stale, but not in the way that helps. The Gmail
  connector does list `in:sent` for 7 days, yet it reads only the `Message-ID` header into an in-memory
  set used for reply-policy tiering (`gmail.py` `_fetch_sent_message_ids`). It holds no recipient, date
  or thread, so it cannot say whose turn it is. The WhatsApp and Telegram user clients do see the
  owner's own messages (`is_from_me`), and `owner_outbound_events` records them but deliberately
  without counterpart identity.
- Stale-contact cadence already has a fail-closed `unmeasurable` state (REQ-butler-relationship-001).
  It conflates "no source is tracked" with "cannot see". The move asks for a three-way taxonomy
  without renaming `unmeasurable`.

## What Changes

### Added Capabilities

- `relationship-turn-status`: `whose_turn` (`owner`, `other`, `unknown`) as a derived, unstored,
  entity-keyed read projection with a per-direction availability taxonomy (`observed`, `not_tracked`,
  `no_signal`, `unavailable`); the eligible and excluded evidence; ordering and the evidence window;
  identity merge, unresolved identity and retraction; the rule that a turn status grants no outreach
  authority; privacy and retention; and an explicit out-of-scope guard.
- `connector-owner-direction-evidence`: the connector half. Only the user-client batch envelope's
  `owner_sender_id` is outbound-direction evidence; the Gmail `in:sent` set stays Message-ID-only
  metadata for reply policy; `owner_outbound_events` stays counterpart-less and is not turn evidence.

### Modified Capabilities

None. Both capabilities are new, so no existing requirement block is edited.

## Posture and scope

[decision] Posture A, from the owner-vetted engineering call that closed bu-b1ybef on 2026-10-17:
derive on demand from data already held, add no collection. Posture B (a forward-only SENT metadata
store) and C (defer the turn half) are not taken. Reversible: yes.

Out of scope, stated as `REQ-relationship-turn-status-008`:
- Gmail SENT ingestion (bu-h40h2b.14 acceptance 2): no provider call, OAuth scope change, backfill
  or new retained field. A SENT collection store would be an owner privacy decision.
- A stored turn-state table or any durable materialization.
- Circles by canonical person (slice D) and occasion candidates (slice E): they keep their owners
  (bu-h40h2b.16, bu-h40h2b.7 and bu-h40h2b.13) and are not specified here.

## Relationship to specify-connector-event-clock-and-coverage (#4686)

The two changes touch different capabilities and do not edit a common requirement block, so they can
be adopted in either order. One soft dependency: ordering the newest inbound and outbound events
(REQ-relationship-turn-status-004) uses `occurred_at` when its basis is `source_stated`. Until the
event-clock change is adopted and implemented, ordering falls back to the day of receipt, which makes
same-day exchanges `unknown`. That is a correct but conservative degradation, not a blocker. The
conventions are shared: the same `source_stated` vocabulary, `unavailable` is never an all-clear,
and no provider call.

## Impact

Spec only. No code, migration, provider call or live action. On adoption bu-h40h2b.14 unblocks;
if the owner declines, bu-h40h2b.14 closes as declined.
