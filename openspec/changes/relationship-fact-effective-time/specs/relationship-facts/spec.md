## MODIFIED Requirements

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
| `effective_period_id` | UUID NULL | Stable identity of one effective occurrence; NULL is the backward-compatible default occurrence; the all-zero UUID is reserved and rejected |
| `effective_from` | TIMESTAMPTZ NULL | Inclusive normalized lower effective bound; interpretation comes from `effective_from_precision` |
| `effective_from_precision` | TEXT NULL | `instant \| day \| month \| year \| unbounded`; NULL with a NULL bound means unknown |
| `effective_to` | TIMESTAMPTZ NULL | Exclusive normalized upper effective bound; interpretation comes from `effective_to_precision` |
| `effective_to_precision` | TEXT NULL | `instant \| day \| month \| year \| unbounded`; NULL with a NULL bound means unknown |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

Effective time SHALL be independent of assertion time (`created_at`), observation time
(`observed_at` and `last_seen`), confidence (`conf`), and assertion lifecycle (`validity`). No
effective field SHALL default from or be backfilled from any of those axes. `validity='active'`
means the current assertion version, not "effective at the current clock time". An active assertion
MAY describe a closed effective interval, and a retracted or superseded assertion SHALL retain the
effective packet it had when active.

The effective interval SHALL be half-open `[effective_from, effective_to)`: a concrete lower bound
is included and a concrete upper bound is excluded. Precision applies independently to each bound.
The only concrete precision values SHALL be `instant`, `day`, `month`, and `year`; `unbounded` SHALL
be the only non-concrete string value. A SQL NULL precision SHALL be the sole representation of an
unknown bound, and the string `unknown` SHALL be rejected.

For each bound, a non-NULL timestamp SHALL require a concrete precision, and a concrete precision
SHALL require a non-NULL timestamp. A NULL timestamp MAY have NULL precision, meaning that boundary
is unknown, or `unbounded` precision, meaning the caller explicitly asserts no boundary in that
direction. Both bounds unknown SHALL mean the temporal extent is unspecified, never that the fact
was true forever. Both bounds `unbounded` SHALL explicitly mean all time. When both timestamps are
concrete, `effective_from` MUST be earlier than `effective_to`; equality and reversal SHALL be
rejected by both writer validation and a database CHECK constraint.

The migration SHALL name and enforce `ck_ef_effective_from_shape` and
`ck_ef_effective_to_shape` for the timestamp/precision pair rules,
`ck_ef_effective_from_canonical` and `ck_ef_effective_to_canonical` for UTC unit-start alignment of
stored `day`, `month`, and `year` bounds, `ck_ef_effective_range` for the strict concrete range, and
`ck_ef_effective_period_nonzero` for the reserved UUID. These constraints SHALL accept every
unknown, unbounded, and partial-bound combination described above and SHALL reject every second
encoding of the same bound state.

Stored timestamps SHALL be canonical UTC instants. `instant` input MUST carry `Z` or a numeric UTC
offset, MUST NOT be timezone-naive, and SHALL be converted to UTC without losing PostgreSQL-supported
sub-second precision. `day`, `month`, and `year` inputs SHALL respectively use the civil forms
`YYYY-MM-DD`, `YYYY-MM`, and `YYYY`, with no offset or time component. Using the proleptic Gregorian
calendar, a coarse lower bound SHALL normalize to 00:00:00 UTC at the first day of its named unit;
a coarse upper bound SHALL normalize to 00:00:00 UTC at the first day after its named unit. Invalid
calendar values, malformed input, precision/value mismatches, and upper-bound normalization overflow
MUST fail before any write.

`effective_period_id` SHALL identify one occurrence of the same SPO triple. NULL SHALL identify the
single backward-compatible default occurrence used by legacy rows and callers. A caller asserting a
genuinely repeated period SHALL supply a stable non-zero UUID distinct from the earlier occurrence
and SHALL reuse that UUID for replay. A temporal correction SHALL retain the occurrence id. This
identity rule distinguishes a repeated period from a correction without relying on overlap policy.

