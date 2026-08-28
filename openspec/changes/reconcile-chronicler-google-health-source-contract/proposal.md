## Why

The accepted Chronicler RFC and two canonical capability specs still describe
`google_health` as deferred, while the shipped source registry and scheduled
adapters now have their migration-tracked `health.facts` read grant through
Health memory `mem_011`. Their regression tests project that approved evidence.
That mismatch makes a deterministic retrospective projection look unsupported
and obscures the much narrower truth about workouts.

## What Changes

- Record the already-shipped Google Health-to-Chronicler projection boundary in
  RFC 0014 and the governing capability specs, including the now-landed
  migration-tracked read prerequisite.
- Declare `google_health.measurements`, `health.steps`, and
  `health.heart_rate` as supported `health.facts` projection sources with their
  actual outputs, precision, privacy, idempotency, and optional-schema
  behavior.
- State explicitly that the current Google Health connector emits sleep and
  daily-summary resources only. It does **not** ingest or write
  `workout_session`; the existing workout adapter can project a separately
  present fact but does not make workout ingestion a connector capability.
- Preserve the existing read-only, migration-tracked source boundary. This
  change documents but does not alter `mem_011`, ACLs, migrations, connector
  polling, Health ingest, credentials, deployment, or PR #3897.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-chronicler`: source compatibility declarations describe the
  supported Health fact projections rather than an obsolete blanket deferral.
- `chronicler-source-compatibility`: Google Health receives a complete
  deterministic `health.facts` compatibility declaration, including the
  upstream workout-ingestion boundary.
- `connector-google-health`: the connector's Chronicler boundary distinguishes
  supported downstream fact projection from its still-absent workout ingest
  resource.

## Impact

Documentation and contract artifacts only: RFC 0014, the three OpenSpec
capability specs, and no runtime source. Existing `mem_011`, adapter schedules,
and tests remain the implementation evidence; no new API, schema, credential,
or deployment behavior is introduced.
