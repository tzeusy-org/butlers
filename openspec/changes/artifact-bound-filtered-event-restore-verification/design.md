## Context

`deploy/backup/pg_dump.sh` currently publishes a compressed plain-SQL dump and a
fixed-vocabulary run receipt. The protected restore-drill executor selects the
newest artifact, restores it to `butlers_restore_drill`, checks that at least one
non-system table exists, removes the scratch database, and records a sanitized
pass or failure through the executor-owner authority. The ordinary dashboard,
butler, connector, and shared PostgreSQL credentials remain outside that
privileged path.

`connectors.filtered_events` needs stronger proof because it is a range-
partitioned table whose whole UTC-month children are the future retention unit.
An artifact can pass the generic non-system-table check while omitting the
parent, one child, a bound, or rows. Counts taken later from the live database do
not describe the artifact. Counts taken independently during backup can also
race with inserts, replay transitions, partition attachment, or backfill.

## Goals / Non-Goals

**Goals:**

- bind filtered-event recovery evidence to the exact compressed artifact and a
  manifest captured from the same PostgreSQL snapshot as its dump;
- prove the partitioned parent, key, complete attached-child set, normalized
  month bounds, and exact per-child row counts without reading row content;
- make missing, malformed, partial, stale, cross-artifact, or mismatched evidence
  fail closed with fixed reason codes;
- preserve the protected executor as the sole scratch-restore and authoritative
  result path; and
- define testable producer/checker seams before implementation.

**Non-goals:**

- reading a backup or payload, running a restore, or changing any live system in
  this planning change;
- proving application semantics for the payload stored in a row;
- choosing or enabling a retention policy;
- adding an API that accepts caller-supplied manifests or verdicts; or
- weakening, bypassing, or duplicating the protected restore executor.

## Decisions

### 1. The backup producer owns one content-blind manifest per artifact

The backup producer is the only component that can make an artifact-time claim,
so it owns capture and publication. For an artifact named
`butlers_<timestamp>.sql.gz`, it produces a sibling
`butlers_<timestamp>.sql.gz.filtered-events-manifest.json`. The manifest is an
exact JSON object with unknown fields rejected and this version-1 shape:

```json
{
  "schema_version": 1,
  "scope": "connectors.filtered_events",
  "capture": {
    "method": "postgres_exported_snapshot",
    "started_at": "RFC3339 UTC timestamp",
    "completed_at": "RFC3339 UTC timestamp",
    "complete": true
  },
  "artifact": {
    "basename": "butlers_<timestamp>.sql.gz",
    "sha256": "64 lowercase hexadecimal characters",
    "size_bytes": 1
  },
  "parent": {
    "schema": "connectors",
    "name": "filtered_events",
    "relkind": "p",
    "partition_strategy": "range",
    "partition_key": ["received_at"]
  },
  "partitions": [
    {
      "name": "filtered_events_YYYYMM",
      "relkind": "r",
      "lower_bound": "RFC3339 UTC month start",
      "upper_bound": "RFC3339 UTC next-month start",
      "row_count": 0,
      "status_counts": {
        "filtered": 0,
        "error": 0,
        "replay_pending": 0,
        "replay_complete": 0,
        "replay_failed": 0
      }
    }
  ]
}
```

`status_counts` is optional as a whole. When present, it contains exactly the
five allowed status keys, including zeroes, and its non-negative integer values
sum exactly to `row_count`. Every partition entry is unique and sorted by name.
The suffix and bounds agree: `filtered_events_202608` has exactly
`[2026-08-01T00:00:00Z, 2026-09-01T00:00:00Z)`. Gaps between months are allowed
because a month with no insert need not have a child; duplicates, overlapping
bounds, default partitions, unattached tables, extra partition-key columns, and
non-month bounds are not allowed by this scope.

The allowlist deliberately excludes row IDs, connector or sender identities,
external IDs, preview/subject text, filter reasons, `full_payload`, error detail,
replay timestamps, SQL text, dump fragments, credentials, absolute paths, and
query/client diagnostics. Names of the fixed parent and its month partitions,
timestamps that describe capture/bounds, counts, sizes, and cryptographic
digests are metadata rather than content. They remain forbidden as metric
labels.

### 2. Dump and manifest share one exported PostgreSQL snapshot

The producer begins a repeatable-read, read-only transaction and exports its
snapshot. The dump imports that snapshot through PostgreSQL's supported
`pg_dump --snapshot` path. Manifest catalog queries and count queries run in the
exporting transaction or in repeatable-read, read-only sessions that import the
same snapshot before their first query. The exporting transaction stays open
until both the dump and every manifest query finish.

The manifest inventory comes from PostgreSQL partition catalogs, not table-name
discovery. It records every child attached to `connectors.filtered_events` in
that snapshot. Every count is a `COUNT(*)` scoped to one recorded child;
optional status counts group only by the closed status vocabulary. No payload
column is selected. If snapshot export/import, catalog discovery, any count,
the dump, gzip validation, or manifest validation fails, the producer publishes
no completed pair and records a failed backup run through the existing run-
outcome mechanism.

This design allows writes after snapshot acquisition without mixing states. A
row committed later is absent from both the dump and manifest. Concurrent DDL
that makes the snapshot unusable causes the producer to fail rather than
publishing an unproven pair.

### 3. The manifest is the pair's final publication marker

The producer writes the dump and manifest to temporary names, validates gzip and
the manifest, computes the exact compressed artifact's SHA-256 and byte length,
and writes those values into the temporary manifest. It publishes the artifact
first and the manifest last. A completed manifest is therefore the commit marker
for the pair. Candidate selection treats an artifact without its valid sibling
manifest as incomplete, even if the generic artifact scanner can decompress it.

