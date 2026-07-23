## Context

`public.user_context` is deliberately a shared-awareness exception to the
normal schema boundary. Its current `dnd` rows are useful for ordinary
pull-based suppression, but their row-level upsert/clear API is not a
linearizable safety boundary. A caller can read "DND inactive", then a
canonical writer can set DND before a later wake-recovery admission or durable
egress intent. The current API has no durable version, replay key, mutation
receipt, or lock which can prove that the later admission still refers to the
same DND state.

The affected owners are intentionally narrow:

| Concern | Owner | Boundary |
| --- | --- | --- |
| Explicit DND state | Context bus in `public` | Shared-awareness data only |
| Canonical DND writers | General and Switchboard | Existing RFC 0009 permission matrix |
| DND generation / serialization row | Context bus core in `public` | Migration-managed, durable guard |
| Wake-policy sleep validation | Health | Its own MCP surface and schema |
| Final delivery admission | Messenger | Its own schema and durable egress record |
| Cross-butler coordination | Switchboard | Authenticated MCP only |

Health therefore reads a public context-bus guard; it does not read General or
Switchboard private tables. General does not call Health directly, and
Switchboard remains the sole cross-butler coordinator. The shared guard is not
a second context store or a general distributed-lock facility: it exists only
to serialize canonical `dnd` mutations with a consumer's own durable admission
record.

## Goals / Non-Goals

**Goals:**

- Give every successful canonical DND mutation one durable, monotonically
  increasing generation that an admission consumer can compare later.
- Make General and Switchboard DND writes atomic with guard advancement,
  replay-safe, authorized, observable without message content, and safe under
  concurrent writers.
- Give Health and Messenger a precise shared-row locking protocol that prevents
  a DND mutation from passing invisibly between a snapshot and a durable
  admission.
- Define failure-closed handling for missing state, malformed records, clock
  uncertainty, stale snapshots, retries, restart, and generation exhaustion.
- Preserve RFC 0009's TTL and pull-based context model without adding a
  private Health mirror or a direct peer database read.

**Non-Goals:**

- No owner-Telegram wake release, cohort preparation, Health sleep clear,
  Scheduler cancellation admission, Messenger egress intent, or provider call.
- No change to DND's user-facing policy, TTL limits, ordinary notification
  suppression, context preambles, or non-DND signal behavior.
- No new cross-butler direct client, schema grant to a peer schema, shared DSN,
  or generic lock service.
- No live-data rewrite or backfill of existing `public.user_context` rows.
- No claim of external/provider exactly-once delivery.

## Decisions

### D1 — Use one public, singleton DND generation guard

The future core migration creates a singleton `public.dnd_generation_guard`
row. Its durable minimum shape is:

| Field | Meaning |
| --- | --- |
| `guard_id` | Fixed singleton identity (`1`) with a check constraint |
| `generation` | Non-negative `BIGINT` monotonic counter; never reset or reused |
| `updated_at` | Database-clock mutation timestamp |

It also creates an append-only `public.dnd_generation_mutations` audit keyed by
`mutation_id`. Each row records the resulting generation, effective writer
identity, operation (`set` or `clear`), an opaque stable correlation reference,
the affected context writer identity, `requested_expires_at`,
`effective_expires_at`, `semantic_fingerprint_version`,
`semantic_fingerprint`, and commit timestamps. `requested_expires_at` and
`effective_expires_at` are null for a clear. The receipt returned to a caller
contains the same durable identity fields that it needs to identify a retry:
`mutation_id`, generation, effective writer, operation, opaque correlation,
requested and effective expiry, fingerprint version/digest, and commit time.
It MUST NOT store raw Telegram text, the optional DND `value`, a metadata
document, or a copied notification payload.

The audit is not a snapshot/admission reader surface. Runtime roles receive no
direct `SELECT` or write privilege on it; the canonical operation alone reads
it for deduplication and returns a receipt only to the calling canonical writer.
Health and Messenger receive snapshot/admission fields, never replay identity
or audit rows.

