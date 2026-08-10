## ADDED Requirements

### Requirement: Runtime-Probe Replay Receipt Is Switchboard-Owned

The core migration SHALL create a durable
`public.runtime_probe_control_receipts` relation that contains only a
Switchboard receipt timestamp, key ID, capability expiry, fixed audience, and
SHA-256 nonce digest. It SHALL have a unique constraint on the fixed audience
and nonce digest, SHALL NOT retain a raw nonce or signature, and SHALL retain a
receipt through at least the capability expiry plus the configured five-second
clock-skew allowance. Switchboard SHALL atomically insert and commit that
receipt after successful capability verification but before catalog lookup,
runtime launch, or verification persistence. Only Switchboard's runtime role
may create, inspect for replay, or remove expired receipt rows; ordinary
butler, connector, and dashboard runtime roles receive no direct receipt-table
grant. Expired-row cleanup SHALL not remove a row before its retention bound.

ID: REQ-database-security-008
Source: heart-and-soul/security-and-secrets.md; RFC 0003; core-credentials REQ-core-credentials-002; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Concurrent replay receipt claim permits one control execution

- **WHEN** two valid control requests with the same nonce reach Switchboard
  concurrently
- **THEN** exactly one unique receipt insert succeeds and commits before any
  catalog lookup, runtime launch, or verification persistence
- **AND** the other request is rejected as a replay with no side effect

#### Scenario: Receipt retention cannot reopen a valid replay window

- **WHEN** a cleanup worker considers a receipt whose capability is within its
  accepted expiry plus five-second skew window
- **THEN** it retains the receipt
- **AND** it deletes only rows whose retention bound has elapsed

#### Scenario: Receipt material and access remain narrow

- **WHEN** a non-Switchboard runtime, connector, or dashboard role attempts to
  query, insert, update, or delete a receipt
- **THEN** PostgreSQL rejects the direct operation
- **AND** no persisted row contains a raw nonce or capability signature

### Requirement: Runtime Attention Outbox Least-Privilege Authorization

The core migration SHALL grant the public runtime-attention outbox only the
minimum authority needed for its ownership model. Butler runtime roles that
produce breaker or fleet-halt edges SHALL have no raw `INSERT`, `SELECT`,
`UPDATE`, or `DELETE` permission on outbox rows. They may only `EXECUTE` a
narrow fixed-search-path `SECURITY DEFINER` producer operation that derives the
safe payload, immutable source key, and deduplication key from a validated
qualifying dispatch attempt or fleet-halt evidence. The operation SHALL reject
caller-controlled recipient, payload, delivery state, source key, and arbitrary
deduplication data. The migration SHALL `REVOKE EXECUTE` on every such function
from `PUBLIC` before it grants `EXECUTE` only to the designated model-breaker
and fleet-halt producer runtime roles. Switchboard alone receives the required
select/update authority to claim and transition episodes through the external
delivery boundary. The dashboard operator surface SHALL receive sanitized read
data through its API without granting ordinary runtime roles access to other
producers' episode payloads.

The current shared-login plus `SET ROLE` database topology does not provide an
unforgeable per-runtime principal, so this requirement SHALL NOT claim database
enforcement that a caller belongs to a particular butler. Its database boundary
enforces server-derived attention integrity and effective-role grants; trusted
runtime application code remains responsible for binding a normalized dispatch
outcome to its butler/session. A future adversarial-component boundary requires
independently authenticated runtime principals without peer `SET ROLE` ability.

ID: REQ-database-security-007
Source: heart-and-soul/security-and-secrets.md; RFC 0003; RFC 0006; database-security Public Schema Write Authorization Matrix; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Runtime producer has only validated append authority

- **WHEN** a butler runtime operating under its `SET ROLE` records a
  qualifying breaker or fleet-halt edge
- **THEN** it can invoke the authorized producer operation that appends the
  corresponding outbox episode and its own dispatch provenance in one
  transaction
- **AND** direct `INSERT`, `SELECT`, `UPDATE`, and `DELETE` attempts against
  the outbox are rejected for that role

#### Scenario: Producer cannot forge arbitrary attention content or a non-edge source

- **WHEN** a runtime caller supplies a recipient, lifecycle state, payload,
  triggering key, or fleet window that is not server-derived from a qualifying
  dispatch attempt or fleet-halt edge
- **THEN** the producer operation rejects the call without appending an
  episode
- **AND** it cannot create a Switchboard-pageable attention record with
  arbitrary content or a fabricated edge

#### Scenario: Public and non-producer roles cannot execute producer operations

- **WHEN** `PUBLIC`, `connector_writer`, or a runtime role not designated for
  the relevant model-breaker or fleet-halt producer calls the
  `SECURITY DEFINER` producer operation
- **THEN** PostgreSQL rejects the call before it can inspect source evidence or
  append an episode
- **AND** only the explicitly granted designated producer roles can execute it
- **AND** the test asserts the effective `SET ROLE` grant boundary rather than
  falsely treating the shared-login topology as independently authenticated
  per-runtime identity

#### Scenario: Switchboard can claim without peer-schema access

- **WHEN** Switchboard operates under its runtime role to process pending
  runtime-attention episodes
- **THEN** it can select and transition public outbox rows required for its
  durable claim and delivery lifecycle
- **AND** it gains no read or write grant to a producer's private schema

#### Scenario: Unrelated runtime and connector roles cannot inspect episodes

- **WHEN** a non-producing runtime role or `connector_writer` attempts to read
  or mutate runtime-attention episodes
- **THEN** the database rejects the attempt
- **AND** the migration does not grant a shared privileged DSN or broad
  cross-schema access as a workaround
