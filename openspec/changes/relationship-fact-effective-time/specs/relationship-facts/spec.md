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
The final schema SHALL enforce active uniqueness over `(subject, predicate, object,
COALESCE(effective_period_id, '00000000-0000-0000-0000-000000000000'::uuid))`, and the all-zero
UUID SHALL be rejected as an explicit `effective_period_id`. The occurrence-scoped rule permits
multiple active repeated periods for one SPO. It SHALL NOT enforce predicate cardinality or period
overlap; that separately owned policy belongs to `bu-4ss0u`.

The uniqueness transition SHALL use two schema stages. The expand migration SHALL add the temporal
columns, constraints, and occurrence index while retaining the deployed
`uq_ef_spo_active (subject, predicate, object) WHERE validity='active'` index unchanged. The deployed
writer's inferred `ON CONFLICT` target MUST continue preparing and executing throughout that stage.
While both indexes exist, the legacy index SHALL intentionally prevent multiple active occurrences.

Only after every Relationship instance is proven to contain the complete compatible/fenced mutator
inventory, and every old image is proven absent, MAY a later cutover migration drop
`uq_ef_spo_active`. Temporal assertions, corrections, and repeated-period writes MUST remain disabled
until that cutover. The final writer SHALL use targetless `ON CONFLICT DO NOTHING` plus locked
re-read/CAS behavior so it remains valid before and after the drop. Every mutator SHALL treat
presence of the legacy index as the fail-closed `temporal_cutover_pending` capability state where
applicable, checked before approval parking or persistence. Central-writer compatibility alone SHALL
NOT authorize cutover.

Schema rollback SHALL be allowed only while temporal writes have remained disabled, or during a
quiesced no-write window after proving there is at most one active row per SPO and no temporal value
would be lost. Once any temporal write is admitted, automatic downgrade and old-writer rollback MUST
be refused: the legacy index may conflict with repeated occurrences, and old code does not preserve
temporal packets. Recovery SHALL roll forward or follow a separately reviewed data-preserving plan;
it MUST NOT delete, choose, supersede, or flatten rows to recreate the legacy index.

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

#### Scenario: Expand schema preserves the deployed conflict target

- **WHEN** the temporal expand migration has added the new columns, checks, and occurrence index
- **THEN** `uq_ef_spo_active` MUST still exist with its original active-SPO definition
- **AND** the deployed writer's exact inferred `ON CONFLICT` statement MUST prepare and write
  successfully against the migrated PostgreSQL schema
- **AND** multiple active occurrences MUST remain unavailable during this compatibility stage

#### Scenario: Cutover does not pretend legacy rollback remains safe

- **WHEN** the legacy index has been removed and any temporal assertion or repeated occurrence has
  been admitted
- **THEN** downgrade and rollback to the old writer MUST fail closed
- **AND** no migration or operator path may discard or collapse temporal rows merely to recreate the
  legacy active-SPO index

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

The wire contract SHALL treat omission and explicit JSON null identically; it SHALL NOT depend on
whether an optional key was present. When all five stored temporal values and `corrects_fact_id` are
omitted/null, the request SHALL mean an ordinary assertion with no temporal intent. It SHALL use the
default occurrence: if no active default occurrence exists, the writer SHALL create it with unknown
bounds; if one exists, unchanged comparison and any non-temporal replacement SHALL use and preserve
that row's stored packet, including known bounds.

When any stored temporal value is non-null and `corrects_fact_id` is omitted/null, the request SHALL
be an explicit temporal assertion/replay whose values are the desired packet. When
`corrects_fact_id` is non-null, correction mode SHALL be selected even if every other temporal
argument is omitted/null; the desired replacement then has unknown bounds and inherits the target
row's occurrence id. A supplied period id in correction mode MUST match the target. For either bound,
`unbounded` requires a null/omitted timestamp, a concrete precision requires a concrete value, and a
concrete value requires concrete precision. Correction input SHALL be a complete desired replacement
packet, not a partial patch. Clearing a known packet to unknown MUST use explicit correction mode.

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
- Targetless `ON CONFLICT DO NOTHING` insertion followed by locked re-read/CAS handling, so the new
  writer remains valid with the old index, both indexes, or only the final occurrence index.

The pre-temporal dedup statement above SHALL remain executable while the legacy index exists. The
new writer SHALL check for that index before approval parking or persistence. While it exists, an
all-omitted/all-null request SHALL keep the old single-slot behavior and any non-null temporal value
or correction target SHALL fail `temporal_cutover_pending` without writes. Once the index is absent,
the writer SHALL enable occurrence-scoped temporal behavior and MUST NOT collapse distinct explicit
periods.

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
For the default NULL occurrence, omitted and explicit-null temporal arguments SHALL preserve an
existing packet and SHALL create unknown only when the slot is empty. For an explicit period, an
occupied occurrence whose temporal packet differs SHALL fail unless `corrects_fact_id` names its
exact active row.

