## ADDED Requirements

### Requirement: Fenced Cancellation Return Is All-Cohort and Guarded
The ordinary deferred-notification Scheduler SHALL keep all
`release_prepared` and cancellation-ready wake-recovery rows scheduler-ineligible.
An origin may create cancellation-ready state only through authenticated
`wake_recovery.cancel_finalize.v1`, which delivers accepted Messenger evidence
and its own immutable frozen-subset manifest entry and atomically persists the
matching local finalization receipt. The Scheduler SHALL allow a future
cancellation-return state only after receiving a matching
`wake_recovery.cancel_publish.v1` receipt whose run, fence, origin identity,
participant/cohort digests, immutable local `origin_frozen_subset_digest`/count,
release action, cancellation action key, accepted Messenger decision, local
finalization receipt digest, stable publish action/request identities, and
complete finalization digest exactly match the row's durable provenance. The
origin SHALL prove that local subset only from its own durable rows/receipts;
the global cohort digest alone is insufficient and peer-schema reads are
forbidden.

The Scheduler SHALL reject partial, stale, changed-digest, DND-blocked,
egress-present, or ambiguous cancellation outcomes. It SHALL not append late
rows, recalculate a target, re-gate the parent wake release, or use the legacy
unguarded flush path for a cancelled cohort. Future effective egress SHALL
preserve the receipt's DND evidence and complete its own Messenger admission.

#### Scenario: Cancellation-ready row remains invisible to ordinary flush
- **WHEN** an origin has recorded its local finalization receipt but has not
  received a complete finalization digest and publish authorization
- **THEN** its rows remain scheduler-ineligible
- **AND** the ordinary deferred-notification flush neither selects nor sends them

#### Scenario: Changed publish evidence cannot expose a subset
- **WHEN** a publish request carries a different fence, cohort digest, release
  action, cancellation action key, local subset digest/count, target origin, or
  missing/incompatible local finalization receipt or incomplete finalization
  digest
- **THEN** the Scheduler rejects it without changing row eligibility
- **AND** no individual row becomes ordinary pending work

#### Scenario: DND rejection remains a parent retained state
- **WHEN** Switchboard relays the parent `abort.v1(reason=blocked_dnd)` receipt
  after Messenger rejects precommit cancellation for DND
- **THEN** the origin applies the parent-defined `release_retained_dnd` state to
  its complete frozen subset and returns the durable parent receipt
- **AND** it neither performs a new DND query nor accepts `cancel_publish.v1`
