# relationship-facts Specification

## Purpose
Defines `relationship.entity_facts`, the relationship butler's single RDF (subject-predicate-object) triple store that is the canonical registry for both relational predicates (`knows`, `family-of`, `partner-of`, `co-attended`, ...) and contact predicates (`has-email`, `has-phone`, `has-handle`, `has-address`, ...). It supersedes RFC 0004 §3 as the channel-identity registry, lives in the `relationship` schema (cross-butler reads go through Switchboard/MCP per RFC 0006 isolation), and holds both predicate families in one table to preserve RDF purity and avoid a dual-write split. The spec fixes the table schema, required indexes and active-fact uniqueness, the predicate catalog, the central `relationship_assert_fact()` writer, the Switchboard `resolve_contact_by_channel()` re-point onto triples, the dual-write/parity/cut-over migration path, the credentials carve-out and orphan-contact handling, and the confidence-gated extraction of inferred edges from relational prose.

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
| `observed_at` | TIMESTAMPTZ NULL | When the fact was actually observed, distinct from assertion time (`created_at`). Added by migration `rel_021_entity_v3_lifecycle`; backfilled by `scripts/backfill_entity_fact_observed_at.py`. Immutable on supersession. |
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
(schema-qualified). A separate `memory.facts` table exists under the memory module schema
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
  `subscribed-to`, `visited`, `works-at`, `member-of`, `manages-property` (set extensible).
  `works-at` and `member-of` are person→organization edges. `manages-property` (subject person
  manages an object place or organization) is an owner-approved catalog predicate seeded by
  migration `rel_026` (`bu-60pwv6.35`).
- **Override predicates** (`object_kind='literal'`, JSON): `dunbar_tier_override` (per
  RFC 0013 weight-at-query decision and Phase 1 Amendment 6).

Predicate names in the registry are **hyphenated** (`friend-of`, not `friend_of`). The set is
extended ONLY by adding seed rows to `relationship.entity_predicate_registry`; no predicate ID
MAY be hardcoded outside `entity-model.ts` (frontend) and the registry seed (backend).

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

#### Scenario: Person-to-organization edges are registered as relational
- **WHEN** `relationship.entity_predicate_registry` is queried for the relational family
- **THEN** `works-at` and `member-of` MUST be present with `object_kind='entity'`
- **AND** `relationship_assert_fact(predicate='works-at', object_kind='entity', ...)` MUST
  insert an active row in `relationship.entity_facts`

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
emit a `pending_action` for owner approval through the central writer's owner carve-out
(`roster/relationship/tools/relationship_assert_fact.py::_create_pending_action`, inherited by
`roster/relationship/tools/channel.py::channel_add`) per RFC 0017 §2.3. The owner
approves the pending action via the existing approval ceremony; only after approval does
the triple land as `validity='active'`. Non-owner subjects are written directly without
the approval hop.

**Owner-gate trusted-source exemption (as built):** the owner gate has a
trusted-source carve-out (`roster/relationship/tools/relationship_assert_fact.py::_OWNER_AUTO_APPLY_SOURCES`,
checked in `_assert_on_conn`).
When `src` is an owner-self source (`"owner-bootstrap"` from daemon startup, or
`"owner-self"` from owner-setup tools) or a trusted internal-derivation source
(`"interaction_sync"`), an owner-subject write is auto-applied directly instead of being
parked for approval (`_OWNER_AUTO_APPLY_SOURCES`). These source strings are server-set: the
MCP tool wrapper hardcodes `src` and the dashboard API rejects the trusted values via a
Pydantic validator, so external callers (LLM sessions, HTTP) cannot spoof them. This lets
the daemon self-register owner identity handles and lets structured-data jobs derive owner
facts without a human approval hop.

This single-ingress contract preserves RFC 0006 schema isolation and RDF integrity.

#### Scenario: Direct SQL writes are blocked
- **WHEN** any butler other than relationship attempts `INSERT INTO relationship.entity_facts`
- **THEN** PostgreSQL MUST reject the statement on role permissions
- **AND** the only successful write path MUST be `relationship_assert_fact()` via MCP

### Requirement: Switchboard `resolve_contact_by_channel()` re-points to triples

The Switchboard's `resolve_contact_by_channel()` function (defined in RFC 0004 §"resolve_contact_by_channel()", `rfcs/0004:83-95`) SHALL query `relationship.entity_facts` (the dropped `public.contact_info` table is never read). The query MUST use the following SQL shape:

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