A temporal correction SHALL be an immutable-version replacement. `corrects_fact_id` MUST resolve to
an active row with the same subject, predicate, and object. An omitted/null period id SHALL inherit
that row's occurrence id, while a supplied id MUST match it. The writer SHALL lock that occurrence,
mark exactly the named row superseded, insert one active replacement with the complete desired
packet, keep the old packet unchanged, and carry its evidence forward. `corrects_fact_id` SHALL be
operation input only and SHALL NOT become stored temporal state.

An exact correction retry SHALL be idempotent when the named target is already superseded and the
one active row for that occurrence has the same complete desired packet and assertion fields: the
writer SHALL return the active successor as unchanged. A different successor SHALL make the retry
fail stale. A retracted target SHALL NOT serve as an idempotent correction witness.

Two concurrent identical replays SHALL converge on the same active row. Two different corrections
that name the same active fact id SHALL use compare-and-swap semantics: the transaction that
successfully locks the named row while active and commits first SHALL succeed, and the other SHALL
fail as a stale correction after observing that the named id is no longer active. The losing
transaction MUST leave no fact, evidence, coverage, approval-context, or graph-projection write. It
MUST NOT silently correct the winner's replacement.

**Owner-gate carry-forward (RFC 0017, binding):** when `subject` resolves to the owner
entity, `relationship_assert_fact()` MUST NOT write the triple directly; instead it MUST
emit a `pending_action` for owner approval, mirroring the existing pattern in
`roster/relationship/tools/contact_info.py::contact_info_add` per RFC 0017 §2.3. The owner
approves the pending action via the existing approval ceremony; only after approval does
the triple land as `validity='active'`. Non-owner subjects are written directly without the
approval hop.

The normalized temporal packet, resolved effective period id, and correction target SHALL survive
owner parking in `pending_actions.tool_args` because they are part of the assertion being reviewed.
The parked JSON SHALL contain all six temporal keys: JSON null for unknown/default values,
normalized UTC strings for concrete bounds, and canonical strings for UUIDs. Approval deduplication
and verification SHALL compare that whole canonical packet as well as the SPO identity and SHALL
reject any altered packet. Approved replay SHALL restore the parked temporal values together with
the server-recorded source, observation time, evidence, session, and approval action id. A
pre-temporal pending action with none of the keys SHALL normalize to the all-null/default packet. A
failed or stale replay SHALL roll back every fact, evidence, coverage, approval-context, and
graph-projection effect.

Before parking, the writer SHALL resolve the request against the current occurrence. For an ordinary
reassertion over a known default occurrence, the parked packet SHALL contain that row's stored values,
not all nulls. `relationship.fact_approval_context` SHALL add
`temporal_request_mode TEXT NULL CHECK (temporal_request_mode IN ('ordinary', 'explicit',
'correction'))` and `temporal_base_fact_id UUID NULL`. Approved replay SHALL verify that frozen mode
and base. A preserve action requires its base to remain active. A no-base ordinary create may insert
unknown only while the default slot is empty or already contains the identical unknown packet; a
known row appearing in that slot makes the replay stale. Later ordinary provenance replacement of an
approved known fact SHALL again preserve its packet.

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

Temporal admission SHALL cover every production mutation path, not only the central insert helper.
The transition's exact inventory SHALL include:

- central insert/supersession, SPO retraction, and preferred-channel helpers in
  `roster/relationship/tools/relationship_assert_fact.py`;
- owner-handle bootstrap in `src/butlers/owner_bootstrap.py`;
- subject/object collision, cardinality, and repoint updates in
  `roster/relationship/tools/entity_merge.py::merge_entity_pair`;
- legacy subject/object repoint updates in
  `roster/relationship/tools/contacts.py::contact_merge`;
- contact delete, verify, value update, and entity-forget mutations in
  `roster/relationship/api/router.py`;
- hard-delete FK cascades from Google and Steam companion-entity deletion; and
- historical direct DML in Relationship migrations 019, 027, and 028, which SHALL remain ordered
  before temporal expansion and SHALL NOT become post-cutover repair paths.

The implementation SHALL add a static production-DML inventory guard. Any direct
`relationship.entity_facts` INSERT, UPDATE, DELETE, or mutating helper absent from the exact
inventory SHALL block cutover. Every inventoried path SHALL either preserve each selected row's id,
occurrence id, bounds, precision, evidence, and projection as its operation permits, or reject an
unsupported/ambiguous request before its first write.

Occurrence-safe behavior SHALL be:

- Ordinary central-writer provenance reassertion preserves the active occurrence's packet. Explicit
  correction remains the only way to clear or change it.
