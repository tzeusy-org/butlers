## ADDED Requirements

### Requirement: Filtered-Event Artifact Restore Verification Evidence

The artifact-bound filtered-event manifest and checker SHALL have hermetic
contract fixtures and integration evidence against a real PostgreSQL
testcontainer. The real-PostgreSQL lane SHALL exercise the producer's exported-
snapshot capture, real `pg_dump`/gzip artifact, real scratch restore, partition
catalogs, exact counts, checker, and cleanup. Mocked subprocesses or JSON-only
fixtures SHALL NOT substitute for that end-to-end database evidence.

ID: REQ-testing-032
Source: Non-Negotiable Rule 4; craft-and-care/testing-and-verification.md § New Feature; deployment-hardening REQ-deployment-hardening-008 and REQ-deployment-hardening-009
Scope: v1-mandatory

#### Scenario: Hermetic manifest fixtures cover the strict boundary

- **WHEN** manifest validation is tested without a database
- **THEN** fixtures cover a valid minimal manifest and missing, malformed,
  duplicate-key, unknown-field, unsupported-version, incomplete-capture,
  noncanonical-digest, impossible-count, invalid-status-map, invalid-month-name,
  invalid-bound, stale, and cross-artifact inputs
- **AND** only the valid manifest can produce trusted expectations

#### Scenario: Hermetic checker fixtures cover every structural mismatch

- **WHEN** checker behavior is exercised through a deterministic catalog/count
  adapter
- **THEN** fixtures cover wrong or missing parent relkind/strategy/key, missing
  and extra attached children, unattached name matches, an attached same-name
  child in a foreign schema, child-kind mismatch, lower/upper-bound mismatch,
  row-count mismatch, optional status mismatch, unknown status, and query/parse
  failure
- **AND** every mismatch produces one fixed failure reason and no partial pass

#### Scenario: Real PostgreSQL proves same-snapshot artifact parity

- **WHEN** an integration fixture creates the actual range-partitioned parent and
  month children, seeds rows, exports one repeatable-read snapshot, captures the
  manifest, and supplies that snapshot to real `pg_dump`
- **THEN** the compressed artifact restores through real PostgreSQL client tools
  and the checker passes exact parent, child-set, bounds, and count parity
- **AND** the fixture confirms the named scratch database is absent after the
  protected lifecycle completes

#### Scenario: Concurrent capture changes cannot create mixed evidence

- **WHEN** the real-PostgreSQL fixture commits an insert, replay-status change,
  backfill, attachment, or detach after the producer acquires its snapshot
- **THEN** the published dump and manifest both reflect the pre-change snapshot,
  or production fails without a completed pair
- **AND** no test accepts a dump from one database state and counts or partition
  inventory from another

#### Scenario: Real PostgreSQL rejects a foreign-schema same-name child

- **WHEN** a real-PostgreSQL fixture attaches a month partition whose relation
  name matches `filtered_events_YYYYMM` but whose schema is not `connectors`
- **THEN** producer capture fails without publishing a complete pair
- **AND** a scratch fixture with that attachment fails the checker by fully
  qualified child identity rather than accepting the matching relation name

#### Scenario: Cross-artifact and partial restore failures are proven

- **WHEN** the fixture swaps two valid manifests, removes or truncates a
  manifest, restores only part of an artifact, omits a child, or changes a
  restored child bound or count
- **THEN** the checker fails with the applicable closed reason and the overall
  drill reports a verify-stage integrity failure
- **AND** neither generic non-system-table presence nor later live counts can
  turn the failure into a pass

#### Scenario: Content-blindness is witnessed end to end

- **WHEN** fixtures seed unique sentinel values in payload, preview, sender and
  connector identities, filter reason, external IDs, and error detail
- **THEN** none appears in the manifest, scoped verdict, protected/public result
  projection, logs, metrics, or test failure diagnostics
- **AND** exact child and optional status counts still verify

#### Scenario: Protected executor authority remains enforced

- **WHEN** integration/configuration fixtures exercise executor and ordinary
  runtime identities
- **THEN** only the protected executor can run the scratch checker and persist
  its authoritative scoped result
- **AND** dashboard/shared roles cannot create the scratch database, read the
  backup body, supply a verdict, or obtain the executor secret
- **AND** no test requires or performs a restore against the live application
  database

#### Scenario: Trusted-bootstrap transition is proven against PostgreSQL

- **WHEN** real-PostgreSQL fixtures exercise a clean installation, the exact
  finalized predecessor upgrade, privileged bootstrap rerun, interrupted and
  migration retry, an untrusted lookalike, and a later schema replay
- **THEN** only the bootstrap-owned installer/finalizer may create or transition
  the protected table/functions, and every spoof or wrong-order migration fails
  closed before authority handoff
- **AND** the final catalog proves exact columns/constraints, function
  signatures, owners, SECURITY DEFINER/search-path settings, trigger absence,
  role flags, memberships, and ACLs without duplicate rows or widened grants

#### Scenario: Attempt binding survives crash and retry boundaries

- **WHEN** fixtures interrupt the executor before and after authoritative
  persistence, retry the attempt, and record adjacent attempts for different
  artifact/manifest pairs
- **THEN** the ledger contains either no row or one complete constrained row for
  each attempt, never a partial scope/overall result
- **AND** no previous scope pass, digest, or capture timestamp can satisfy or be
  associated with a later attempt's overall result

### Source References

- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): deterministic
  infrastructure must be testable, debuggable, and predictable.
- `about/craft-and-care/testing-and-verification.md` § New Feature: verify a
  feature at the layer where its behavior is defined.
- `openspec/specs/testing/spec.md` § PostgreSQL Testcontainer Infrastructure:
  integration tests use disposable PostgreSQL containers for real DB evidence.
- `deployment-hardening` REQ-deployment-hardening-008 and
  REQ-deployment-hardening-009: producer/checker guarantees that the fixtures
  must prove.
- `database-security` REQ-database-security-009: trusted transition, atomic
  attempt binding, and exact ACL guarantees that the fixtures must prove.
