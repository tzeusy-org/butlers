## Why

The current Decisions digest reads a host-local Beads JSONL export through
read-only compose mounts. That preserves a useful single-host compatibility
path, but it cannot serve a separated runtime host, can freeze at a replaced
file inode, and cannot provide an atomic relational snapshot without giving
runtime workloads tracker access.

The owner has selected a planning-only PostgreSQL projection: a tracker-host
exporter publishes a minimal active Beads snapshot, while runtime readers use
one bounded provider and retain the existing honest degraded-state behavior.
This change records the contract and implementation graph; it does not install
or run the exporter, create credentials, execute a migration, alter a network,
or cut consumers over.

## What Changes

- Define a deterministic tracker-host Beads exporter that parses a local
  export, allowlists active issue/dependency and normalized decision-lint
  fields, and transactionally publishes a PostgreSQL snapshot.
- Define the `beads_projection` storage, role, retention, and atomic
  publication/read contracts: one active snapshot plus two prior complete
  snapshots, and 30 days of categorical failed-run metadata.
- Require source-completeness evidence from the same source watermark before a
  candidate can publish, so an empty, partial, or count-regressed candidate
  without matching authoritative count/digest evidence becomes the categorical
  unavailable state `source_completeness_unverified`, never an all-clear.
- Define a bounded asynchronous `BeadReadProvider` that returns one coherent
  active snapshot with target/warning/hard freshness semantics of five, ten,
  and fifteen minutes respectively.
- Preserve the existing deterministic decision-label, dependency-escalation,
  and unlabeled-marker lint behavior while removing the need for runtime
  callers to parse JSONL or inspect raw Beads metadata.
- Specify a 14-day JSONL-versus-projection shadow parity gate and an explicit,
  seven-day JSONL rollback selection after cutover. No automatic fallback or
  JSONL retirement is introduced; the cutover remains Decisions-only and
  retains `GET /api/beads/{id}` with `BeadSnapshotReader` until a separately
  scoped security-reviewed migration.
- Add RFC and architecture documentation plus a test-first implementation and
  activation plan. The only changes in this planning packet are documents and
  OpenSpec artifacts.

## Capabilities

### New Capabilities

- `beads-projection`: Management-plane export, bounded projection storage,
  atomic reader contract, freshness, and retention for active Beads data.

### Modified Capabilities

- `dashboard-api`: The read-only Decisions digest changes from a direct JSONL
  reader to the bounded provider while retaining source-honest availability,
  structured decision detail, and escalation behavior.
- `dashboard-decisions`: The Decisions source plaque and degraded state must
  identify projection freshness without treating a warning or unavailable
  source as a calm all-clear.

## Impact

- Future core work: `beads_projection` schema migration, least-privilege
  database roles/views, exporter entry point, and reader provider.
- Future consumer work: `decision_review`, the Switchboard scheduled jobs, and
  `GET /api/decisions` will share the provider; the dashboard receives
  additive snapshot provenance/freshness metadata.
- Future platform work: an operator-authorized tracker-host workload, TLS
  writer credential, migration execution, shadow observation, and explicit
  cutover/rollback procedure.
- This PR makes none of those operational changes. Beads/Dolt remains the sole
  authoritative tracker; the selected JSONL file remains a derived compatibility
  and rollback path, never a second tracker authority, and stays intact.