`dnd` is a logical OR over active RFC 0009 DND rows from General and
Switchboard. A guard generation represents the complete logical DND state at
the commit boundary, not one writer's row version. Updating General's DND while
Switchboard's DND remains active still advances the one global generation.

**Why this instead of a Health-private counter?** A private Health counter
would require every canonical writer to call Health or write Health state,
which violates the MCP-only and schema-isolation boundary. A public
context-bus guard is the existing shared-awareness authority and is readable by
Health without expanding its privileges.

**Why this instead of a version column on each `user_context` row?** Multiple
authorized writers can hold DND concurrently. Per-row versions cannot express
one logical DND state or serialize an admission against both rows without a
separate aggregate fence.

### D2 — Route every canonical DND mutation through one atomic operation

The eventual implementation exposes one core-owned DND mutation operation;
the exact Python helper name is an implementation detail, but its versioned
wire/receipt shape is `context.dnd.mutate.v1`. Generic `set_context()` and
`clear_context()` dispatch to it whenever `signal_type == "dnd"`; non-DND
signals retain their existing paths.

The only canonical callers are:

| Caller | Allowed operations | Required correlation source |
| --- | --- | --- |
| General explicit-context MCP tool | Set and clear its own DND row | Routed request ID when available, otherwise the durable session/tool-call ID |
| Switchboard deterministic/accepted-event path | Set and clear its own DND row | Accepted ingestion event ID / request ID |

There is no current Switchboard DND call site. Its RFC 0009 authorization is a
reserved canonical path, not permission to perform raw SQL; a future path must
use this operation and carry accepted-event correlation before it becomes live.
Health, Messenger, connectors, and every other butler cannot mutate DND.

The request contains an immutable `mutation_id`, writer, operation, affected
`set_by_butler`, opaque correlation reference, and the complete DND payload. A
retry MUST reuse the same `mutation_id` and payload. The operation has one
privacy-preserving semantic identity, defined as SHA-256 over a versioned,
canonical UTF-8 document containing:

- `context.dnd.mutate.v1`, `dnd`, the verified effective writer, affected
  writer row, operation, and opaque correlation reference;
- the normalized requested expiry and the persisted effective expiry, each in
  UTC at PostgreSQL timestamp precision; and
- for a set, the effective confidence and canonical JSON metadata plus a
  tagged optional-value digest. The optional DND value **does participate**:
  `null` and an empty value are distinct, and a present value is Unicode-NFC
  normalized before its SHA-256 digest is included. Metadata is canonicalized
  before it is digested, so neither raw value nor raw metadata lands in the
  audit or receipt. A clear rejects set-only payload fields and fingerprints
  their canonical null form.

Timestamp normalization renders the stored `TIMESTAMPTZ` instant in UTC to
microsecond precision. Confidence is the validated PostgreSQL `REAL` value
rendered by its canonical binary32 representation. Canonical JSON recursively
sorts object keys after Unicode-NFC normalization, preserves array order,
distinguishes null from an empty object/value, uses canonical finite numeric
representations, and rejects non-finite or unrepresentable inputs. The opaque
correlation reference is a normalized identifier, never a user-text surrogate.

This is an unkeyed, content-minimizing SHA-256 identity, not a secrecy
primitive. The privacy boundary is no raw semantic payload in the durable audit
plus the narrow audit ACL above; an actor already permitted to read the digest
and test low-entropy candidate values could make guesses. The operation must
therefore never expose the digest to broad context readers or treat it as a
credential.

For a new set, one database timestamp inside the mutation transaction applies
the existing TTL default/max policy and produces `effective_expires_at`; the
stored value, not a client wall clock, is normalized for the fingerprint. For
a replay lookup, the operation combines the retry's normalized semantic inputs
with the already-persisted effective expiry before recomputing the candidate
fingerprint. This prevents a later retry from silently changing a clamped or
defaulted expiry merely because database time advanced.

The operation executes in one database transaction:

1. lock the singleton guard with `SELECT ... FOR UPDATE`;
2. look up `mutation_id` in the durable mutation audit;
3. on an exact replay, return the original receipt without changing a context
   row or generation; on a fingerprint mismatch, including a changed requested
   or effective expiry or changed semantic payload, reject
   `idempotency_conflict`;
