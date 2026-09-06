## MODIFIED Requirements

### Requirement: Write Permissions
The system SHALL enforce per-signal write permissions at the application
level for all non-DND context signals. Each signal type SHALL have a defined
set of authorized writer butlers, and the normal `set_context()` path SHALL
validate `(butler_name, signal_type)` against that mapping before writing.
Unauthorized non-DND writes SHALL raise `PermissionError`.

The permission mapping SHALL remain:
- `traveling`: travel, general
- `sleeping`: health, general
- `meeting`: general
- `focused`: general
- `exercising`: health
- `sick`: health, general
- `socializing`: relationship, general
- `commuting`: travel, general
- `at_home`: travel, home, general
- `in_space`: home, general
- `away`: general
- `dnd`: general, switchboard

`dnd` is the safety-critical exception to the otherwise application-enforced
model. General and Switchboard SHALL use the canonical DND mutation operation
instead of direct generic DML. The database SHALL reject direct DND DML even
when a runtime role retains the existing broad table grant required for
non-DND context writes. The ordinary non-DND mapping remains an application
authorization rule; this change does not turn it into a generic row-level
permission system.

The canonical DND operation SHALL enter through a pinned `SECURITY INVOKER`
gateway that checks `current_user` as the active General/Switchboard runtime
role before it calls the private pinned `SECURITY DEFINER` mutation operation.
The private operation SHALL independently check the active `SET ROLE` setting,
because its `current_user` is the NOLOGIN owner. Neither layer may trust a
caller-supplied writer, `session_user`, an absent role, or a shared-role
development fallback. A runtime role may mutate only its own DND row; a
cross-writer operation SHALL fail before it changes a row, guard, or receipt.

The DND database policy constrains writes only. Every butler retains the public
read path for active DND state and guarded snapshots; the implementation SHALL
not use a blanket RLS policy that hides DND rows from those readers.

#### Scenario: Authorized non-DND write remains application-authorized
- **WHEN** Health calls the normal context path with
  `butler_name="health"` and `signal_type="exercising"`
- **THEN** the application permission check and the non-DND database policy
  permit the write
- **AND** the DND guard is not read or advanced

#### Scenario: DND writer is routed through the guarded boundary
- **WHEN** General or Switchboard requests `signal_type="dnd"`
- **THEN** the request is routed to the canonical DND mutation operation
- **AND** it cannot complete through the ordinary generic row-upsert or clear
  path

#### Scenario: DND gateway rejects an active-role or writer mismatch
- **WHEN** a caller invokes the DND gateway without an active General or
  Switchboard runtime role, or asks it to mutate the other canonical writer's
  row
- **THEN** the gateway/private operation rejects before any DND row, guard, or
  mutation receipt changes

#### Scenario: Unauthorized writer remains rejected
- **WHEN** Finance calls the normal context path with
  `butler_name="finance"` and `signal_type="exercising"`
- **THEN** a `PermissionError` is raised before any context write

#### Scenario: Authorized butler writes signal
- **WHEN** the health butler calls `set_context(butler_name="health", signal_type="exercising", ...)`
- **THEN** the signal is written successfully

#### Scenario: Unauthorized butler rejected
- **WHEN** the finance butler calls `set_context(butler_name="finance", signal_type="exercising", ...)`
- **THEN** a `PermissionError` is raised with a message identifying the butler and signal type
- **AND** no database write occurs

#### Scenario: General butler has broad write access
- **WHEN** the general butler calls `set_context()` with any signal type
- **THEN** the write succeeds because general is authorized for all signal types
## ADDED Requirements

### Requirement: Canonical DND Mutation and Durable Generation
The system SHALL represent logical DND state through one singleton,
monotonically increasing context-bus guard in `public`. The guard SHALL carry a
non-negative `BIGINT` generation and SHALL be advanced exactly once for each
successful new canonical DND mutation. A logical DND state is the OR of active
RFC 0009 DND rows written by General and Switchboard; it SHALL NOT use a
per-writer row version as its admission fence.

The only canonical DND writers SHALL be General and Switchboard. General's
explicit-context `set_context` and `clear_context` paths, and any future
Switchboard DND path, SHALL route `signal_type="dnd"` through a single
versioned atomic mutation operation. The operation SHALL require an immutable
`mutation_id`, verified writer identity, operation, opaque correlation
reference, and complete DND payload. It SHALL lock the guard, validate
authorization, mutate only the caller's DND row, advance generation, and
persist a receipt/audit in one transaction. A committed observer SHALL never
see a DND row change without the corresponding advanced generation, or an
advanced generation without the corresponding row change.

`mutation_id` SHALL be created once from durable routed action/session/tool-call
evidence before the first mutation attempt. Retry paths SHALL carry it forward
unchanged; the implementation SHALL NOT derive it from wall-clock time, an
attempt count, or raw DND content. The durable receipt is the authoritative
replay result and SHALL retain no raw DND value or metadata.

