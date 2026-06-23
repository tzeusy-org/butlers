# relationship-facts Specification

## Purpose
TBD - created by archiving change relationship-tabs-to-entities. Update Purpose after archive.
## Requirements
### Requirement: Relationship entity facts triple store

The relationship butler SHALL own a single triple-store table `relationship.entity_facts` that
serves as the canonical RDF (subject-predicate-object) registry for both relational
(`knows`, `family-of`, `partner-of`, `co-attended`, `colleague-of`, ...) and contact
(`has-email`, `has-phone`, `has-handle`, `has-address`, `has-birthday`, `has-website`)
predicates. This table **supersedes** RFC 0004 §3 ("Contacts and Contact Info") as the
canonical channel-identity registry.

**Schema location:** `relationship` schema (NOT `public`). Cross-butler reads go through
Switchboard / MCP, consistent with RFC 0006 schema isolation. A single triple table avoids
the dual-write trap of putting contact-triples in `public` and relational-triples in
`relationship`.

**Single table for both predicate families.** Contact-facts and relational-facts live in
ONE `relationship.entity_facts` table (NOT two). Rationale: RDF purity (subject-predicate-object
is the contract); identical column shape across predicate families; query simplicity
(`SELECT * FROM relationship.entity_facts WHERE subject = $1`); storage cost is identical;
resolves the Phase 1 Amendment 1.1 open question.

**Schema:**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `subject` | UUID NOT NULL | FK to `public.entities(id)` |
| `predicate` | TEXT NOT NULL | From `relationship.entity_predicate_registry` |
| `object` | TEXT NOT NULL | Literal value (for `has-*` predicates) or `entity_id::text` (for relational predicates) |
| `object_kind` | TEXT NOT NULL | `'literal'` or `'entity'`; informs how to interpret `object` |
| `src` | TEXT NOT NULL | Authoring butler |
| `conf` | FLOAT NOT NULL DEFAULT 1.0 | 0..1 |
| `last_seen` | TIMESTAMPTZ NULL | |
| `weight` | INT NULL | Relational aggregation weight |
| `verified` | BOOL NOT NULL DEFAULT false | Owner-confirmed |
| `primary` | BOOL NULL | Primary-of-kind for multi-valued contact preds |
| `validity` | TEXT NOT NULL DEFAULT 'active' | `active \| retracted \| superseded` |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

**Indexes (required):**
- `(subject, predicate)` — primary access pattern
- `(predicate, object) WHERE object_kind = 'literal'` — reverse-lookup for ingestion
  routing (e.g. "incoming Telegram chat 12345 → which entity")
- `(predicate) WHERE validity = 'active'` — Concentration aggregation
- `(last_seen DESC)` — stale detection, Finder tie-break
- `(subject) WHERE validity = 'active' AND predicate LIKE 'has-%'` — contacts endpoint

**Uniqueness:** `UNIQUE (subject, predicate, object) WHERE validity = 'active'`.