**Indexes (required):**
- `(subject, predicate)` — primary access pattern
- `(predicate, object) WHERE object_kind = 'literal'` — reverse-lookup for ingestion
  routing (e.g. "incoming Telegram chat 12345 → which entity")
- `(predicate) WHERE validity = 'active'` — Concentration aggregation
- `(last_seen DESC)` — stale detection, Finder tie-break
- `(subject) WHERE validity = 'active' AND predicate LIKE 'has-%'` — contacts endpoint

**Uniqueness (pre-temporal contract):** `UNIQUE (subject, predicate, object) WHERE validity = 'active'`.
That pre-temporal SPO rule SHALL be replaced by active uniqueness over `(subject, predicate, object,
COALESCE(effective_period_id, '00000000-0000-0000-0000-000000000000'::uuid))`, and the all-zero
UUID SHALL be rejected as an explicit `effective_period_id`. The occurrence-scoped rule permits
multiple active repeated periods for one SPO. It SHALL NOT enforce predicate cardinality or period
overlap; that separately owned policy belongs to `bu-4ss0u`.

**Schema boundary with `memory.facts` (R2 #3):** the table is `relationship.entity_facts`
(schema-qualified). A separate `memory.facts` table exists under the memory module schema
per RFC 0006 (`src/butlers/modules/memory/migrations/001_memory_schema.py:106`); the two
tables are isolated by schema and MUST NOT be cross-joined. Migration beads and all SQL
authored under this change MUST reference the schema-qualified name `relationship.entity_facts`
throughout — never bare `facts`.

Effective-time support SHALL NOT add temporal fields to narrative memory `facts`, copy or infer
temporal values between the stores, or create a cross-store query. Structural registry facts remain
in `relationship.entity_facts`; episodic and coordination facts remain narrative memory under the
separate `relational-edges-single-home` boundary.

Existing assertion-current readers SHALL continue to select `validity='active'` without an implicit
comparison between `now()` and either effective bound. `bu-1ypjo` owns opt-in `as_of` selection and
any new owner REST surface. Storage of an effective packet SHALL NOT itself change a reader's time
slice.

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

#### Scenario: Legacy row has unspecified effective time

- **WHEN** a row created before the temporal columns exists after upgrade
- **THEN** its period id, both bounds, and both precision columns MUST be NULL
- **AND** those NULLs MUST mean the default occurrence with unspecified effective extent
- **AND** no value may be synthesized from `created_at`, `observed_at`, or `last_seen`

#### Scenario: Unknown and unbounded bounds remain distinct

- **WHEN** one fact stores a NULL lower timestamp with NULL lower precision and another stores a
  NULL lower timestamp with `effective_from_precision='unbounded'`
- **THEN** the first lower bound MUST mean unknown
- **AND** the second MUST mean explicitly open toward the past
- **AND** neither representation may be rewritten into the other

#### Scenario: Partial interval is representable

- **WHEN** a fact has a concrete normalized lower bound and an unknown or unbounded upper bound
- **THEN** the row MUST satisfy the temporal CHECK constraints
- **AND** the known lower-bound meaning MUST remain available without inventing an upper bound

#### Scenario: Coarse dates normalize into a half-open UTC interval

- **WHEN** a lower bound `2024-03` and upper bound `2024-05` are supplied with `month` precision
- **THEN** the stored lower bound MUST be `2024-03-01T00:00:00Z`
- **AND** the stored upper bound MUST be `2024-06-01T00:00:00Z`
- **AND** the represented interval MUST include all of March through May and exclude June

#### Scenario: Invalid temporal shape is rejected structurally

- **WHEN** a row has a concrete timestamp without concrete precision, concrete precision without a
  timestamp, an unsupported precision token, the reserved all-zero period UUID, or two concrete
  bounds whose normalized upper value is not later than the lower value
- **THEN** the database CHECK constraints MUST reject the row
- **AND** the central writer MUST reject the same packet before any write

#### Scenario: Effective time remains independent of other clocks and confidence

- **WHEN** assertion time, observation time, `last_seen`, confidence, or derived staleness changes
- **THEN** that change MUST NOT derive or rewrite an effective bound or its precision
- **AND** any replacement assertion version MUST copy the normalized effective packet unchanged
  unless the call explicitly corrects that packet

#### Scenario: A closed effective interval can remain assertion-current

- **WHEN** an active row's concrete `effective_to` is earlier than the current clock time
- **THEN** the row MUST remain `validity='active'`
- **AND** existing assertion-current readers MUST continue returning it
- **AND** no implicit effective-now filter may be added before `bu-1ypjo`

#### Scenario: Assertion lifecycle preserves effective history

- **WHEN** a row is retracted or superseded
- **THEN** its occurrence id, bounds, and precisions MUST remain unchanged
- **AND** lifecycle change MUST NOT imply that its effective interval was open, closed, or corrected

#### Scenario: Repeated period has a distinct occurrence identity

- **WHEN** the same subject, predicate, and object holds again in a separate period
- **THEN** the caller MUST supply a new stable non-zero `effective_period_id`
- **AND** both occurrences MAY remain assertion-current under occurrence-scoped uniqueness
- **AND** overlap or single-valued cardinality enforcement MUST remain the responsibility of
  `bu-4ss0u`

### Requirement: Central writer — `relationship_assert_fact()`

ALL writes into `relationship.entity_facts` MUST go through a single MCP tool
`relationship_assert_fact(subject, predicate, object, *, src, conf, weight, primary,
verified, object_kind)` exposed by the relationship butler. No butler MAY issue a direct
`INSERT INTO relationship.entity_facts` or `UPDATE relationship.entity_facts` from outside the
relationship butler's schema role.

The temporal extension SHALL add optional `effective_period_id`, `effective_from`,
`effective_from_precision`, `effective_to`, `effective_to_precision`, and `corrects_fact_id`
arguments at the writer boundary. The public MCP wrapper SHALL continue keeping `src` and
`observed_at` server-held. Temporal values are caller-controlled assertion content, not provenance.

The central writer is responsible for:
- Predicate validation against `relationship.entity_predicate_registry`.
- Dedup (pre-temporal contract: `ON CONFLICT (subject, predicate, object) WHERE validity='active' DO UPDATE`).
- Provenance enforcement (every triple has `src`, `conf`, `verified`).
- Supersession on update (mark prior row `validity='superseded'`, insert new row).
- Canonical validation and UTC normalization of the complete temporal packet before approval,
  deduplication, correction, evidence, coverage, or projection writes.
- Occurrence-scoped deduplication using the normalized SPO and `effective_period_id`, with NULL
  mapped only to the reserved default-occurrence sentinel in the active unique index.
- Explicit temporal correction only when `corrects_fact_id` identifies the exact active row in the
  same SPO occurrence.

The pre-temporal dedup statement above SHALL remain the behavior for calls that omit the complete
temporal packet. Once a temporal packet or explicit period id is present, occurrence-scoped
uniqueness SHALL be the conflict target; the writer MUST NOT collapse distinct explicit periods.

**Transaction-safety (Amendment 14, binding):** `relationship_assert_fact()` MUST be safe
to call from within an open `asyncpg` transaction. It MUST NOT require its own outer
transaction wrapper, MUST NOT open a nested transaction that would deadlock on the existing
connection, and MUST NOT panic when invoked from a caller that already holds a pool
connection. **Idempotency (Amendment 14, binding):** the writer MUST be idempotent on
`(subject, predicate, object)` — repeated calls with identical identity arguments produce
exactly one active row, not duplicates; supersession semantics apply when `(src, conf,
verified, lastSeen)` differ across calls.

Temporal idempotency SHALL refine that rule by occurrence. Replaying the same SPO, occurrence id,
normalized temporal packet, and assertion fields SHALL return the existing active fact id and SHALL
NOT create or supersede a fact row. Existing evidence behavior remains additive and deduplicated.
For the default NULL occurrence, a call that omits all temporal arguments SHALL retain existing
behavior. For an explicit period, an occupied occurrence whose temporal packet differs SHALL fail
unless `corrects_fact_id` names its exact active row.

A temporal correction SHALL be an immutable-version replacement. `corrects_fact_id` MUST resolve to
an active row with the same subject, predicate, object, and effective period id. The writer SHALL
lock that occurrence, mark exactly the named row superseded, insert one active replacement with the
corrected packet, keep the old packet unchanged, and carry its evidence forward. `corrects_fact_id`
SHALL be operation input only and SHALL NOT become stored temporal state.

Two concurrent identical replays SHALL converge on the same active row. Two different corrections
that name the same active fact id SHALL use compare-and-swap semantics: the transaction that locks
and commits the named active row first SHALL succeed, and the other SHALL fail as a stale correction
after observing that the named id is no longer active. The losing transaction MUST leave no fact,
evidence, coverage, approval-context, or graph-projection write. It MUST NOT silently correct the
winner's replacement.

**Owner-gate carry-forward (RFC 0017, binding):** when `subject` resolves to the owner
entity, `relationship_assert_fact()` MUST NOT write the triple directly; instead it MUST
emit a `pending_action` for owner approval, mirroring the existing pattern in
`roster/relationship/tools/contact_info.py::contact_info_add` per RFC 0017 §2.3. The owner
approves the pending action via the existing approval ceremony; only after approval does
the triple land as `validity='active'`. Non-owner subjects are written directly without the
approval hop.

The normalized temporal packet, effective period id, and correction target SHALL survive owner
parking in `pending_actions.tool_args` because they are part of the assertion being reviewed.
Approval verification SHALL compare them as well as the SPO identity and SHALL reject any altered
packet. Approved replay SHALL restore the parked temporal values together with the server-recorded
source, observation time, evidence, session, and approval action id. A failed or stale replay SHALL
roll back every fact, evidence, coverage, approval-context, and graph-projection effect.

**Owner-gate trusted-source exemption (as built):** the owner gate has a
trusted-source carve-out (`roster/relationship/tools/relationship_assert_fact.py:179-217`).
When `src` is an owner-self source (`"owner-bootstrap"` from daemon startup, or
`"owner-self"` from owner-setup tools) or a trusted internal-derivation source
(`"interaction_sync"`), an owner-subject write is auto-applied directly instead of being
parked for approval (`_OWNER_AUTO_APPLY_SOURCES`). These source strings are server-set: the
MCP tool wrapper hardcodes `src` and the dashboard API rejects the trusted values via a
Pydantic validator, so external callers (LLM sessions, HTTP) cannot spoof them. This lets
the daemon self-register owner identity handles and lets structured-data jobs derive owner
facts without a human approval hop.

Trusted-source exemption SHALL change only whether approval parking occurs. It SHALL NOT weaken
temporal validation, occurrence uniqueness, explicit-correction preconditions, atomic evidence, or
concurrency behavior.

This single-ingress contract preserves RFC 0006 schema isolation and RDF integrity.

#### Scenario: Direct SQL writes are blocked
- **WHEN** any butler other than relationship attempts `INSERT INTO relationship.entity_facts`
- **THEN** PostgreSQL MUST reject the statement on role permissions
- **AND** the only successful write path MUST be `relationship_assert_fact()` via MCP

#### Scenario: Omitted temporal arguments preserve legacy writer behavior

- **WHEN** an existing caller omits every temporal argument
- **THEN** the writer MUST use the NULL default occurrence and store unknown lower and upper bounds
- **AND** existing idempotency and non-temporal provenance supersession behavior MUST remain valid
- **AND** the writer MUST NOT derive a bound from assertion or observation time

#### Scenario: Identical normalized replay is unchanged

- **WHEN** a caller repeats an assertion with the same SPO, occurrence id, normalized temporal
  packet, and assertion fields
- **THEN** the writer MUST return the existing active fact id with outcome `unchanged`
- **AND** it MUST NOT insert or supersede a fact row
- **AND** cited evidence MAY be appended and deduplicated under the existing evidence contract

#### Scenario: Different packet without correction target is refused

- **WHEN** an explicit occurrence already has an active row and a call supplies a different
  effective bound or precision without `corrects_fact_id`
- **THEN** the writer MUST reject the call as an ambiguous correction
- **AND** it MUST NOT reinterpret the call as a repeated period or mutate the occupied occurrence

#### Scenario: Temporal correction preserves the old assertion version

- **WHEN** `corrects_fact_id` names the active row for the same SPO occurrence and the caller changes
  an effective bound or precision
- **THEN** the named row MUST become `validity='superseded'` with its original temporal packet intact
- **AND** exactly one replacement row for that occurrence MUST become active with the corrected
  normalized packet
- **AND** the replacement MUST carry forward the prior evidence without rewriting prior evidence

#### Scenario: Recording a known end does not retract the fact

- **WHEN** an explicit correction replaces an unknown or unbounded upper bound with a concrete
  `effective_to`
- **THEN** the corrected replacement MUST remain `validity='active'` even when that bound is in the
  past
- **AND** the prior row MUST be superseded only as the immutable earlier assertion version, not as a
  claim that the relationship was false during its represented interval

#### Scenario: Two concurrent corrections cannot overwrite each other

- **WHEN** two transactions submit different temporal corrections naming the same active fact id
- **THEN** the first transaction to lock and commit that named active row MUST create the sole active
  replacement for the occurrence
- **AND** the other transaction MUST fail as stale without superseding the replacement
- **AND** the losing transaction MUST persist no partial fact, evidence, coverage, approval-context,
  or graph-projection effect

#### Scenario: Owner-approved replay preserves the reviewed temporal assertion

- **WHEN** a temporal assertion or correction is parked and later approved
- **THEN** the approved replay MUST use the parked normalized bounds, precisions, occurrence id, and
  correction target without accepting replacements from the dispatch caller
- **AND** it MUST use `src`, `observed_at`, evidence, session, and action id from the server-held
  approval records
- **AND** an altered or stale replay MUST fail atomically

#### Scenario: Evidence carry-forward does not rewrite effective time

- **WHEN** a non-temporal provenance, confidence, verification, observation, or staleness change
  supersedes an assertion version
- **THEN** the replacement MUST copy the occurrence id and effective packet unchanged
- **AND** evidence carry-forward MUST copy ledger references to the replacement without changing the
  superseded row's evidence or temporal fields

#### Scenario: Passing the upper bound does not mutate assertion lifecycle

- **WHEN** the clock advances beyond an active row's concrete `effective_to`
- **THEN** the writer MUST perform no automatic retraction or supersession
- **AND** current readers MUST remain assertion-current until opt-in as-of behavior lands under
  `bu-1ypjo`

## Source References

- Non-Negotiable Rule 3 (inter-butler communication and schema isolation)
- Non-Negotiable Rule 4 (deterministic infrastructure)
- Non-Negotiable Rule 6 (Relationship Butler manifesto governs domain scope)
- RFC 0004 (identity and contact resolution)
- RFC 0006 (database schema and isolation)
- RFC 0017 (owner mutation approval carry-forward)
- RFC 0031 (atomic public entity graph projection)
- `fact-evidence-and-coverage` active change (immutable evidence, coverage, and approval provenance)
- `relational-edges-single-home` active change (structural versus narrative fact ownership)