The `ResolvedContact` return shape carries `entity_id` as the routing key in place of the
retired `contact_id`, and the identity preamble references only `entity_id`.

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

### Requirement: Credentials carve-out

Secured contact details (credentials such as encrypted session blobs, OAuth tokens, or
secret-store pointers) SHALL live in the non-triple `relationship.credentials` table and MUST
NOT be stored in `relationship.entity_facts` as triples. `relationship.credentials` is keyed by
`entity_id`, holds at most one active (non-revoked) credential per `(entity_id, type)`, and is
writable only by the relationship butler; other butlers reach credentials only through the
relationship butler's tool surface.

#### Scenario: Credentials are never stored as triples
- **WHEN** a secured credential is recorded for an entity
- **THEN** it MUST be written to `relationship.credentials`
- **AND** no triple carrying the credential value MUST exist in `relationship.entity_facts`

#### Scenario: Credentials are not surfaced on entity contacts endpoint
- **WHEN** `GET /api/butlers/relationship/entities/{id}/contacts` is called for an entity
  that has both a non-secured `has-email` triple and a secured credential
- **THEN** the response MUST include the non-secured email
- **AND** the response MUST NOT include the credential row from `relationship.credentials`

### Requirement: `verified` is a column, not a triple

The `verified` field SHALL be a **column on `relationship.entity_facts`** and MUST NOT be modeled
as a separate verification-triple (`(triple_id, verified-by, owner)`). Rationale: pure RDF would split it out, but every triple needs a verified
flag, the verifying actor is always the owner (single-user v1), and the column form keeps
query plans simple. If v2 introduces multi-actor verification, the column MAY be promoted
to a separate verification table at that point.
- verified=true SHALL describe genuine admitted owner-class assertion, real executor owner-confirmation or protected dashboard confirmation. Caller booleans SHALL not confer it. The same row SHALL record server-derived confirmation authority/entity/time/origin, retaining its original content_authority and immutable authority_original_entity_id, with authority_entity_id reflecting only live availability. Any new confirmation identity reference SHALL preserve immutable original confirmation provenance without introducing an authorized-companion-delete veto; author availability SHALL not imply a new confirmation or authentication. Existing raw legacy verified values SHALL remain available as legacy data but SHALL not be displayed as newly proven owner confirmation. Confirmation SHALL preserve confidence, evidence, occurrence and effective packet.

ID: REQ-relationship-facts-003
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; Relationship MANIFESTO.md; adopted dashboard-owner-auth and existing capability contract
Scope: v1-mandatory

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

#### Scenario: Owner confirmation retains the original reporter

- **WHEN** an authenticated owner confirms a third-party report through the actual protected operation
- **THEN** the same surviving fact SHALL carry valid owner confirmation
- **AND** original authority/author, confidence and effective packet SHALL remain unchanged

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
- As built in `roster/relationship/tools/relationship_assert_fact.py` (`_FAMILY_GATE_PREDICATES`, `_FAMILY_GATE_CONF`), the gated family predicates are `parent-of`, `child-of`, and `family-of` (`_FAMILY_GATE_PREDICATES`), and the confidence bar is `_FAMILY_GATE_CONF = 0.8`. A call asserting one of those predicates with `conf < 0.8` is routed to `pending_approval` rather than written active.
- For parent-of, child-of and family-of, third-party/mixed or otherwise unconfirmed routed authority SHALL require owner confirmation regardless of caller confidence, including0.95. Confidence cannot elevate content authority. Existing low-confidence inference confirmation and provenance rules remain; real admitted owner-class and actual approved execution positives retain current owner-subject/exemption policy.

ID: REQ-relationship-facts-004
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; Relationship MANIFESTO.md; adopted dashboard-owner-auth and existing capability contract
Scope: v1-mandatory

#### Scenario: Low-confidence inferred family fact is not written active
- **WHEN** extraction infers a family relationship (e.g. "has a son") without direct owner confirmation and below the confidence bar
- **THEN** it MUST NOT be stored as an active fact
- **AND** it MUST be surfaced for owner confirmation before becoming active

#### Scenario: Inferred fact records provenance
- **WHEN** any relationship fact is stored from inference
- **THEN** it MUST record its confidence and the source it was inferred from

#### Scenario: High confidence does not elevate a third-party family claim