4. validate writer authorization and perform the DND upsert/clear for that
   writer only;
5. advance `generation` exactly once, persist the mutation receipt, and commit.

No committed observer can see a changed DND row without its new generation, or
the new generation without its corresponding row mutation. Any error, including
audit failure or counter exhaustion, rolls back the whole operation.

An audit row that lacks its fingerprint version/digest, uses an unsupported
fingerprint version, or has expiry fields incompatible with its operation
(including a set without an effective expiry) is not comparable after restart.
The operation MUST reject that replay before DND DML as
`replay_identity_unprovable`; it must not infer an identity, return a guessed
receipt, or apply a second mutation.

The core migration must preserve the existing table-level runtime grants for
ordinary non-DND context writes while making direct DND DML impossible for
runtime roles. It does this with `FORCE ROW LEVEL SECURITY` policies that allow
the existing runtime roles to write only `signal_type <> 'dnd'`, including
blocking updates that cross the DND boundary. A narrowly owned,
`SECURITY DEFINER` canonical operation is the only DND exception: it runs with
a pinned search path, is executable only by the General and Switchboard runtime
roles, validates the active `SET ROLE` identity rather than a caller-supplied
writer, and is the only role permitted by the DND RLS policy. The migration
must revoke `EXECUTE` from `PUBLIC`, grant no runtime role direct guard/audit
write privilege, and preserve the application-level per-signal permission check
for non-DND rows. This is a database security property, not a Python
convention.

The RLS policies are write-specific: every butler retains the RFC 0009 public
read path for DND snapshots, while `INSERT`, `UPDATE`, and `DELETE` are the
only commands constrained by the DND row predicate. A blanket `FOR ALL` policy
that hides DND rows from snapshot/admission readers is forbidden.

### D3 — A snapshot is evidence, not an authorization to send

`context.dnd.snapshot.v1` returns a durable observation:

```text
generation: bigint
dnd_active: boolean
observed_at: database-clock timestamp
revalidate_at: database-clock timestamp or null
```

The snapshot derives `dnd_active` from every active canonical DND row using the
same `superseded_at IS NULL AND expires_at > database_now()` predicate as RFC
0009. If DND is active, `revalidate_at` is the earliest active DND expiry,
because that is the next time the logical state can change without a writer
transaction. If inactive, it is null: any future activation must use the
guarded mutation and therefore advance `generation`.

An inactive snapshot is useful only as an input to a later durable admission.
It never authorizes provider egress by itself. Snapshot consumers must treat
missing guard rows, a non-integer/negative generation, unavailable database
time, or an expired `revalidate_at` as unprovable and fail closed.

**Why not use an in-memory generation cache?** A daemon restart, process split,
or worker retry would lose it. The value must be durable and readable from the
shared context boundary.

### D4 — Serialize admission with the same guard row through the local durable effect

The future admission helper is `context.dnd.admit.v1`. It MUST be called on an
already-acquired connection inside the consumer's own transaction, not as an
independent preflight RPC. The consumer:

1. locks `public.dnd_generation_guard` with `SELECT ... FOR SHARE`;
2. re-reads canonical active DND state using database time;
3. requires the captured generation, inactive state, and snapshot validity to
   remain provable; and
4. while that share lock is held, writes its *own* durable admission record
   (for example, a future Messenger egress intent) and commits.

Canonical writers always take `FOR UPDATE` on the same guard before touching a
DND row. PostgreSQL's conflicting row locks establish the required ordering:

- if a writer commits first, generation changes and the consumer rejects stale
  evidence before its durable effect;
- if admission obtains the share lock first, the writer waits until the local
  admission record commits or rolls back; and
- a consumer never treats a DND change as invisible between its check and the
  record it uses to authorize downstream work.

Health owns the policy/wake decision that consumes snapshots. Messenger owns
the final egress admission and therefore must repeat the guarded check in the
transaction that persists its own egress intent. Switchboard only carries the
captured generation and correlation through authenticated MCP packets; it
cannot SQL-read an origin queue or substitute a local check for a receiver's
admission.

