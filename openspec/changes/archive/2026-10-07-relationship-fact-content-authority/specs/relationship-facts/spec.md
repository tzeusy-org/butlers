## MODIFIED Requirements

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

## ADDED Requirements

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