- **WHEN** a third-party parent-of/child-of/family-of assertion supplies conf=0.95
- **THEN** it SHALL park one matching pending action and create no active fact or edge
- **AND** an actual owner-admitted or executor-approved companion SHALL remain possible under existing policy

### Requirement: Re-home and backfill must not retract a parked write

Any re-home or backfill path MUST inspect the central writer's outcome and retract the source memory edge-fact only when the write committed an active row. When `relationship_assert_fact()` returns `pending_approval` (the owner carve-out parked the write), the source memory fact MUST be left active so the edge is never lost between stores. This corrects the backfill behavior specified in `relational-edges-single-home`.

#### Scenario: Parked owner write leaves the source intact
- **WHEN** the backfill re-homes an edge whose subject is the owner and `relationship_assert_fact()` returns `pending_approval`
- **THEN** the source memory edge-fact MUST remain `validity='active'`
- **AND** the summary MUST count it as parked, distinct from migrated

#### Scenario: Committed write retracts the source
- **WHEN** the backfill re-homes an edge and `relationship_assert_fact()` commits an active row
- **THEN** the source memory edge-fact MUST be retracted exactly once

### Requirement: prefers-channel predicate

The `relationship.entity_facts` store SHALL support a `prefers-channel`
predicate expressing a contact's preferred outbound channel. It is seeded into
`relationship.entity_predicate_registry` with `object_kind='literal'` and
`cardinality='single'`. The `object` is a channel name (e.g. `"telegram"`,
`"email"`, `"discord"`). It MAY name any channel, including channels `notify()`
cannot yet deliver.

#### Scenario: Assert a preferred channel
- **WHEN** a `prefers-channel` fact is asserted for an entity that has a contact
  fact for that channel (e.g. an active `has-handle` `telegram:…` for
  `object="telegram"`)
- **THEN** an active `prefers-channel` triple is stored for that entity with the
  channel name as `object`

#### Scenario: Preference is single-valued (supersession)
- **WHEN** an entity already has an active `prefers-channel` fact and a new
  `prefers-channel` fact is asserted for the same entity
- **THEN** the prior triple is marked `validity='superseded'`
- **AND** exactly one active `prefers-channel` triple remains for that entity

#### Scenario: Clearing the preference
- **WHEN** the preferred channel is cleared for an entity
- **THEN** the active `prefers-channel` triple is marked `validity='retracted'`
- **AND** the entity has no active `prefers-channel` triple

#### Scenario: Reject preference for an unreachable channel
- **WHEN** a `prefers-channel` fact is asserted for a channel the entity has no
  corresponding contact fact for (no `has-handle`/`has-email`/`has-phone` of that
  channel family)
- **THEN** the assertion is rejected with an error naming the missing contact fact

### Requirement: Fact writes persist an immutable typed evidence packet

Every write that makes a row in `relationship.entity_facts` active SHALL persist
the writer's cited evidence into `relationship.fact_evidence` on the same
connection and inside the same transaction as the fact row, so a fact can never
become active with its justification missing and a rolled-back write SHALL leave
no evidence behind.

An evidence row SHALL be a typed reference — `fact`, `entity`, `url`, or `text`
— carrying a `ref`, a `note`, and the assertion's `src`, `origin`, `session_id`,
and `action_id`. `ref` and `note` SHALL each be bounded at 512 characters, both
in the writer and as a database CHECK constraint, so the ledger is structurally
incapable of holding a copy of a source message, document, or transcript. A
packet SHALL carry at most 32 references.

Evidence rows SHALL be append-only: a BEFORE UPDATE trigger SHALL reject any
in-place rewrite. Re-citing a `(kind, ref)` already recorded for the same fact
SHALL be absorbed rather than duplicated. On supersession the replacement row
SHALL receive copies of the superseded row's evidence tagged with
`carried_from`, and the superseded row SHALL keep its own evidence unchanged.

Per-assertion provenance SHALL additionally be stamped on the fact row itself as
`assert_origin` (`direct` or `approved`), `assert_session_id`, and
`assert_action_id`, so a fact asserted with no cited evidence still records who
asserted it and from which session. A runtime session identifier that is not a
UUID SHALL be recorded as unknown rather than failing the write.

#### Scenario: Direct write records evidence and provenance atomically

- **WHEN** a caller asserts a fact with typed evidence
- **THEN** the fact row and its evidence rows are visible together
- **AND** the fact row records `assert_origin='direct'` and the session that
  authored it