This contract intentionally stops at durable admission. A DND mutation that
commits after Messenger's durable admission cannot retract an already-admitted
effect; it invalidates every later/retry admission. The wake-recovery protocol
must define the provider send-start marker separately.

### D5 — Treat TTL expiry as a database-time revalidation boundary

Expiry is not a writer mutation and does not advance the generation by itself.
That is safe because it can only change a currently active DND row to inactive,
and an active snapshot is never a positive admission authorization. Consumers
MUST use database time and MUST re-read active DND state while holding the
admission guard lock; they cannot cache an active result past `revalidate_at`.

An expired row remains audit history exactly as RFC 0009 requires. A later
set, refresh, reactivation, or explicit clear is a canonical mutation and
advances generation. The mutation receipt, rather than client wall clock,
provides replay and ordering evidence.

### D6 — Fail closed rather than wrapping or recovering by inference

`generation` uses a signed `BIGINT`. If it would exceed its maximum, the
mutation fails before the DND row changes, records an operator-visible failure,
and requires an explicit migration/recovery procedure. It MUST NOT wrap,
reset, or silently reuse a generation.

Process restart recovers exclusively from `dnd_generation_guard`, canonical
context rows, and the mutation audit, including the persisted fingerprint and
effective expiry. No daemon cache, replayed connector update, or reconstructed
timestamp is authoritative. A replay with an existing `mutation_id` returns its
persisted receipt only when its candidate fingerprint matches; a new mutation
with a later correlation receives the next guard generation. If durable
evidence is missing or inconsistent, the mutation/admission rejects rather than
guessing the state.

### D7 — Keep this prerequisite independent from wake and cancellation work

This change defines a reusable DND guard only. It does not add a Health MCP
tool, Messenger table, Switchboard run, Scheduler handoff, or a provider send.
The strict owner-Telegram wake-recovery change may consume this contract only
after the guard implementation and its behavior-executing tests merge. The
separate durable precommit-cancellation admission prerequisite owns the
Scheduler/Messenger cancellation state machine and must not be folded into this
change.

## Contract-Test Matrix

The implementation PR must add behavior-executing contract and PostgreSQL
integration tests. Source inspection alone is insufficient because the safety
property is transactional.

| Case | Setup / action | Required proof |
| --- | --- | --- |
| General set | Generation `N`; General sets DND with fresh mutation ID | Committed row is active, receipt/audit is correlated, generation is `N+1`, and no separate committed state is observable. |
| Switchboard clear | Switchboard owns an active DND row and clears it | Only its row changes; generation advances exactly once; another active writer still makes logical DND active. |
| Duplicate replay | Repeat an identical mutation ID and normalized semantic payload after a successful set/clear | Original generation/receipt, including the persisted effective expiry and fingerprint, is returned; no second audit row or generation advance occurs. |
| Changed-expiry replay | Reuse a set mutation ID with a different requested expiry, or a candidate whose normalized effective expiry differs from the receipt | Rejects `idempotency_conflict`; context, guard, audit, and prior receipt remain unchanged. |
| Changed-payload replay | Reuse a set mutation ID with a changed optional value, confidence, or metadata, including `null` versus empty value | Rejects `idempotency_conflict`; the privacy-preserving audit exposes no raw changed payload. |
| Uncomparable replay | Restart with an audit row missing fingerprint version/digest, using an unsupported version, or carrying expiry fields incompatible with its operation (such as a set with no effective expiry) | Rejects `replay_identity_unprovable` before DND DML; it neither guesses an identity nor applies a second mutation. |
| Conflicting replay | Reuse mutation ID with different writer, operation, or correlation identity | Rejects `idempotency_conflict`; context and guard remain unchanged. |
| Concurrent writers | General and Switchboard mutate DND concurrently | Results serialize to contiguous generations with no lost update or partial audit. |
| Refresh / reactivation | A canonical writer refreshes an active DND row, then sets it again after clear or expiry, each with a new mutation ID | Each successful refresh/reactivation writes its normalized effective expiry and advances generation exactly once; expiry alone still does not advance it. |
| Stale snapshot | Capture inactive `N`, then a writer commits `N+1` before admission | Guarded admission rejects before its local durable effect. |
| Admission wins | Admission holds `FOR SHARE` and persists its local effect while a writer races | Writer blocks until commit/rollback; the committed effect is tied to `N`, then writer produces `N+1`. |
| DND before egress | A DND mutation wins before future Messenger durable admission | Messenger admission rejects; no egress intent/provider call is created. |
| TTL expiry | Capture active DND, advance database clock beyond `revalidate_at` without a write | Cached evidence is rejected; a fresh guarded read sees inactive state using database time. |
| Restart / audit recovery | Restart after committed mutation and after rolled-back mutation | Committed state reconstructs from tables; rollback leaves no receipt, row change, or generation advance. |
| Authorized non-DND path | A non-owner test session executes `SET ROLE butler_health_rw` or another real runtime role and writes an authorized non-DND signal through the normal context path | The normal application-level permission check and non-DND RLS policy permit the write; the test is not run as the migration owner or a privileged setup session. |
| Unauthorized DND path | Health, Messenger, connector, or direct generic DND DML from a real runtime role attempts mutation | RLS/ACL denies the operation before any DND row/guard/audit mutation; a missing or unverifiable `SET ROLE` makes canonical DND mutation and DND-based admission fail closed. |
| Counter exhaustion | Seed guard at `BIGINT` maximum and attempt a new mutation | Fails closed; no wrap, no context mutation, and an observable error is produced. |

