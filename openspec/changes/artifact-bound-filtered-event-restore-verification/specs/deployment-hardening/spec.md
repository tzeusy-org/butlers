## ADDED Requirements

### Requirement: Artifact-Bound Filtered-Event Restore Manifest

Every newly published PostgreSQL backup artifact eligible for filtered-event
restore verification SHALL have one strict, versioned, content-blind sibling
manifest owned by the backup producer. The manifest SHALL bind the exact
compressed artifact to the PostgreSQL snapshot used by `pg_dump` and SHALL
record the `connectors.filtered_events` partitioned-parent shape, complete set
of attached children, normalized half-open UTC month bounds, and exact
artifact-time per-child row counts. It MAY record exact counts for each allowed
status, but SHALL NOT contain row content, row or external identifiers, sender
or connector identities, previews, filter reasons, payloads, error details,
replay timestamps, SQL/dump fragments, credentials, absolute paths, or raw
diagnostics.

ID: REQ-deployment-hardening-008
Source: Non-Negotiable Rules 1 and 4; RFC 0006 § Database Connection Scoping; RFC 0008 § Invariants
Scope: v1-mandatory

#### Scenario: Producer captures one coherent artifact and manifest

- **WHEN** the backup producer creates a dump eligible for filtered-event
  restore verification
- **THEN** it exports one repeatable-read, read-only PostgreSQL snapshot and
  keeps the exporting transaction open while `pg_dump` and every manifest
  catalog/count query use that snapshot
- **AND** the manifest records `capture.method="postgres_exported_snapshot"`,
  ordered UTC capture timestamps, and `capture.complete=true`
- **AND** an insert, replay transition, backfill, or partition change committed
  after snapshot acquisition is either absent from both outputs or makes
  production fail; it never appears in only one side of a published pair

#### Scenario: Manifest describes the complete partition scope

- **WHEN** the producer constructs the manifest from the exported snapshot
- **THEN** it records the fixed parent as `connectors.filtered_events` with
  `relkind="p"`, range strategy, and the sole partition key `received_at`
- **AND** it records every directly attached child exactly once, using a
  fixed `schema="connectors"`, `filtered_events_YYYYMM` name, ordinary-relation
  kind, normalized UTC month lower bound, next-month exclusive upper bound, and
  non-negative exact row count
- **AND** the complete set and uniqueness comparison uses fully qualified
  `(schema, name)` identities, never relation name alone
- **AND** no attached foreign-schema child, name-matching unattached table,
  default partition, partial child list, duplicate child, overlapping bound, or
  non-month bound is accepted

#### Scenario: Optional status counts remain complete and content-blind

- **WHEN** a partition entry includes status counts
- **THEN** it contains exactly `filtered`, `error`, `replay_pending`,
  `replay_complete`, and `replay_failed`, including zero values
- **AND** every value is a non-negative integer and the sum equals that child's
  exact row count
- **AND** the producer obtains those counts without selecting payload, preview,
  identity, reason, or diagnostic columns

#### Scenario: Artifact identity is bound before publication

- **WHEN** the temporary dump has completed and passed gzip integrity checks
- **THEN** the producer records its exact basename, byte length, and lowercase
  SHA-256 in the validated temporary manifest
- **AND** the manifest uses the supported schema version, fixed scope, exact
  field allowlist, canonical types, unique child entries, and valid timestamp,
  digest, count, status, and bound relationships

#### Scenario: Incomplete production never becomes recovery evidence

- **WHEN** snapshot coordination, dump, gzip verification, catalog discovery,
  any count, digest calculation, or manifest validation fails
- **THEN** the backup run fails and publishes no completed artifact/manifest pair
- **AND** a crash that leaves only one final file yields an incomplete pair,
  never a filtered-event recovery pass
- **AND** candidate selection ignores or rejects an artifact without its valid
  sibling even when the artifact alone decompresses successfully

#### Scenario: Pair publication and lifecycle stay coupled

- **WHEN** a valid temporary artifact and manifest are ready
- **THEN** the producer publishes the artifact first and the manifest last as the
  pair's completion marker, and reports backup-run success only after both exist
- **AND** backup-artifact movement and pruning copy or remove the pair together
- **AND** an orphan is governed only by the existing backup-artifact lifecycle,
  never by the `connectors.filtered_events` retention job

### Requirement: Protected Filtered-Event Restore Checker

The existing isolated restore-drill executor SHALL run one deterministic,
all-or-nothing `connectors.filtered_events` checker during its scratch-database
verification stage. The checker SHALL compare the authorized scratch restore
only with the manifest paired to the exact selected artifact; it SHALL NOT use
the later live database, filesystem mtime, an artifact filename alone, an
operator assertion, or generic non-system-table presence as substitute evidence.
It SHALL create no alternate restore endpoint, credential, scheduler, scratch
lifecycle, or result authority.

ID: REQ-deployment-hardening-009
Source: Non-Negotiable Rules 1 and 4; RFC 0005 § Workflow and Recovery Telemetry; RFC 0006 § Database Connection Scoping; RFC 0008 § Invariants
Scope: v1-mandatory

#### Scenario: Checker binds the exact selected pair before comparison

- **WHEN** the protected executor reaches verification after restoring a
  selected artifact to its scratch database
