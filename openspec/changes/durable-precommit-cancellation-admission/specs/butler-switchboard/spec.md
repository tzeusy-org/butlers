## ADDED Requirements

### Requirement: Switchboard-Mediated Wake-Recovery Cancellation
Switchboard SHALL be the only cross-butler relay for a future wake-recovery
precommit cancellation. It SHALL validate the local Scheduler/Health decision
against the durable current-fence run, snapshot participant set, frozen cohort,
and release action before it calls Messenger. It SHALL relay the exact stable
cancellation action key, request correlation, DND-generation evidence, and
opaque decision reference without inventing a replacement action or reading a
peer schema.

At prepare time, Switchboard SHALL persist the immutable ordered per-origin
frozen-subset manifest that `cohort_digest` commits to. After a Messenger
`accepted_precommit` receipt, it SHALL derive and persist one stable
`cancel_finalize.v1` action/request identity for each origin, then deliver the
opaque accepted Messenger evidence and that origin's manifest entry through the
authenticated finalization operation. It SHALL retain the resulting durable
same-fence finalization receipt for every participant and replay the exact
finalization packet after a timeout or restart; it SHALL not infer finalization
from the cohort digest or current origin state. Only after it has persisted a
compatible receipt from every participant may it derive the aggregate
finalization digest and send authenticated `wake_recovery.cancel_publish.v1`
packets. Each publish packet carries the recipient's own origin-frozen-subset
digest/count and finalization-receipt digest as well as the global
participant/cohort and finalization commitments, with a stable per-origin
action/request identity. Switchboard SHALL retain every publish receipt and
replay the exact packet after a timeout or restart; it SHALL not infer success
from the cohort digest.

On a durable Messenger `rejected_blocked_dnd` receipt, Switchboard SHALL persist
a same-fence fanout record and invoke the parent
`strict-owner-telegram-wake-recovery` `abort.v1(reason=blocked_dnd)` operation
for every current participant. It SHALL retain/replay each parent abort until
every participant returns its parent receipt and every origin has its
parent-defined `release_retained_dnd` result. It SHALL not send
`cancel_publish.v1`, query DND for an origin, read a peer queue, request a
partial cancellation, recompute the cohort, or dispatch a provider action.
Other rejected/ambiguous results retain the same action and remain
scheduler-ineligible.

This coordination SHALL remain deterministic under RFC 0038 D1/D2/D7 and SHALL
NOT admit a reasoning goal, invoke an LLM, start a session or delegate reasoning.
Any generic route proxy for cancellation traffic SHALL be replaced by named
endpoints on Switchboard's service listener. Each endpoint SHALL fix its payload
schema, one recipient service-plane operation and an allowlist of authenticated
calling daemons; neither endpoint nor target SHALL be LLM-presentable or served
on the session listener. Unknown endpoints, unauthenticated/unlisted callers,
invalid schemas and mismatched targets SHALL be refused before recipient contact;
a payload's claimed source SHALL NOT establish caller authority.

Every frozen origin SHALL retain a separate declared, recipient-bound endpoint
for each existing finalization, publication and parent DND-abort operation. The
packet's origin identity SHALL match that endpoint's fixed recipient rather
than select an arbitrary target/tool. Missing endpoints SHALL follow the
existing unavailable-participant retention/retry rule, without shrinking the
snapshot cohort, replacing actions or publishing a subset. These endpoint
checks SHALL supplement, not replace, the complete run/fence/cohort/local-subset,
replay, Messenger admission and DND evidence checks, and SHALL create no
subscription, goal or grant.

ID: REQ-butler-switchboard-010
Source: proposal.md; design.md D0–D7; RFC 0038 D1/D2/D7; REQ-butler-switchboard-008
Scope: v1-mandatory

#### Scenario: Switchboard forwards only a durable current-fence decision
- **WHEN** Health or an origin Scheduler supplies cancellation evidence for the
  current wake run
- **THEN** Switchboard verifies the immutable run/fence/digests/action binding
  before it calls Messenger
- **AND** a stale or changed local decision reaches no participant or egress
  surface

#### Scenario: Unavailable participant preserves the exact cancellation action
- **WHEN** Messenger accepts cancellation but a required participant is
  unavailable during `cancel_finalize.v1`
- **THEN** Switchboard retains the cohort and retries the same action and
  finalization request identity for that participant set
- **AND** it does not publish scheduler work or create a replacement action key

#### Scenario: Finalization response is recovered without early publication
- **WHEN** an origin commits a durable finalization receipt but its response is
  lost or Switchboard restarts
- **THEN** Switchboard replays that origin's exact `cancel_finalize.v1` action,
  request ID, and fingerprint until it receives the stored receipt
- **AND** it does not derive the aggregate finalization digest or send
  `cancel_publish.v1` until every participant has a compatible receipt

#### Scenario: DND rejection fans out only through the parent retained path
- **WHEN** Messenger returns a durable `rejected_blocked_dnd` receipt for the
  current cancellation action
- **THEN** Switchboard replays the same-fence parent `abort.v1(reason=blocked_dnd)`
  operation to every current participant until each has its parent receipt and
  every origin has the parent-defined retained result
- **AND** no origin receives `cancel_publish.v1` or a direct DND-derived SQL
  state change