A crash between the two final renames can leave an orphan artifact but cannot
create filtered-event recovery evidence. A manifest without its artifact is
also ineligible. Backup pruning and explicit artifact movement copy or remove
the pair together; an orphan may be cleaned up only by the existing backup-
artifact lifecycle, never by the filtered-event retention job. `last_run` may
report success only after the complete pair is published.

### 4. The checker runs only inside the protected restore verification stage

The existing restore-drill executor remains the sole owner of artifact
selection, scratch creation, restore, verification, cleanup, and authoritative
result persistence. The checker is a deterministic function invoked by that
executor after the selected artifact has restored successfully to the scratch
database and before post-run cleanup can yield an overall pass. It receives the
already selected artifact, its sibling manifest, and a connection scoped to the
scratch database. It has no endpoint, scheduler, credential, live-database
comparison, or ability to create/drop a database.

Before trusting expected structure or counts, the executor hashes the exact
selected compressed artifact and compares basename, byte length, and SHA-256
with the manifest. It hashes the exact manifest bytes for the durable scoped
receipt. A stale manifest copied under a newer name, a manifest from another
artifact, and a same-name artifact replacement all fail artifact binding.

The scratch comparison runs in one repeatable-read, read-only transaction. It
verifies:

1. `connectors.filtered_events` exists with `relkind='p'`, range strategy, and
   exactly one partition key, `received_at`;
2. the exact set of attached child names equals the manifest set, with no
   missing, unexpected, or unattached name-matching table accepted;
3. each child is an ordinary relation attached directly to the parent and has
   the exact normalized half-open UTC month bounds in the manifest;
4. each child's exact `COUNT(*)` equals `row_count`; and
5. when `status_counts` is present, every closed-vocabulary status count equals
   the manifest and sums to the child count.

The checker does not accept a partial pass. A missing parent, one missing child,
one extra attached child, one wrong bound, one count mismatch, an unknown status,
or any query/parse failure fails the entire scoped verdict. It never substitutes
current live counts when manifest evidence is unavailable.

### 5. Scoped verdicts are exact, durable, and sanitized

The checker returns exactly one scoped verdict:

```text
scope = connectors.filtered_events
result = pass | fail
reason_code = <closed vocabulary>
artifact_sha256 = <exact selected artifact digest or null>
manifest_sha256 = <exact selected manifest digest or null>
manifest_capture_completed_at = <manifest UTC timestamp or null>
```

The closed failure vocabulary is:

- `manifest_missing`
- `manifest_malformed`
- `manifest_unsupported`
- `manifest_incomplete`
- `artifact_identity_mismatch`
- `capture_consistency_invalid`
- `parent_mismatch`
- `partition_set_mismatch`
- `partition_bound_mismatch`
- `partition_count_mismatch`
- `status_count_mismatch`
- `verification_query_failed`

`pass` uses `reason_code=ok` and requires all three binding fields to be
non-null. A failure may leave a field null when it cannot be established safely.
The executor records the scoped verdict through a migration-owned extension to
its existing protected result authority. No caller-supplied dashboard or normal
runtime value is accepted as evidence. The fixed public audit projection and
operator API need expose only the existing overall result and stable sanitized
failure classification; they do not expose artifact paths, counts, manifest
bodies, query output, or client diagnostics.

Any scoped failure maps to the existing overall restore result
`result=fail`, `failure_stage=verify`, and
`failure_code=integrity_check_failed`. A scoped pass is necessary but not
sufficient for an overall pass: generic restore verification and post-cleanup
must still succeed. This preserves the existing result vocabulary and prevents
a table-specific success from masking a later lifecycle failure.

### 6. Missing and legacy metadata fail closed

An artifact produced before this manifest contract, a missing sibling, invalid
JSON, duplicate key, unknown field, unsupported version, incomplete capture,
invalid timestamp ordering, noncanonical digest, impossible count, partial
partition list, or failed artifact-identity comparison cannot produce a scoped
pass. There is no compatibility fallback to generic table count, filename-only
matching, filesystem mtime, the current live database, or an operator assertion.

Deployment must therefore stage the producer first, wait for at least one valid
pair, then deploy the checker/executor update. If an older artifact is selected
after the checker becomes active, the truthful outcome is a verify-stage failure
until a valid pair is available. Rollback preserves artifacts, manifests, and
protected result history; it does not synthesize a pass or mutate live data.

## Risks / Trade-offs

- Exact `COUNT(*)` capture adds bounded work to backup creation. Implementation
  must measure it and keep timeouts/resource limits explicit; it must not weaken
  consistency or silently omit a child to make the backup finish.
- Publishing two files is not filesystem-atomic. Manifest-last publication and
  pair-only candidate selection turn a crash into an incomplete pair that fails
  closed rather than false evidence.
- SHA-256 binds identity, not hostile-host authenticity. The host and backup
  volume remain trusted by the existing deployment threat model.
- Older artifacts intentionally cannot pass the stronger scope. The producer-
  first rollout prevents avoidable downtime while retaining honest failure for
  incomplete evidence.

## Evidence Basis And Authority

The `bu-3lijn` packet is dated development evidence of the present gap and of
the content-blind fields available for a future manifest. It is not a current
runtime snapshot, implementation acceptance, owner approval, or authorization
to inspect artifacts or execute the protected lifecycle. Implementation,
deployment, restore execution, and any retention decision remain separate acts.
