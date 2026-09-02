# RFC 0029: Expected Signals and Honest Absence

**Status:** Accepted
**Date:** 2026-09-03

## Context

Elapsed time is not evidence of owner behavior when the instrument that would
produce an observation is unavailable. Existing gap detectors compare a last
observation with a cadence independently of connector liveness, allowing a dead
connector to produce claims such as "you have not logged a measurement."

## Decision

`public.expected_signals` is the shared deterministic ledger for expected
observations. A producer-owned key records the producer, expected cadence, last
observation, evaluation time, and exactly one state:

- `present`: the producer is measurable and the cadence has not elapsed;
- `absent`: the producer is measurable and the cadence has elapsed;
- `unmeasurable`: producer liveness or health cannot support an absence claim.

Connector producers are written as `connector:<connector_type>` and join the
existing read-only `public.v_qa_connector_state` projection. A connector is
measurable only when at least one registered runtime is healthy with a heartbeat
inside the canonical five-minute liveness window. Missing, stale, offline,
paused, degraded, errored, or unreadable liveness is `unmeasurable`. The special
`owner` producer covers explicitly owner-entered observations and has no external
instrument dependency.

The table is globally keyed and row-level security binds updates to the runtime
role that first claimed the key. All roles may read the tri-state projection;
they may not overwrite another role's signal. The core migration serializes
database-global creation because the core chain runs once per target schema.

Gap detectors MUST persist the tri-state before proposing an owner-facing
candidate. They may propose absence only from `absent`; `unmeasurable` is an
instrument condition and suppresses owner-behavior wording. API failure returns
an explicit degraded envelope, and clients render instrument failure rather than
an empty all-clear.

## Initial adoption

Health measurement gaps are the first adopted detector. Their expected cadence
is the existing two-times-median warning boundary. Measurement provenance binds
Google Health and Home Assistant observations to their connector liveness;
purely manual histories use `owner`; unknown provenance is unmeasurable.
The boundary is inclusive: evaluation exactly at the expected timestamp is
`absent`. A history containing both connector producers is unknown and therefore
unmeasurable; row order never selects authority.

Relationship interaction gaps and Finance recurrence gaps require a separate
provenance decision before adoption. Their current rows do not identify one
authoritative producer, so assigning a connector here would fabricate
measurability. RFC 0029 supplies their shared primitive but does not introduce a
new notification policy or guess a producer.

## Failure properties

- Liveness read failure fails closed to `unmeasurable`.
- Persistence failure aborts the detector; no unreceipted absence is emitted.
- Concurrent upserts converge on one key.
- An unavailable API query is not represented as an empty signal list.
- The ledger stores no measurement value or owner-facing message.
- The standard backup excludes the forced-RLS ledger rather than enabling row
  security or aborting the dump; source observations and liveness remain in the
  artifact, and detector evaluation rebuilds the projection after restore.

## Alternatives rejected

- Elapsed-time-only gaps: instrument failure impersonates owner behavior.
- Treating stale liveness as absent: absence of evidence is promoted to evidence.
- Guessing Relationship or Finance producers: their current provenance is not
  sufficient to bind one liveness source honestly.