- **AND** no evidence row carries a copy of the source content

#### Scenario: Rolled-back write leaves no evidence

- **WHEN** an assert runs inside a transaction that is rolled back
- **THEN** neither the fact row nor any evidence row for it exists

#### Scenario: Evidence rows cannot be rewritten

- **WHEN** an UPDATE is issued against a `relationship.fact_evidence` row
- **THEN** the write is rejected as an integrity-constraint violation

#### Scenario: Supersession carries the prior justification forward

- **WHEN** a re-assertion from a different source supersedes an active fact
- **THEN** the new active row carries copies of the prior row's evidence tagged
  with `carried_from`
- **AND** the superseded row's own evidence rows are unchanged

#### Scenario: Over-long reference is rejected before any write

- **WHEN** a caller cites a reference longer than the character bound
- **THEN** the assert raises before writing the fact or any evidence row

### Requirement: Predicate reads report an explicit composable coverage state

A read of a predicate for a subject SHALL report exactly one of `present`,
`absent_proven`, `unknown`, or `unavailable`, composed from the subject's
availability, the count of active facts for the predicate, and the coverage
receipts recorded in `relationship.fact_coverage`.

`relationship.fact_coverage` SHALL record, per `(subject, predicate, src)`, the
most recent outcome that source observed when it looked: `present` (it found a
value), `absent` (it looked and there was nothing), or `unavailable` (it could
not be consulted at all). A successful assert is itself an observation and SHALL
write a `present` receipt in the same transaction as the fact. A receipt whose
observation time precedes the stored one SHALL NOT overwrite it, so an
out-of-order replay cannot rewind coverage.

Composition SHALL be total and SHALL follow these rules: an unavailable subject
composes to `unavailable`; otherwise at least one active fact composes to
`present`; otherwise at least one `absent` receipt composes to `absent_proven`;
otherwise receipts that are all `unavailable` compose to `unavailable`; and
everything else composes to `unknown`.

**Missing coverage SHALL always compose to `unknown`.** No configuration, source
allowlist, or freshness heuristic may upgrade "we never looked" into "it is not
there"; only an explicit `absent` receipt may do that. A subject that is missing
from `public.entities` or tombstoned by a merge SHALL be reported as
`unavailable` rather than absent, because its predicate reads are unanswerable
rather than empty.

A read SHALL return the per-source receipts backing each state alongside the
state, so a caller can see why a read is proven absent rather than taking the
verdict on faith.

#### Scenario: Never-observed predicate is unknown, not absent

- **WHEN** a predicate has no active facts and no coverage receipts
- **THEN** the read reports `unknown`

#### Scenario: Observed-and-empty predicate is proven absent

- **WHEN** a source records an `absent` receipt for a predicate with no active
  facts
- **THEN** the read reports `absent_proven`
- **AND** the read includes that source's receipt

#### Scenario: Unconsultable sources do not prove absence

- **WHEN** every receipt for a predicate with no active facts is `unavailable`
- **THEN** the read reports `unavailable`, not `absent_proven`

#### Scenario: Merged-away subject is unavailable

- **WHEN** the subject entity is tombstoned by a merge
- **THEN** every predicate for it reports `unavailable`

#### Scenario: Stale replay does not rewind coverage

- **WHEN** a receipt is recorded with an observation time older than the stored
  receipt for the same `(subject, predicate, src)`
- **THEN** the stored outcome and observation time are unchanged

### Requirement: Relationship owns entity-merge coordination

Relationship SHALL be the sole authority that validates and tombstones an
entity merge, rewires its canonical relationship facts, opens the per-schema
rebind cohort, and emits `entity.rebound.v1`. It SHALL NOT update narrative
facts or association tables in another butler schema. Those references SHALL
be rebound by that schema's own daemon from the durable pending receipt. A
running daemon SHALL react to the emitted event, while startup replay SHALL
recover missed delivery. Each daemon SHALL establish its listener before
startup replay and SHALL re-establish the listener plus replay pending receipts
after a retained-listener connection failure. Relationship's own receipt SHALL
remain pending until its local narrative facts and association tables have
been rebound.

#### Scenario: Local repoint failure is visible

- **WHEN** a daemon receives a rebind and one of its local writes raises an error other than an absent optional table
- **THEN** its receipt MUST become `failed`
- **AND** the receipt MUST record the exception class
- **AND** the merge result MUST name the failed schema