The General MCP tool's relative `hours` convenience input SHALL remain limited
to non-DND signals. A DND action with a custom TTL SHALL provide a stable
absolute requested expiry from its routed action and preserve it for retry;
otherwise the tool SHALL reject before mutation. A null requested expiry means
the canonical database operation resolves the DND default exactly once.

The durable replay identity SHALL be `(mutation_id,
semantic_fingerprint_version, semantic_fingerprint)`. The private durable audit
SHALL retain `mutation_id`, generation, verified writer, affected writer row,
operation, opaque correlation reference, requested and effective expiry,
fingerprint version/digest, and commit timestamp. The returned canonical-writer
receipt SHALL retain the non-secret ordering/replay evidence it needs:
`mutation_id`, generation, verified writer, operation, opaque correlation,
requested/effective expiry, and commit timestamp. It SHALL NOT expose the
fingerprint/digest, raw DND value, or metadata. A set resolves its effective
expiry once from database time after the normal TTL default/max normalization;
both requested and effective expiry use canonical UTC PostgreSQL timestamp
precision. A clear stores null expiry fields and rejects set-only payload
fields.

`semantic_fingerprint` SHALL be SHA-256 over a versioned canonical document
that includes the protocol/signal, verified writer, affected row, operation,
opaque correlation, requested/effective expiry, effective confidence, and
canonicalized metadata. The optional DND value SHALL participate without being
stored: null and empty are distinct, and a present Unicode-NFC value is included
only through its SHA-256 digest. Metadata is likewise canonicalized and digested
before inclusion, so no raw optional value or metadata is retained in the
receipt/audit. On a replay lookup, the operation combines the retry's
normalized inputs with the persisted effective expiry before recomputing the
candidate fingerprint. This makes later retries deterministic even when a
default or clamp would otherwise move with database time.

Normalization renders requested/effective timestamps as stored UTC instants at
PostgreSQL microsecond precision and confidence as the validated stored `REAL`
binary32 representation. Canonical metadata JSON sorts Unicode-NFC object keys,
preserves array order, distinguishes null from empty values, uses finite
canonical numeric forms, and rejects unrepresentable input. The correlation is
a normalized opaque identifier, never a user-text surrogate.

The digest is an unkeyed content-minimizing replay identity, not a secret or
credential. The audit SHALL retain no raw optional value or metadata and SHALL
not be directly readable by runtime roles; only the canonical operation may
read it for deduplication and return a receipt to its canonical caller.
Snapshot/admission readers receive no digest or audit rows. A reader capable of
testing low-entropy candidate payloads against a digest is outside this narrow
non-disclosure guarantee, so the digest SHALL not be exposed to broad context
readers.

An exact replay SHALL return the original receipt without mutating DND or
generation. Any fingerprint mismatch, including changed requested/effective
expiry, optional value, confidence, metadata, writer, operation, or correlation,
SHALL fail closed as `idempotency_conflict`. Mutation receipts and audits SHALL
NOT copy raw user message text, optional DND value text, metadata, notification
content, or provider payloads.

If a persisted replay row lacks a fingerprint version/digest, has an
unsupported fingerprint version, or has expiry fields incompatible with its
operation (including a set without an effective expiry), the operation SHALL
reject the retry as `replay_identity_unprovable` before DND DML. It SHALL NOT
infer a replacement identity, return a guessed receipt, or apply a second
mutation.

#### Scenario: General DND set atomically advances the guard
- **WHEN** General submits a new correlated DND set with guard generation `N`
- **THEN** the committed context row is active and the durable receipt records
  generation `N + 1`
- **AND** no reader can observe either committed effect without the other

#### Scenario: Switchboard clear preserves another writer's logical DND
- **WHEN** General and Switchboard both have active DND rows and Switchboard
  clears only its own row
- **THEN** the guard advances exactly once and Switchboard's row is cleared
- **AND** a current DND snapshot remains active because General's row remains
  active

#### Scenario: Exact mutation replay is idempotent
- **WHEN** a canonical writer retries an already committed mutation with the
  same mutation ID and identical normalized semantic inputs
- **THEN** it receives the original generation and receipt, including the
  persisted effective expiry and fingerprint
- **AND** no second DND row mutation, audit row, or generation increment occurs

#### Scenario: Conflicting mutation replay fails closed
- **WHEN** a caller reuses a persisted mutation ID with a different writer,
  operation, correlation, requested/effective expiry, or semantic payload
- **THEN** the operation returns an idempotency-conflict error
- **AND** it does not change a DND row, guard generation, or prior receipt

#### Scenario: Changed expiry replay fails closed
- **WHEN** a caller reuses a committed DND-set mutation ID with a different
  requested expiry or a candidate effective expiry that differs from the
  persisted receipt
- **THEN** the operation returns `idempotency_conflict`
- **AND** the original context row, generation, audit, and receipt remain
  unchanged

#### Scenario: Changed optional value replay fails closed without disclosure
- **WHEN** a caller reuses a committed DND-set mutation ID but changes the
  optional value, including null versus empty, or changes confidence or metadata
- **THEN** the operation returns `idempotency_conflict`
- **AND** neither the audit nor receipt reveals the prior or attempted raw value
  or metadata