- **THEN** it validates the sibling manifest and compares the selected compressed
  artifact's exact basename, byte length, and SHA-256 with the manifest before
  trusting any expected structure or count
- **AND** it hashes the exact manifest bytes for the scoped receipt
- **AND** a missing, malformed, unsupported, incomplete, stale, renamed,
  same-name-replaced, or cross-artifact manifest fails closed

#### Scenario: Checker proves parent and exact attached-child structure

- **WHEN** artifact and manifest binding succeeds
- **THEN** one repeatable-read, read-only transaction against the scratch
  database proves that `connectors.filtered_events` exists with
  `relkind="p"`, range strategy, and exactly the `received_at` partition key
- **AND** the attached fully qualified `(schema, name)` child identities equal
  the manifest set exactly, every child is in the fixed `connectors` schema, and
  no missing, unexpected, foreign-schema, or unattached name match is accepted
- **AND** every fully qualified child is attached directly to the parent with
  the exact normalized half-open UTC month bounds declared by the manifest

#### Scenario: Checker proves exact counts without content reads

- **WHEN** the scratch structure matches the manifest
- **THEN** the checker compares exact `COUNT(*)` for each attached child with its
  manifest row count
- **AND** when status counts are present, it compares all five allowed statuses,
  rejects an unknown status, and proves their sum equals the child row count
- **AND** no payload, preview, identity, reason, or diagnostic column is selected
  or emitted as evidence

#### Scenario: Partial or unavailable verification fails the whole scope

- **WHEN** any parent, partition-set, bound, row-count, or optional status-count
  check mismatches, or a catalog/count query or parse fails
- **THEN** the scoped result is `fail`; no passing subset, inferred value,
  current-live comparison, or last-known result substitutes for the failed check
- **AND** the overall restore drill records `result="fail"` with
  `failure_stage="verify"` and `failure_code="integrity_check_failed"` unless a
  later post-cleanup failure supplies the terminal overall stage/code while
  retaining this scoped failure in the same attempt row
- **AND** generic verification success cannot override that scoped failure

#### Scenario: Passing verdict is tied to the exact artifact and capture

- **WHEN** every artifact, manifest, parent, child, bound, and required count
  check succeeds
- **THEN** the checker returns `scope="connectors.filtered_events"`,
  `result="pass"`, and `reason_code="ok"`, bound to non-null artifact SHA-256,
  manifest SHA-256, and manifest capture-completion timestamp
- **AND** the protected executor persists the scoped binding and overall result
  in the same immutable executor-owner attempt row and database transaction
- **AND** that row's database constraints require an overall pass to carry this
  exact scoped pass and all three non-null binding fields
- **AND** generic restore verification and successful post-run scratch cleanup
  remain separately necessary for the overall pass

#### Scenario: Crash or retry cannot reuse an earlier scoped pass

- **WHEN** the executor crashes before authoritative attempt persistence, retries
  after an interrupted attempt, or records two attempts against adjacent
  artifacts
- **THEN** a pre-commit crash leaves no partial authoritative row, and each retry
  recomputes and atomically records its own scoped verdict and bindings
- **AND** a post-commit crash leaves one complete immutable attempt row
- **AND** no read or write path may join, copy, or associate a scoped pass or
  artifact/manifest/capture binding from one attempt row with another attempt's
  overall result

#### Scenario: Failure verdict uses a closed content-blind vocabulary

- **WHEN** the scoped checker cannot pass
- **THEN** its reason is exactly one of `manifest_missing`,
  `manifest_malformed`, `manifest_unsupported`, `manifest_incomplete`,
  `artifact_identity_mismatch`, `capture_consistency_invalid`,
  `parent_mismatch`, `partition_set_mismatch`, `partition_bound_mismatch`,
  `partition_count_mismatch`, `status_count_mismatch`, or
  `verification_query_failed`
- **AND** the protected result may retain only the fixed scoped result/reason,
  safe digests, and capture timestamp; public audit/API/log/metric projections
  do not expose paths, manifest bodies, counts, query output, client diagnostics,
  payload-derived values, or high-cardinality digest labels

#### Scenario: Existing privileged authority remains the only execution path

- **WHEN** filtered-event restore verification is implemented, deployed, or run
- **THEN** only the separately authorized protected executor may select/read the
  artifact, create or inspect the scratch restore, and persist the authoritative
  result
- **AND** dashboard-api, butlers, connectors, and shared PostgreSQL credentials
  receive no executor secret, `CREATEDB`, backup-body access, checker-write
  authority, or caller-supplied verdict path
- **AND** specification, implementation review, deployment, restore execution,
  and any retention activation remain separately authorized acts

### Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): owner sovereignty
  requires recovery evidence for the exact owner-controlled artifact.
- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): deterministic backup
  and verification infrastructure must be predictable and testable.
- `about/heart-and-soul/security.md` § Schema Isolation and Credential
  Management: the existing purpose-bound executor remains the privileged
  runtime boundary.
- RFC 0005 § Workflow and Recovery Telemetry: persist structured failure
  evidence while keeping high-cardinality identifiers out of metrics.
- RFC 0006 § Database Connection Scoping: ordinary runtime roles do not acquire
  backup or scratch-database authority.
- RFC 0008 § Invariants: deployment and recovery boundaries remain explicit and
  fail closed.