#### Scenario: An optional local table is absent

- **WHEN** a daemon attempts a rebind and an optional reference table is undefined
- **THEN** that absence MUST be classified separately from a failed write
- **AND** it MUST NOT be counted as a successful rebind

### Requirement: Approved fact writes execute under server-recorded provenance

When the owner carve-out or the confidence gate parks a fact write, the
asserting `src` and `observed_at` SHALL be recorded in
`relationship.fact_approval_context` — a row only the writer ever writes — and
SHALL NOT be stored in `pending_actions.tool_args`. Approval dispatch replays
`tool_args` as keyword arguments to the MCP tool, so every key stored there is
necessarily a parameter a session could also supply, and `src` selects the
carve-out's trusted-source exemption.
- The parked `tool_args` SHALL carry the action's own id as `approval_action_id`,
  so the replay is recognisable as the execution of an approved decision rather
  than a fresh proposal.
- `approval_action_id` SHALL be treated as a claim to be verified, never as
  authority in itself. The writer SHALL accept it only if a `pending_actions` row
  exists for this tool, is in an executable status, matches the
  `(subject, predicate, object, object_kind)` quadruple being written, and has a
  recorded source; any mismatch SHALL raise rather than write a fact under
  provenance that cannot be substantiated. A verified approval SHALL supply the
  `src`, `observed_at`, evidence, and session from the parked row, and SHALL skip
  the proposal-time gates the owner already cleared so the write lands instead of
  re-parking. The fact SHALL record `assert_origin='approved'` and the approving
  action's id, and SHALL keep the observation time from when the fact was
  proposed rather than when it was approved.
- The Relationship MCP assert tool SHALL expose no `src` or `observed_at`
  parameter at all, in either its schema or its Python signature.
- The parked private context SHALL additionally freeze original content authority and author. Replay SHALL require the actual ApprovalExecutionContext for current task, tool, action and original canonical stored args digest, together with current executable decision lineage; planted approved status/action id/actor text alone SHALL not authorize it. Replay confirmation SHALL preserve original reporter and the full normalized temporal mode/base/packet and observation/evidence/session. Legacy stored verified keys SHALL normalize privately after the original approval binding, never reappear in public schema.
- The Relationship-only private executor adapter SHALL bind original stored tool/args under the pending-action row lock before normalizing legacy verified. Genuine new owner confirmation SHALL require the private admitted-decision/rule lineage hook recording its server-resolved owner, immutable decision reference and original digest in fact_approval_context. Event/actor text and planted approved status SHALL not substitute. Existing execution compatibility and fact-versus-terminal-receipt transaction boundaries SHALL remain explicit.
- Original-report approval replay SHALL preserve the selected report’s authority/original token and its frozen nullable authority_entity_created_at availability witness. A cleared live fact/context pointer SHALL never refill from original UUID lookup, current ambient identity or a recreated entity. The private adapter still binds original stored tool/args under the existing pending lock before legacy normalization; it SHALL not add a conflicting second pending lock in a separate fact connection. Confirmation lineage and fact-versus-executor-terminal transaction boundaries remain separate and unchanged.

ID: REQ-relationship-facts-005
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; Relationship MANIFESTO.md; adopted dashboard-owner-auth and existing capability contract
Scope: v1-mandatory

#### Scenario: Parked action keeps the source server-side

- **WHEN** a write to the owner entity is parked for approval
- **THEN** the stored `tool_args` contains neither `src` nor `observed_at`
- **AND** `relationship.fact_approval_context` records both for that action

#### Scenario: Approved replay writes the fact instead of re-parking

- **WHEN** an approved action is replayed with its stored arguments
- **THEN** the fact is written with `assert_origin='approved'`, the parked
  evidence, and the approving action's id
- **AND** no new pending action is created

#### Scenario: Approved observation time survives the approval delay

- **WHEN** an action parked with an old `observed_at` is approved much later
- **THEN** the written fact keeps the original observation time

#### Scenario: Unverifiable approval claim is refused

- **WHEN** a caller supplies an `approval_action_id` that is unknown, still
  pending, belongs to another tool, or was approved for a different triple
- **THEN** the write raises and no fact is written

#### Scenario: Tool surface offers no way to name a source

- **WHEN** the assert tool's signature is inspected
- **THEN** it has no `src` and no `observed_at` parameter