## Risks / Trade-offs

- **[One shared guard serializes all DND writes]** → DND is rare and safety
  critical; the singleton makes the logical OR and admission ordering explicit.
  It is not used for non-DND context signals.
- **[A durable mutation audit adds retained metadata]** → Store only IDs,
  writer/operation, generation, normalized expiry, and a digest; exclude raw
  user text, optional value, metadata, and notification content. The unkeyed
  digest is content minimization rather than a secrecy primitive, so direct
  audit read stays restricted to the canonical operation. Retention must
  preserve any replay window required by active recovery work.
- **[Holding a row lock through local durable admission can increase latency]**
  → The critical section contains only public revalidation plus the consumer's
  local database write. It never holds the lock through an MCP call or provider
  request; timeout/rollback fails closed.
- **[Existing development environments may lack SET ROLE enforcement]** → The
  ordinary non-DND context path keeps the existing development fallback, but
  the guarded DND operation and any DND-based admission must fail closed when
  they cannot establish the active runtime role, RLS/ACL boundary, and atomic
  guard. Tests may use a migration owner only through an explicit test-only
  setup, never as the runtime role whose privilege is being proved.
- **[TTL expiry does not increment a counter]** → `revalidate_at` and
  database-time guarded rechecks make time-based state changes explicit without
  adding a background expiry writer or a second clock.

## Migration Plan

This documentation PR performs no migration. The follow-on implementation must:

1. add a guarded public singleton and replay audit in a new core migration,
   with `to_regclass`/column guards suitable for core-only databases;
2. seed generation `0` without rewriting existing context rows;
3. preserve the existing broad `public.user_context` runtime grants for
   non-DND rows, then enforce DND-only RLS/ACL restrictions, a pinned
   `SECURITY DEFINER` mutation operation, and minimum execute/select
   privileges for canonical writers and admission readers while denying direct
   runtime access to the replay audit;
4. route generic context-bus DND set/clear calls through the atomic operation;
5. add the contract/integration tests in this design, including role-enforced
   non-DND regression and refresh/reactivation coverage, then require the strict
   wake-recovery implementation to consume the finalized helper;
6. deploy the guard before enabling any Health/Messenger wake admission; and
7. roll back only before a consumer depends on it. Once a durable mutation or
   admission record references a generation, rollback requires a planned,
   audited migration rather than dropping/reseeding the counter.

## Open Questions

None block the contract. The eventual SQL function and Python helper names are
implementation details, but they must preserve the versioned request/receipt,
row-lock ordering, and least-privilege properties defined above.