- Owner bootstrap is insert-only unknown-default behavior. It SHALL no-op when any active occurrence
  of its SPO exists, and SHALL NOT create a default sibling beside an explicit occurrence.
- `retract_contact_info_fact` and hash-addressed contact delete/verify/update SHALL fail
  `temporal_occurrence_ambiguous` before writes when more than one active occurrence matches their
  SPO/hash selector. Exact-id lifecycle or verification updates SHALL preserve the selected packet.
  A different-value contact edit SHALL fail `temporal_mutator_unsupported` before retraction or
  approval parking when the selected row is temporal-bearing; same-value provenance reassertion
  SHALL preserve it.
- Preferred-channel set/clear and generic temporal assertion of `prefers-channel` SHALL remain
  fenced: any temporal intent, temporal-bearing current row, or multiple active occurrence SHALL fail
  before writes. The existing unknown-default singleton behavior MAY continue until `bu-4ss0u`
  defines period-aware policy for this single-valued predicate.
- Entity merge SHALL lock and plan every affected occurrence before any mutation, repoint each safe
  row without changing its packet, and update its graph projection in the same transaction. It SHALL
  NOT apply SPO-only or predicate-only confidence collapse. A collision in the projected final
  occurrence key SHALL fail `temporal_occurrence_collision` before any entity, contact, fact,
  evidence, or projection write.
- Legacy `contact_merge` SHALL preflight before its first contact/entity/fact write. If an affected
  row is temporal-bearing, multiple occurrences share an SPO, or the projected merge collides, it
  SHALL fail `temporal_mutator_unsupported`; its current best-effort/swallowed direct updates SHALL
  run only for the proven all-unknown/default singleton set until replaced by the compatible merge
  service.
- Entity forget SHALL intentionally retract every matched occurrence without altering its packet and
  SHALL remove every corresponding projection in the same transaction. Explicit Google/Steam
  companion-entity hard delete SHALL retain its destructive all-version FK cascade and SHALL remove
  all attached evidence/projections atomically rather than choosing an occurrence.

A row SHALL be temporal-bearing when any effective bound or precision is non-null or when its period
id is non-null. The later index cutover MUST remain unauthorized until the exact deployed image
contains the complete inventory and each compatible behavior or fence, old images are absent, the
static inventory is clean, and the named real-PostgreSQL transition/mutator tests pass.

This single-ingress contract preserves RFC 0006 schema isolation and RDF integrity.

#### Scenario: Direct SQL writes are blocked
- **WHEN** any butler other than relationship attempts `INSERT INTO relationship.entity_facts`
- **THEN** PostgreSQL MUST reject the statement on role permissions
- **AND** the only successful write path MUST be `relationship_assert_fact()` via MCP

#### Scenario: Omitted temporal arguments preserve legacy writer behavior

- **WHEN** an existing caller omits every temporal argument and no active default occurrence exists
- **THEN** the writer MUST create the NULL default occurrence with unknown lower and upper bounds
- **AND** existing idempotency and non-temporal provenance supersession behavior MUST remain valid
- **AND** the writer MUST NOT derive a bound from assertion or observation time

#### Scenario: Ordinary provenance reassertion preserves a known default packet

- **WHEN** an ordinary caller omits or sends null for every temporal argument while reasserting an
  existing default occurrence with known or unbounded effective fields
- **THEN** unchanged comparison and any non-temporal provenance replacement MUST use and copy the
  stored occurrence packet
- **AND** the request MUST NOT be rejected as an ambiguous unknown correction
- **AND** the stored packet MUST NOT be cleared to unknown
- **AND** clearing it to unknown MUST require a non-null `corrects_fact_id`

#### Scenario: Explicit null and omission are the same wire request

- **WHEN** one caller omits all six temporal arguments and another explicitly sends each as JSON null
- **THEN** both requests MUST normalize to the same ordinary no-temporal-intent mode
- **AND** both MUST create unknown only when the default occurrence is absent and preserve its packet
  when it exists
- **AND** they MUST take the same idempotency, approval-deduplication, persistence, and replay path
- **AND** no implementation may branch on optional-key presence

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

#### Scenario: Correction target alone corrects the interval to unknown

- **WHEN** a caller supplies a valid active `corrects_fact_id` and omits or sends null for every
  other temporal argument
- **THEN** the writer MUST select correction mode, inherit the target's occurrence id, and make both
  desired replacement bounds unknown
- **AND** it MUST NOT interpret the request as a legacy replay or a partial patch preserving old
  bounds

#### Scenario: Exact correction retry finds only its matching successor

- **WHEN** a correction is retried after its named target was superseded by the same complete desired
  packet and assertion fields
- **THEN** the writer MUST return the active successor for that occurrence as unchanged
- **AND** it MUST fail stale if the successor differs or the target was retracted