#### Scenario: A planted approved row is not executor authority

- **WHEN** an ordinary caller supplies a matching action id/status without the actual bound executor context
- **THEN** the writer SHALL refuse owner confirmation and commit no fact effects
- **AND** real execute_approved_action using the original stored args SHALL remain a positive

### Requirement: Single home for registry-relational edges

Every entity-to-entity edge whose meaning is a **registry-relational predicate** SHALL be
written to `relationship.entity_facts` through the central writer
`relationship_assert_fact(object_kind='entity')`, and SHALL NOT be stored as the canonical
representation in the memory module's `{schema}.facts` table. The relationship graph — the data
read by `/entities/concentration`, Dunbar tier-scoring, and entity-neighbor views — SHALL be
materialized from `relationship.entity_facts` only.

A relationship between two entities is a **registry-relational edge** when it corresponds to a
predicate in the relational family of `relationship.entity_predicate_registry` (kinship, social,
professional, membership, employment, co-attendance, transactional). An edge that references two
entities but is **episodic or coordination context** (e.g. `planned_dinner_with`,
`wake_coordination`) is **narrative**, is not part of the canonical relationship graph, and MAY
live in memory `{schema}.facts` (see `module-memory`).

#### Scenario: Relational edge lands in entity_facts, not memory
- **WHEN** a relationship between two tracked entities matching a registry-relational predicate
  (e.g. "Alice is Bob's sister", "Carol works at Acme") is extracted
- **THEN** an active row MUST be written to `relationship.entity_facts` via
  `relationship_assert_fact(object_kind='entity')` with the hyphenated registry predicate
- **AND** the canonical edge MUST NOT be stored in `relationship.facts`

#### Scenario: Concentration reads the graph from entity_facts
- **WHEN** registry-relational edges exist as active rows in `relationship.entity_facts`
- **THEN** `GET /api/relationship/entities/concentration?pred=<p>` MUST return those edges
  aggregated by weight
- **AND** the result MUST NOT depend on any read from `relationship.facts`

### Requirement: Fact-extraction skill routes relational edges to the central writer

The relationship butler's `fact-extraction` skill SHALL instruct the runtime to write
registry-relational edges through `relationship_assert_fact()` using hyphenated registry
predicate names, and SHALL reserve `memory_store_fact(object_entity_id=…)` for narrative
(non-registry) edges only. A contract test SHALL assert that every edge predicate named in the
skill is either a member of the relational registry or on an explicit narrative allowlist.

#### Scenario: Skill edge vocabulary stays a subset of the registry
- **WHEN** the contract test scans `roster/relationship/.agents/skills/fact-extraction/SKILL.md`
  for edge predicates routed to `relationship_assert_fact()`
- **THEN** each MUST resolve (directly or via the underscore→hyphen alias map) to a relational
  predicate in `relationship.entity_predicate_registry`
- **AND** any predicate not so resolvable MUST appear on the documented narrative allowlist or
  the test MUST fail

### Requirement: One-time backfill of memory edge-facts into entity_facts

A one-time backfill SHALL migrate existing memory edge-facts (`relationship.facts` rows with
`object_entity_id` set) into `relationship.entity_facts` where their predicate maps to a
registry-relational predicate, leaving genuinely narrative edges untouched. The backfill SHALL
default to dry-run, be idempotent, and report counts moved / retracted / left-narrative with no
silent truncation.

#### Scenario: Dry-run is the default and emits the mapping plan
- **WHEN** the backfill script is run without `--apply`
- **THEN** it MUST print the per-predicate mapping plan and counts
- **AND** it MUST NOT write to `relationship.entity_facts` or modify `relationship.facts`

#### Scenario: Mappable edge is re-homed exactly once
- **WHEN** the backfill is applied to a memory edge-fact whose predicate maps to a registry
  predicate (e.g. `child_of`→`child-of`)
- **THEN** an active row MUST be asserted in `relationship.entity_facts` via the central writer
- **AND** the source memory edge-fact MUST be retracted so the edge has a single home, but only
  when the central write committed an active row; a `pending_approval` outcome leaves the source
  fact active (see the baseline owner carve-out requirement)
- **AND** re-running the backfill MUST NOT create a duplicate active row

#### Scenario: Narrative edge is left in memory
- **WHEN** the backfill encounters a memory edge-fact whose predicate has no registry mapping
  (e.g. `planned_dinner_with`)