**Schema boundary with `memory.facts` (R2 #3):** the table is `relationship.entity_facts`
(schema-qualified). A separate `memory.facts` table exists under the memory butler schema
per RFC 0006 (`src/butlers/modules/memory/migrations/001_memory_schema.py:106`); the two
tables are isolated by schema and MUST NOT be cross-joined. Migration beads and all SQL
authored under this change MUST reference the schema-qualified name `relationship.entity_facts`
throughout — never bare `facts`.

#### Scenario: Triple store accepts contact and relational predicates in one table
- **WHEN** `relationship_assert_fact()` is called with a contact predicate (`has-email`,
  `object_kind='literal'`) and separately with a relational predicate (`knows`,
  `object_kind='entity'`) for the same subject
- **THEN** both rows MUST land in `relationship.entity_facts` with `validity='active'`
- **AND** `SELECT * FROM relationship.entity_facts WHERE subject = $1` MUST return both rows
- **AND** no cross-join to `memory.facts` MUST be required to materialize the result

#### Scenario: Schema-qualified name is enforced
- **WHEN** any migration bead or production SQL references the table without the
  `relationship.` schema prefix
- **THEN** code review MUST reject the change as a schema-boundary violation
- **AND** the SQL MUST be rewritten to use `relationship.entity_facts` explicitly

### Requirement: Predicate catalog

The set of valid predicates SHALL live in `relationship.entity_predicate_registry` (table seeded by
Alembic migration). Predicates MUST be grouped into families:

- **Contact predicates** (`object_kind='literal'`): `has-email`, `has-phone`, `has-handle`,
  `has-address`, `has-birthday`, `has-website`.
- **Relational predicates** (`object_kind='entity'`): `knows`, `family-of`, `partner-of`,
  `parent-of`, `child-of`, `colleague-of`, `friend-of`, `co-attended`, `purchased-from`,
  `subscribed-to`, `visited` (set extensible).
- **Override predicates** (`object_kind='literal'`, JSON): `dunbar_tier_override` (per
  RFC 0013 weight-at-query decision and Phase 1 Amendment 6).

No predicate ID MAY be hardcoded outside `entity-model.ts` (frontend) and
`relationship.entity_predicate_registry` (backend); resolves Brief §1 hard-don't.

#### Scenario: Unknown predicate is rejected by the central writer
- **WHEN** `relationship_assert_fact()` is called with `predicate='has-feet'` (not in
  `relationship.entity_predicate_registry`)
- **THEN** the writer MUST raise a validation error before any DB write
- **AND** no row MUST land in `relationship.entity_facts`

#### Scenario: Predicate IDs are not hardcoded in component tree
- **WHEN** ripgrep is run for known predicate string literals (e.g. `'has-email'`,
  `'knows'`) across `frontend/src/components/relationship/`,
  `frontend/src/pages/entities/`, and `roster/relationship/api/`
- **THEN** the only allowed matches MUST be inside `frontend/src/lib/entity-model.ts`
  (frontend) or seed data for `relationship.entity_predicate_registry` (backend)

### Requirement: Central writer — `relationship_assert_fact()`

ALL writes into `relationship.entity_facts` MUST go through a single MCP tool
`relationship_assert_fact(subject, predicate, object, *, src, conf, weight, primary,
verified, object_kind)` exposed by the relationship butler. No butler MAY issue a direct
`INSERT INTO relationship.entity_facts` or `UPDATE relationship.entity_facts` from outside the
relationship butler's schema role.

The central writer is responsible for:
- Predicate validation against `relationship.entity_predicate_registry`.
- Dedup (`ON CONFLICT (subject, predicate, object) WHERE validity='active' DO UPDATE`).
- Provenance enforcement (every triple has `src`, `conf`, `verified`).
- Supersession on update (mark prior row `validity='superseded'`, insert new row).

**Transaction-safety (Amendment 14, binding):** `relationship_assert_fact()` MUST be safe
to call from within an open `asyncpg` transaction. It MUST NOT require its own outer
transaction wrapper, MUST NOT open a nested transaction that would deadlock on the existing
connection, and MUST NOT panic when invoked from a caller that already holds a pool
connection. **Idempotency (Amendment 14, binding):** the writer MUST be idempotent on
`(subject, predicate, object)` — repeated calls with identical identity arguments produce
exactly one active row, not duplicates; supersession semantics apply when `(src, conf,
verified, lastSeen)` differ across calls.

**Owner-gate carry-forward (RFC 0017, binding):** when `subject` resolves to the owner
entity, `relationship_assert_fact()` MUST NOT write the triple directly; instead it MUST
emit a `pending_action` for owner approval, mirroring the existing pattern in
`roster/relationship/tools/contact_info.py::contact_info_add` per RFC 0017 §2.3. The owner
approves the pending action via the existing approval ceremony; only after approval does
the triple land as `validity='active'`. Non-owner subjects are written directly without
the approval hop.

This single-ingress contract preserves RFC 0006 schema isolation and RDF integrity.

#### Scenario: Direct SQL writes are blocked
- **WHEN** any butler other than relationship attempts `INSERT INTO relationship.entity_facts`
- **THEN** PostgreSQL MUST reject the statement on role permissions
- **AND** the only successful write path MUST be `relationship_assert_fact()` via MCP

### Requirement: Switchboard `resolve_contact_by_channel()` re-points to triples

The Switchboard's `resolve_contact_by_channel()` function (defined in RFC 0004 §"resolve_contact_by_channel()", `rfcs/0004:83-95`) SHALL be re-implemented to query `relationship.entity_facts` and MUST stop reading `public.contact_info` after Migration bead 7 (read-path cut-over). The reimplementation MUST use the following SQL shape:

```sql
SELECT f.subject AS entity_id,
       e.canonical_name AS name,
       COALESCE(e.roles, '{}') AS roles
FROM relationship.entity_facts f
JOIN public.entities e ON e.id = f.subject
WHERE f.predicate = $1                                  -- e.g. 'has-handle' or 'has-email'
  AND f.object   = $2                                   -- channel-specific identifier
  AND f.object_kind = 'literal'
  AND f.validity = 'active';
```

The channel-type → predicate mapping is:
- `telegram` → `has-handle` (object value = `telegram:<chat_id>`)
- `email` → `has-email`
- `discord` → `has-handle` (object = `discord:<user_id>`)
- `phone` → `has-phone`

The `ResolvedContact` dataclass (RFC 0004:97-104) loses the `contact_id` field; the new
return shape carries `entity_id` (which serves the same routing purpose). The identity
preamble format (RFC 0004:119-132) is updated to drop `contact_id` and reference only
`entity_id`.

#### Scenario: Telegram chat resolves to entity via has-handle triple
- **WHEN** an incoming Telegram message arrives with chat_id `12345` and a triple
  `(subject=ent-7, predicate='has-handle', object='telegram:12345',
  object_kind='literal', validity='active')` exists in `relationship.entity_facts`
- **THEN** `resolve_contact_by_channel('telegram', 'telegram:12345')` MUST return a
  `ResolvedContact` with `entity_id = ent-7`
- **AND** the returned shape MUST NOT include a `contact_id` field

#### Scenario: Unknown channel value returns no match
- **WHEN** `resolve_contact_by_channel('email', 'nobody@example.com')` is called and no
  triple with predicate `has-email` and object `nobody@example.com` exists
- **THEN** the function MUST return `None` (or equivalent absent-match sentinel)
- **AND** the identity preamble builder MUST treat the caller as unknown

### Requirement: Migration safety — dual-write, parity, cut-over

The contacts → triple-store migration MUST follow the 10-step protocol enumerated in
Phase 1 Amendment 1.1.C (see `docs/redesigns/2026-05-17-entity-brief.md` §6b Amendment 1.1)
and tracked as 10 verification beads in the beads graph (NOT duplicated as tasks here —
see this change's tasks.md §11 for cross-refs). The protocol MUST guarantee:

1. **Pre-migration snapshot** of `public.contacts` and `public.contact_info` to
   `public.contacts_pre_migration_<YYYYMMDD>` and
   `public.contact_info_pre_migration_<YYYYMMDD>`.
2. **Backfill** triples from existing `public.contact_info` rows via the central writer.
3. **Dual-write period** (minimum 7 days): every existing writer to `public.contact_info`
   ALSO calls `relationship_assert_fact()` via MCP, gated by a feature flag.
4. **Read-path cut-over**: Switchboard `resolve_contact_by_channel()` queries triples after
   24h of zero parity drift.
5. **Write-path cut-over**: dual-write shims removed; `public.contact_info` becomes read-only
   for 30 days.
6. **Drop**: `public.contact_info` and `public.contacts` dropped after the 30-day soak +
   explicit operator sign-off.

The deprecation timeline is BINDING; deviation requires a new OpenSpec change.

**Dual-write reconciliation contract (Amendment 14, binding).** Cross-system atomicity
between the SQL legacy write (`public.contact_info`) and the MCP triple write
(`relationship_assert_fact()`) is NOT guaranteed. Without an explicit reconciliation
contract, dual-write has silent failure modes (SQL commits, MCP fails → triple missing;
SQL commits, MCP duplicates → racing supersession). The migration spec encodes:

- **SQL is authoritative during the dual-write window.** Existing legacy writes commit
  unchanged; the MCP `relationship_assert_fact()` call is invoked **post-commit, best-effort**.
- **Reconciler job (new).** A periodic worker (interval ≤ 1h during the dual-write window)
  sweeps `public.contact_info` rows lacking a matching active triple in `relationship.entity_facts`
  and emits them via the central writer. The reconciler is idempotent on
  `(subject, predicate, object)`.
- **Registry-drift exception.** If the reconciler's static `contact_info.type` mapping resolves
  to a predicate that is absent from `relationship.entity_predicate_registry`, the reconciler
  MUST NOT call the central writer for those rows. It MUST count them under
  `rows_skipped_no_predicate`, log the missing predicate once per run, and leave the rows
  unreconciled until the registry seed/migration drift is repaired. A non-zero
  `rows_skipped_no_predicate` count is not a successful zero-drift state.
- **Parity tests are eventual, not synchronous.** Migration bead 6 ("parity tests") asserts
  24h-window reconciliation, NOT write-time synchrony.
- **Central writer safety** (cross-ref to Requirement: Central writer): safe to call from
  within an open asyncpg transaction; idempotent on `(subject, predicate, object)`.

#### Scenario: Dual-write window keeps SQL authoritative
- **WHEN** a legacy writer inserts a row into `public.contact_info` during the dual-write
  window AND the post-commit `relationship_assert_fact()` call fails
- **THEN** the SQL commit MUST stand (no rollback)
- **AND** the reconciler job MUST emit the missing triple on its next sweep (≤ 1h later)
- **AND** parity tests MUST eventually report zero drift within the 24h window

#### Scenario: Read-path cut-over only after 24h zero-drift
- **WHEN** the Switchboard read path is flipped to `relationship.entity_facts` (Migration bead 7)
- **THEN** parity tests over the prior 24h window MUST report zero drift
- **AND** the cut-over MUST be reversible by re-pointing reads back to `public.contact_info`
  for the duration of the 30-day soak

#### Scenario: Drop is gated by 30-day soak + operator sign-off
- **WHEN** Migration bead 10 (`DROP TABLE public.contact_info`) is dispatched
- **THEN** at least 30 calendar days MUST have elapsed since Migration bead 8 (write-path
  cut-over)
- **AND** an explicit operator sign-off MUST be recorded in the migration report
- **AND** without sign-off the DROP MUST NOT execute

### Requirement: Credentials carve-out

`public.contact_info` rows where `secured = true` (RFC 0004:54) SHALL be treated as
**credentials** and MUST NOT be migrated to `relationship.entity_facts` as triples. They either
(a) remain in a renamed `public.contact_info_credentials` sub-table or (b) move to
`relationship.credentials` (separate non-triple table).

**Phase 2 decision:** option (b) — move to `relationship.credentials` co-located with
relationship butler ownership; the relationship butler is the canonical writer of credentials
already, per `roster/relationship/butler.toml:106-114`.

#### Scenario: Secured rows are skipped during triple backfill
- **WHEN** the Migration bead 5 backfill job iterates `public.contact_info` and encounters
  a row with `secured = true`
- **THEN** the backfill MUST skip the row (no triple emitted in `relationship.entity_facts`)
- **AND** the backfill report MUST tally skipped-credential count distinctly from
  triple-emitted count

> **Deferred copy — known gap (bu-l6bb0):** The row is NOT copied to `relationship.credentials`
> during bead 5. `relationship.credentials` does not exist at migration time (table DDL ships in
> bead 10.4 / bu-uj3xv). The copy of secured rows from the pre-migration snapshot into
> `relationship.credentials` is intentionally deferred to a follow-up bead to be created
> post bu-uj3xv. Until that bead lands, `secured=true` rows remain accessible via
> `public.contact_info` (authoritative until bead 8, read-only until bead 10) and the
> pre-migration `public.contact_info_pre_migration_<YYYYMMDD>` snapshot, but are NOT yet
> present in `relationship.credentials`.

#### Scenario: Credentials are not surfaced on entity contacts endpoint
- **WHEN** `GET /api/butlers/relationship/entities/{id}/contacts` is called for an entity
  that has both a non-secured `has-email` triple and a secured credential
- **THEN** the response MUST include the non-secured email
- **AND** the response MUST NOT include the credential row from `relationship.credentials`

### Requirement: Orphan contact handling

`public.contact_info` rows whose `public.contacts.entity_id IS NULL` (orphan contacts) MUST
be resolved before backfill. Resolution policy (Phase 2 decision, resolves Amendment 1.1.A.3
as updated 2026-05-18 by Phase 1 R-pass):

**Orphan resolution runs as a post-migration Python script**, NOT as an Alembic migration
step and NOT via an MCP call. The hint in earlier drafts to invoke `memory_entity_create()`
is unreachable: Alembic migrations run in a pre-daemon role with no event loop, no MCP
client, and no switchboard, so calling an MCP tool from the migration is impossible. The
script lives at `src/butlers/scripts/contact_orphan_resolver.py` with the following
contract:

1. Default `--apply=false` (dry-run). The operator MUST pass `--apply` explicitly to perform
   any writes; without it, the script enumerates orphan rows and prints the proposed
   resolution plan only.
2. The script reads orphan rows from the `public.contacts_pre_migration_<YYYYMMDD>`
   snapshot table (created by Migration bead 1) — NOT live `public.contacts`.
3. For each orphan, the script either (a) mints an entity row directly via SQL (carve-out
   from the "no direct SQL outside relationship butler" rule, justified by migration-time
   context where the relationship butler may not be live), or (b) emits a `notify()` to the
   owner for manual resolution. The policy choice is per-row deterministic based on the
   orphan's data shape (presence of canonical-name signal, etc.).
4. The script backfills `public.contacts.entity_id` for any orphan it mints an entity for.
5. The script records the resolution outcome in
   `docs/reports/contact-migration-orphans-<YYYY-MM-DD>.md`, including per-row decision
   and the count of orphans resolved vs. escalated.

The script is itself a Migration bead — sequenced between Migration bead 5 (backfill) and
Migration bead 6 (parity tests) and tracked as Migration bead **5.5** in this change's
tasks.md §11. The pre-migration baseline report MUST enumerate orphan count; the
orphan-resolver report MUST enumerate post-resolution count.

#### Scenario: Dry-run is the default
- **WHEN** `contact_orphan_resolver.py` is invoked without `--apply`
- **THEN** the script MUST NOT write to `public.entities`, `public.contacts`, or
  `relationship.entity_facts`
- **AND** the script MUST emit the proposed resolution plan to stdout / the report file
- **AND** the operator MUST be required to pass `--apply` explicitly for any writes

#### Scenario: Orphan with canonical-name signal mints an entity
- **WHEN** the resolver finds an orphan row in
  `public.contacts_pre_migration_<YYYYMMDD>` with a non-empty canonical-name signal
- **AND** the operator passes `--apply`
- **THEN** the script MUST mint a new row in `public.entities` via direct SQL
- **AND** the script MUST backfill `public.contacts.entity_id` for the orphan
- **AND** the per-row decision MUST be recorded in
  `docs/reports/contact-migration-orphans-<YYYY-MM-DD>.md`

#### Scenario: Orphan without signal escalates to owner
- **WHEN** the resolver finds an orphan row with no canonical-name signal
- **THEN** the script MUST emit a `notify()` to the owner describing the row
- **AND** the script MUST NOT mint an entity for that row
- **AND** the escalation MUST be recorded in the report with status `escalated`

### Requirement: `verified` is a column, not a triple

The `verified` field SHALL be a **column on `relationship.entity_facts`** and MUST NOT be modeled
as a separate verification-triple (`(triple_id, verified-by, owner)`). Rationale (resolves
Amendment 1 open question): pure RDF would split it out, but every triple needs a verified
flag, the verifying actor is always the owner (single-user v1), and the column form keeps
query plans simple. If v2 introduces multi-actor verification, the column MAY be promoted
to a separate verification table at that point.

#### Scenario: Verifying a fact updates the column, not a new row
- **WHEN** the owner confirms an existing triple `(subject, predicate, object)` via the
  approval ceremony
- **THEN** the existing row in `relationship.entity_facts` MUST be updated to set `verified = true`
- **AND** no new `(triple_id, verified-by, owner)` row MUST be inserted
- **AND** the row's `validity` MUST remain `'active'`

#### Scenario: No verification-triple predicate is registered
- **WHEN** `relationship.entity_predicate_registry` is queried for all seeded predicates
- **THEN** no predicate named `verified-by` (or analogue) MUST appear in the registry
- **AND** any attempt to assert such a predicate MUST be rejected by the central writer
  per Requirement: Predicate catalog

### Requirement: Extraction emits structured edges from relational prose

The `fact-extraction` skill SHALL emit a registry-relational edge whenever extracted prose asserts a *standing* relationship between the subject and a nameable entity (partner/spouse, parent/child, sibling, friend, colleague, employer/membership): it resolves-or-creates the object entity and asserts the edge via `relationship_assert_fact(object_kind='entity')`, in addition to any narrative fact — rather than recording the relationship only as free-text. Episodic mentions (one-off events, coordination) remain narrative and MUST NOT produce an edge.

#### Scenario: Standing relationship in prose produces an edge
- **WHEN** prose asserts a durable relationship to a resolvable entity (e.g. "cohabiting partner with Chloe Wong")
- **THEN** the skill MUST resolve-or-create the object entity
- **AND** assert the registry-relational edge (e.g. `partner-of`) via the central writer
- **AND** an owner-subject edge MUST route through the carve-out for approval

#### Scenario: Episodic prose does not produce an edge
- **WHEN** prose describes a one-off event (e.g. "planned dinner with X", "coordinated a move with Y")
- **THEN** the skill MUST store it as a narrative fact only
- **AND** MUST NOT assert a registry-relational edge

### Requirement: Inferred relationship facts pass a confidence gate

An inferred relationship fact MUST carry a confidence value and provenance, and an inferred **family** relationship below the confidence bar MUST be proposed for confirmation rather than written as an active fact. ("Inferred" means derived by the system rather than stated directly by the owner.)

#### Scenario: Low-confidence inferred family fact is not written active
- **WHEN** extraction infers a family relationship (e.g. "has a son") without direct owner confirmation and below the confidence bar
- **THEN** it MUST NOT be stored as an active fact
- **AND** it MUST be surfaced for owner confirmation before becoming active

#### Scenario: Inferred fact records provenance
- **WHEN** any relationship fact is stored from inference
- **THEN** it MUST record its confidence and the source it was inferred from

### Requirement: Re-home and backfill must not retract a parked write

Any re-home or backfill path MUST inspect the central writer's outcome and retract the source memory edge-fact only when the write committed an active row. When `relationship_assert_fact()` returns `pending_approval` (the owner carve-out parked the write), the source memory fact MUST be left active so the edge is never lost between stores. This corrects the backfill behavior specified in `relational-edges-single-home`.

#### Scenario: Parked owner write leaves the source intact
- **WHEN** the backfill re-homes an edge whose subject is the owner and `relationship_assert_fact()` returns `pending_approval`
- **THEN** the source memory edge-fact MUST remain `validity='active'`
- **AND** the summary MUST count it as parked, distinct from migrated

#### Scenario: Committed write retracts the source
- **WHEN** the backfill re-homes an edge and `relationship_assert_fact()` commits an active row
- **THEN** the source memory edge-fact MUST be retracted exactly once