#### Scenario: Recording a known end does not retract the fact

- **WHEN** an explicit correction replaces an unknown or unbounded upper bound with a concrete
  `effective_to`
- **THEN** the corrected replacement MUST remain `validity='active'` even when that bound is in the
  past
- **AND** the prior row MUST be superseded only as the immutable earlier assertion version, not as a
  claim that the relationship was false during its represented interval

#### Scenario: Two concurrent corrections cannot overwrite each other

- **WHEN** two real PostgreSQL transactions are synchronized so both submit different temporal
  corrections naming the same active fact id before either correction commits
- **THEN** the transaction that successfully locks the named row while active and commits first MUST
  create the sole active replacement for the occurrence
- **AND** the other transaction MUST fail as stale without superseding the replacement
- **AND** the losing transaction MUST persist no partial fact, evidence, coverage, approval-context,
  or graph-projection effect

#### Scenario: Owner-approved replay preserves the reviewed temporal assertion

- **WHEN** a temporal assertion or correction is parked and later approved
- **THEN** the approved replay MUST use the parked normalized bounds, precisions, occurrence id, and
  correction target without accepting replacements from the dispatch caller
- **AND** the parked payload MUST preserve all six temporal keys in canonical form, including JSON
  nulls, so omitted and explicit-null proposals replay identically
- **AND** server-held approval context MUST preserve the resolved request mode and exact base fact id
  so an ordinary known-packet reassertion cannot replay as an unknown create or correction
- **AND** it MUST use `src`, `observed_at`, evidence, session, and action id from the server-held
  approval records
- **AND** an altered or stale replay MUST fail atomically

#### Scenario: Transition writer fails temporal intent closed before cutover

- **WHEN** the transition writer runs while `uq_ef_spo_active` still exists
- **THEN** omitted/all-null legacy writes MUST continue using the single active SPO slot
- **AND** any non-null temporal value or correction target MUST fail `temporal_cutover_pending`
  before approval parking, fact persistence, evidence, coverage, or projection

#### Scenario: New writer survives the real index transition

- **WHEN** the same new-writer SQL is executed against the expanded two-index schema and then the
  final occurrence-index-only schema
- **THEN** targetless conflict handling and locked re-read/CAS MUST work against both layouts
- **AND** the deployed old-writer inferred conflict statement MUST work before cutover and MUST be
  proven to fail after cutover, establishing why exact old-writer absence is a hard prerequisite

#### Scenario: Mutator inventory blocks incomplete cutover

- **WHEN** the production DML guard finds an unclassified direct `entity_facts` mutation/helper, or
  the deployed image lacks any required occurrence behavior or fence
- **THEN** temporal admission and removal of `uq_ef_spo_active` MUST remain blocked
- **AND** proof of the central writer alone MUST NOT satisfy the cutover gate

#### Scenario: Entity merge preserves every occurrence or writes nothing

- **WHEN** an entity merge would repoint subject/object references for multiple effective occurrences
- **THEN** every non-colliding row MUST retain its id, occurrence id, temporal packet, and evidence
  while its projection is updated in the same transaction
- **AND** the merge MUST NOT supersede rows merely because their post-merge SPO or predicate matches
- **AND** any final occurrence-key collision MUST fail before any entity, contact, fact, evidence, or
  projection write

#### Scenario: Legacy contact merge is fenced before partial mutation

- **WHEN** `contact_merge` would touch a temporal-bearing row, an SPO with multiple occurrences, or a
  projected occurrence collision
- **THEN** it MUST fail `temporal_mutator_unsupported` before its first contact/entity/fact write
- **AND** its best-effort direct repoint blocks MUST NOT swallow a temporal-safety failure

#### Scenario: Ambiguous contact lifecycle selector writes nothing

- **WHEN** SPO/hash-based contact retract, delete, verify, or update matches more than one active
  occurrence
- **THEN** the operation MUST fail `temporal_occurrence_ambiguous` before any write
- **AND** an exact single-row lifecycle/verification update MUST retain that row's temporal packet

#### Scenario: Preferred-channel mutation remains temporally fenced

- **WHEN** preferred-channel set/clear sees temporal intent, a temporal-bearing current row, or more
  than one active occurrence
- **THEN** it MUST fail before predicate-wide supersession, retraction, or insertion
- **AND** only the existing unknown-default singleton behavior MAY continue before `bu-4ss0u`

#### Scenario: Intentional all-occurrence lifecycle operations preserve history

- **WHEN** entity forget retracts all matching occurrences, or an explicit Google/Steam companion
  hard delete cascades through all fact versions
- **THEN** no path may choose one occurrence or rewrite a temporal packet
- **AND** fact lifecycle, evidence, and graph projection effects MUST commit or roll back atomically

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