- **THEN** the row MUST be left unchanged in `relationship.facts`
- **AND** the summary MUST count it under "left narrative"

### Requirement: Deprecate and remove `relationship.quick_facts`

The legacy `relationship.quick_facts` key/value table SHALL be removed. Its only live use —
vCard ORG/TITLE — SHALL be routed to `public.contacts.company`/`job_title`. The table SHALL be
dropped only by a self-guarding migration that asserts zero rows before dropping.

#### Scenario: vCard ORG/TITLE round-trips via public.contacts
- **WHEN** a vCard with ORG and TITLE is imported and then exported
- **THEN** ORG MUST persist to and read from `public.contacts.company`
- **AND** TITLE MUST persist to and read from `public.contacts.job_title`
- **AND** no read or write MUST touch `relationship.quick_facts`

#### Scenario: Drop is gated on zero rows
- **WHEN** the deprecation migration runs
- **THEN** it MUST assert `relationship.quick_facts` has zero rows before issuing `DROP TABLE`
- **AND** if any row exists the migration MUST refuse and report rather than drop

### Requirement: Server-admitted identity fact context

Only the private actual source producer/registered live invocation binding or genuine current protected HTTP admission or actual approved executor SHALL select fact authority. Transport locators and identity attribution SHALL remain distinct from authentication. Internal no-context SYSTEM is retained only for trusted internal entrypoints; public missing-binding traffic SHALL not impersonate it. Server-owned deterministic ingress exemption SHALL remain scoped to actual unknown-sender/transitory dedup. No new custody, role, credential or child-isolation posture is adopted.
- Live availability is a separate private lifecycle witness, never authentication. The actual canonical source producer SHALL capture the resolved entity UUID and its existing public.entities.created_at on the real server resolution path, under the ordinary owning transaction/entity lock, and freeze the witness with its accepted-source attribution record before issuing the private source capability. Durable source recovery SHALL read that original server-written witness rather than reconstruct it from a UUID-only current lookup or caller context. The Relationship-only pending approval context SHALL freeze the same witness and its nullable live pointer; if a live FK is used there it SHALL be ON DELETE SET NULL and its original token remains inert. Registered public pipeline.process/source labels and copied request identifiers SHALL not be witness producers. The target verifies the admitted source first, then locks the COMPLETE subject/object/live-reporter/decision-owner set in sorted UUID order, canonical handle locks next and exact fact/context rows last. Batch callers plan the union before their first entity lock. A new assertion may populate a live reporter pointer only when the locked actual entity row matches the source-captured witness and current source eligibility; missing or differing evidence leaves that already admitted original token with a NULL live pointer, never owner authority inferred from that token. Existing owner/subject liveness gates still apply. For preservation of an existing report, read its own locked fact/context availability: an already NULL pointer is one-way unavailable and MUST NOT refill, including on supersession, approval replay, adoption or unchanged retry. A fresh, genuinely admitted source may separately resolve a new current entity and stamp its new assertion; it may not repair historical links.
- Store authority_entity_created_at TIMESTAMPTZ NULL as the private source-captured lifecycle witness on each fact assertion version and its owning pending context. It is not a copied name/handle or a public caller field. Existing rows remain NULL without backfill guessing. Classified new writes set it only from actual captured attribution; unknown evidence stays NULL. The owned version guard preserves original token, authority and this witness within the assertion version, permits existing domain lifecycle/merge and FK nulling, and forbids NULL-to-live refill of a selected existing report. A new non-NULL live author pointer additionally requires a non-NULL captured witness and a matching current locked entity row. The schema CHECK retains the original-token equality rule and requires authority_entity_created_at IS NOT NULL whenever authority_entity_id IS NOT NULL; a database CHECK cannot authenticate source capture or compare a foreign row, so the actual writer/registered producer and positioned controls remain mandatory. Genuine new-report unchanged comparison includes the witness with authority/original UUID; report-preserving replacement copies it unchanged. This avoids treating two different ordinary row lifetimes sharing a deliberately recreated UUID as the same report while never claiming timestamp uniqueness.
- This witness is intentionally bounded by the adopted trusted host/runtime/database premise. core_002 supplies created_at DEFAULT now(), ordinary entity_create supplies neither id nor created_at, and current entity_update/mounted update/merge/tombstone paths preserve created_at. There is NO existing immutable created_at constraint or cryptographically unique entity-incarnation generation. The private producer capture was absent at the PRIMARY source snapshot. This change introduces it; source presence supplies no runtime or deployed proof. The tuple detects ordinary deletion/recreation with a different creation time; it does not prove external sender identity, defend against a trusted administrator deliberately reproducing an identical UUID/timestamp, or authenticate a runtime. If a trusted restore or repair invalidates that lifecycle evidence, invalidate old ephemeral source capabilities and require genuine source re-admission; a missing witness cannot be silently reconstructed into live availability. Do not introduce a new entity generation/role/credential/host posture merely to turn this bounded lifecycle check into a stronger guarantee. Existing NULL fact/context pointers stay unavailable even if a trusted administrator recreates identical values.

