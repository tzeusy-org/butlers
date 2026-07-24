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