#### Scenario: Uncomparable replay fails closed after restart
- **WHEN** a retry finds a persisted mutation row with missing fingerprint
  version/digest, an unsupported fingerprint version, or expiry fields
  incompatible with its operation, including a set without an effective expiry
- **THEN** the operation returns `replay_identity_unprovable` before DND DML
- **AND** it does not infer a replacement identity or create a second mutation

#### Scenario: Concurrent canonical DND writers serialize
- **WHEN** General and Switchboard concurrently submit new DND mutations
- **THEN** their guard updates serialize into contiguous generations with no
  lost update
- **AND** each resulting receipt identifies the generation it committed

### Requirement: DND Snapshot and Serialized Admission
The system SHALL provide a durable DND snapshot containing `generation`,
`dnd_active`, `observed_at` from database time, and `revalidate_at` when a
time-based expiry can change the observed state. A snapshot is evidence only;
it SHALL NOT by itself authorize an external effect.

A consumer that needs to turn an inactive snapshot into a durable admission
SHALL revalidate it inside the same database transaction that writes the
consumer's own local admission record. It SHALL lock the singleton guard with a
share lock, require the captured generation to match, re-read canonical DND
state using database time, and require DND to be inactive while the lock is
held. Canonical writers SHALL take the conflicting update lock before changing
their DND row. The consumer SHALL commit or roll back its local durable effect
before releasing the guard lock; it SHALL NOT hold the lock across MCP or
provider I/O.

Health SHALL own its policy/wake decision and Messenger SHALL repeat this
guarded admission in the transaction that persists any future egress intent.
Switchboard SHALL carry versioned correlation through authenticated MCP packets
only; it SHALL NOT substitute a local SQL check for Health or Messenger, read a
peer queue, or gain a peer-schema grant.

#### Scenario: DND changes after snapshot and before admission
- **WHEN** a consumer captures inactive generation `N` and a canonical DND
  mutation commits generation `N + 1` before the consumer obtains the guard
  lock
- **THEN** the consumer rejects the stale snapshot before writing its durable
  admission record
- **AND** no external effect is authorized from that snapshot

#### Scenario: Local admission wins the guard ordering
- **WHEN** a consumer obtains the guard share lock for inactive generation `N`
  and writes its local durable admission record while a canonical DND writer
  races it
- **THEN** the writer waits until the consumer commits or rolls back
- **AND** a committed writer mutation advances the guard only after that local
  admission boundary

#### Scenario: Messenger performs the final DND check
- **WHEN** Health has supplied a captured DND generation to a future
  wake-recovery delivery path
- **THEN** Messenger revalidates that generation and inactive DND state in the
  transaction that persists its own egress intent
- **AND** a changed or unprovable generation prevents that intent from being
  created

#### Scenario: Admission evidence is unavailable
- **WHEN** the guard row, canonical DND rows, database time, or required
  generation evidence cannot be read or validated
- **THEN** the consumer fails closed and records no durable admission or
  external effect

### Requirement: DND Expiry, Restart, and Generation Failure Closure
An active DND snapshot SHALL set `revalidate_at` to the earliest active DND
expiry calculated with database time. Consumers SHALL not rely on active
snapshot evidence at or after that timestamp; they SHALL re-read under the
guard. Expiry itself SHALL NOT require a background writer or generation bump,
because it only transitions an active row to inactive and a fresh guarded read
observes that state. Any later canonical DND set, refresh, reactivation, or
explicit clear SHALL be a mutation that advances generation.

The guard and mutation audit SHALL be the only recovery source after restart.
No in-memory cache, reconstructed client timestamp, or replayed connector event
shall be authoritative. The generation SHALL never wrap, reset, or be reused.
If a mutation would exceed the `BIGINT` maximum, the operation SHALL fail
before changing DND state, record an operator-visible failure, and require an
explicit migration/recovery procedure.

#### Scenario: Active DND expires without a writer
- **WHEN** an active DND snapshot reaches its `revalidate_at` time without a
  canonical mutation
- **THEN** a consumer rejects the cached active evidence and performs a fresh
  guarded read using database time
- **AND** the fresh read excludes the expired DND row

#### Scenario: DND refresh and reactivation each advance the guard
- **WHEN** a canonical writer refreshes an active DND row and later sets it
  again after clear or expiry, using a new mutation ID for each request
- **THEN** every successful refresh or reactivation persists its normalized
  effective expiry and advances generation exactly once
- **AND** passive expiry alone does not advance generation

#### Scenario: Restart reconstructs committed DND state
- **WHEN** a process restarts after a DND mutation commits
- **THEN** it reconstructs the generation and replay receipt from durable guard
  and mutation records plus canonical context rows
- **AND** it does not infer ordering from process memory or client timestamps

#### Scenario: Generation exhaustion fails closed
- **WHEN** the next canonical DND mutation would exceed the guard's `BIGINT`
  maximum
- **THEN** the operation fails before changing context state or emitting a new
  successful receipt
- **AND** it does not wrap, reset, or reuse the generation