ID: REQ-relationship-facts-006
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; adopted owner-auth request admission; existing relationship/entity contracts
Scope: v1-mandatory

#### Scenario: Forged locator is not an owner context

- **WHEN** an ordinary caller copies an owner entity UUID/runtime locator without the admitted private invocation binding
- **THEN** it SHALL not write owner authority or mint owner confirmation
- **AND** an actual registered owner-source invocation SHALL remain a positive

#### Scenario: Old source availability cannot relink to a recreated reporter

- **WHEN** the actual registered source producer has frozen reporter availability, that reporter is committed-deleted, and a distinct-created-at entity is deliberately planted with the same UUID before old-source replay
- **THEN** old admitted attribution SHALL remain its original token with no link to the recreated entity, without treating the UUID/timestamp as authentication
- **AND** an existing fact/context NULL availability SHALL remain NULL through unchanged replay, preserving supersession, approval and adoption
- **AND** a fresh genuinely admitted source resolving the new current entity SHALL still be able to stamp its own new assertion under existing policy
- **AND** writer/deletion races in both lock orders SHALL have separate committed readback and no partial attribution effects
- **AND** the witness SHALL be described at its bounded trusted-runtime/DB scope, without a claim to defeat deliberate trusted-admin identical-tuple recreation

### Requirement: Candidate handle lifecycle and atomic owner adoption

Third-party/mixed new channel reports for live known non-transitory persons SHALL be candidate until an actual owner-admitted exact-row adoption. Candidates SHALL retain typed evidence, original reporter and complete occurrence/effective packet without active routing/coverage/graph effects. Duplicate/history, canonical handle collision, owner/legacy active protection, transitory promotion, rotation, rejection and stale/concurrent adoption SHALL preserve receipts and explicit lifecycle. Adoption SHALL atomically activate the compatible same row or return its explicit compatible survivor with owner confirmation and all domain effects; no handle stealing or implicit new uniqueness policy is permitted.

ID: REQ-relationship-facts-007
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; adopted owner-auth request admission; existing relationship/entity contracts
Scope: v1-mandatory

#### Scenario: Adoption commits once with reporter intact

- **WHEN** real owner admission adopts an unchanged lone candidate
- **THEN** its exact row SHALL become active with owner confirmation and unchanged reporter/packet
- **AND** repeated exact adoption SHALL return the same durable receipt without duplicate effects

#### Scenario: Collision or stale loser has no domain effects

- **WHEN** an active different-subject/legacy handle collision or concurrent reject/merge/version change defeats adoption
- **THEN** it SHALL return a bounded conflict and leave all fact/evidence/coverage/gap/projection/audit effects unchanged
- **AND** a compatible independent candidate SHALL still be adoptable

### Requirement: Relationship gap answers use persisted fact attribution

Gap closure SHALL read the committed target fact authority/author and valid stored owner confirmation. An unrelated ambient session SHALL not elevate/rewrite attribution. Candidate identity reports SHALL not answer established identity gaps; active third-party reports remain reported, genuine owner-confirmed answers established while reporter preserved. Unchanged replay SHALL not duplicate answers; current savepoint-only secondary closure degradation SHALL remain truthful and shall not erase primary committed facts.

ID: REQ-relationship-facts-008
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; adopted owner-auth request admission; existing relationship/entity contracts
Scope: v1-mandatory

#### Scenario: Ambient owner cannot elevate a stored report

- **WHEN** a later owner session replays or closes a gap from an existing third-party fact
- **THEN** the stored reporter/authority SHALL govern the answer and remain unchanged
- **AND** an owner-confirmed positive SHALL use its stored confirmation once
