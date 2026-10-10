# Parked-notification approval review door

## Why

Run13 S4 is an already owner-released seam-truth outcome: a real approval park blocks a routed notification, but its action identity disappears into an error string before the notification feed. The owner cannot open the same pending action from that failed row. CLOSED `bu-3b6goa` released the original run13 intent; this proposal adds its missing governing contract. It does not claim the outcome is implemented or authorize implementation.

## What Changes

- Add a `core-notify` requirement for a bounded, server-derived same-action review reference from a committed Messenger recipient-gate park through the existing routed refusal and Switchboard notification writer.
- Add a `dashboard-visibility` requirement for an independently admitted notification projection and keyboard-operable `Review approval` link to the adopted `/approvals/{id}` dossier.
- Keep delivery blocked and existing status/error, role, actor, privacy, authorization, session/trace, pagination, statistics and triage contracts intact.

## Scope and Non-goals

The target is the actual Messenger-routed notification recipient-gate park (email and non-email), including its existing failed ledger row. Local `notify()` parks that return `pending_action_id`, missing-identifier parks, and approval recovery/control envelopes retain their current contracts; this change does not invent ledger rows for them. A reference grants navigation only. No prepare, approve, execute, send, retry, escalation, provider request, role, credential, actor or permission authority is added. No arbitrary metadata/error parsing, retrospective backfill, new table, global UUID assumption or cross-butler daemon SQL is permitted. A later separately reviewed implementation may add the dedicated nullable correlation column described in design; this spec-only PR does not migrate or write data.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-notify`: committed park review-reference preservation.
- `dashboard-visibility`: safe review-reference projection and notification door.

## Impact

Future owned implementation surfaces are Messenger `_routing.py`, the pool-scoped park decision interface, Switchboard notification delivery/logging and its owning migration, notification API models/readers, and the feed/type boundary. The existing `/approvals/{id}` authorization and mutation boundaries stay binding. Nine active core-notify holders and all current baseline/scenario/task bodies remain untouched. Native application/archive, source implementation and actual SQL are outside this contract publication stage. Parent five criteria, all14 original findings, S3 and numeric R-alias uncertainty survive.
