## ADDED Requirements

### Requirement: Bounded source-owned public channel projection

The identity subsystem SHALL read channel facts through the rebuildable `public.identity_channel_projection` row contract in RFC 0004 Amendment 4, while Relationship retains the sole canonical source. The projection MUST contain exactly the eligible literal active `has-handle`, `has-email`, `has-phone` and `has-website` facts, keyed by actual source fact UUID, with source subject/value/nullable primacy/creation time and only the existing routing context label. Private attribution, evidence, credentials and full metadata MUST NOT be projected. Current candidate adoption, legacy NULL attribution, transitory, temporal and entity-liveness semantics SHALL remain unchanged.

ID: REQ-entity-identity-004
Source: RFC 0004 Amendment 4 §§4.1–4.2; REQ-entity-identity-003; REQ-relationship-facts-006–007
Scope: v1-mandatory

#### Scenario: Eligible channel facts retain exact fields

- **WHEN** an eligible canonical source fact is projected
- **THEN** its actual source UUID, subject, predicate, canonical value, nullable primacy, source creation time and routing context MUST be preserved
- **AND** the reader MUST obtain current names, roles and transitory markers from the live public entity rather than copied snapshots
- **AND** private attribution/evidence and credential fields MUST remain absent

#### Scenario: Candidate exclusion preserves a real active companion

- **WHEN** a candidate handle and a distinct eligible active legacy-NULL-attribution handle exist
- **THEN** the candidate MUST be absent from the projection while the active companion remains resolvable under an admitted reader role
- **AND** genuine owner adoption MUST publish only its intended compatible active survivor without repairing historical attribution

#### Scenario: Multiple facts preserve cross-entity ambiguity

- **WHEN** several eligible facts describe the same handle on one or more live entities
- **THEN** projection MUST retain the individual source fact UUIDs without a new global handle uniqueness rule
- **AND** several facts on one entity MUST remain one identity candidate while distinct live entities remain ambiguous

### Requirement: Mandatory atomic channel projection maintenance

Every Relationship source mutation SHALL maintain its projection on the same connection and transaction through the source-owned trigger contract in RFC 0004 Amendment 4. Eligible writes/updates/adoptions MUST upsert the exact source row; eligibility loss, retraction, supersession and deletion MUST remove it. Projection failure MUST prevent that source operation from committing. Existing admitted authority, private lifecycle evidence, domain lock order, receipts and optional-secondary-effect semantics SHALL remain unchanged.

ID: REQ-entity-identity-005
Source: RFC 0004 Amendment 4 §4.3; REQ-relationship-facts-006–008
Scope: v1-mandatory

#### Scenario: Writer success and failure have committed readback

- **WHEN** an actual source writer succeeds or its mandatory projection update fails
- **THEN** a separate committed readback MUST show both intended source and projection effects or neither
- **AND** retry MUST NOT turn a projection-only savepoint failure into a committed source-only effect

#### Scenario: Updates and unchanged replay retain one row

- **WHEN** an existing source fact changes its projected fields or is replayed unchanged
- **THEN** its source-keyed projected row MUST reflect the committed current fields without duplicate rows
- **AND** replacement under a new source UUID MUST remove the old eligible projection in the actual source transaction

#### Scenario: Retraction and deletion remove identity

- **WHEN** a source fact is retracted, superseded, made ineligible or hard-deleted
- **THEN** its projection MUST disappear in the same source transaction
- **AND** both writer/deletion race orders MUST preserve committed source/projection consistency

#### Scenario: Entity cascade remains possible without granting derived writes

- **WHEN** an existing authorized public-entity deletion cascades to its source facts under a non-Relationship runtime role
- **THEN** the source-owned trigger-only maintenance function MUST remove derived rows atomically
- **AND** the deleting caller MUST gain neither general projection DML nor a callable foreign-read function
- **AND** recreating an entity UUID alone MUST NOT recreate a deleted source fact or owner evidence

#### Scenario: Exact owner adoption and losing replay retain lifecycle

- **WHEN** genuine owner admission adopts a candidate or a stale/colliding/rejected adoption loses
- **THEN** only the source's committed compatible active survivor MUST be projected
- **AND** a losing operation MUST leave source, projection and protected evidence/receipt effects unchanged

#### Scenario: Every actual source producer shares mandatory maintenance

- **WHEN** single/batch tools, mounted API, import, COPY, repair, merge or migration-owned source writes execute
- **THEN** their eligible committed source effects MUST have the same atomic projection effects
- **AND** runtime trigger disabling, replica bypass and source/projection TRUNCATE MUST be refused rather than creating a silent maintenance gap

### Requirement: Public-only identity reader equivalence

Inbound, single/bulk/normalized, recipient, owner-corroboration, connector-discretion and email-context identity readers SHALL use only public projection/entity data and preserve their existing channel aliases, canonicalization, phone suffix bounds, full distinct-live-entity ambiguity, forward ordering and context/approval behavior. Missing, ineligible, ambiguous or unavailable data MUST NOT obtain established identity or owner bypass through a private-table/definer, name or credential fallback. Identity resolution SHALL remain identification rather than cryptographic authentication.

ID: REQ-entity-identity-006
Source: RFC 0004 Amendment 4 §4.5; RFC 0017 §§2.2, 2.6–2.7; REQ-entity-identity-003
Scope: v1-mandatory

#### Scenario: Exact normalized and bulk results preserve candidate universes

- **WHEN** exact, email/Telegram/WhatsApp normalized or bounded phone-equivalent inputs are resolved singly or in bulk
- **THEN** the public-only result MUST preserve existing exact-before-fallback rules, alias mapping and complete distinct-live-entity ambiguity
- **AND** merged/deleted entities and ineligible source facts MUST not become matches

#### Scenario: Owner collision is evaluated before role filtering

- **WHEN** normalized equivalents match an owner and another live entity, or projection lookup is missing or unavailable
- **THEN** owner corroboration MUST refuse bypass without a private definer fallback
- **AND** a unique eligible live owner positive MUST still pass the existing dossier/approval contract
- **AND** primacy MUST NOT supply authorization

#### Scenario: Public forward selection and email context preserve policy

- **WHEN** a daemon selects a channel identifier or a non-owner email has an explicit context mismatch
- **THEN** the public read MUST retain existing nullable primacy/source-time delivery ordering and explicit context mismatch parking
- **AND** NULL context MUST remain unclassified without a new context-preference ranking

#### Scenario: Unknown and infrastructure failures preserve safe routing

- **WHEN** public lookup returns no identity or fails under the actual runtime role
- **THEN** inbound unresolved routing and the existing transitory reservation/durable owner-notification flow MUST remain available without contacts-table writes or false surfaced-entity claims
- **AND** outbound rules/parking MUST remain authoritative without granting owner bypass
- **AND** diagnostics MUST be bounded and exclude raw handles, bodies and private attribution

### Requirement: Least-privilege channel reads survive bootstrap

Projection access SHALL follow the exact named-role, table ACL and ENABLED/FORCED RLS contract in RFC 0004 Amendment 4. Named daemon readers and connector sender-discretion may SELECT; only the existing Relationship runtime role may maintain derived rows. PUBLIC and ordinary unknown roles MUST gain no projection access by schema placement, default grants or claimed owner/context; protected identity consumers MUST refuse shared-login fallback before querying, including an independently privileged administrative login. Actual bootstrap and migration replay MUST retain those restrictions without changing graph-projection grants or creating new credentials.

ID: REQ-entity-identity-007
Source: RFC 0004 Amendment 4 §4.4; heart-and-soul/security.md Identity Resolution
Scope: v1-mandatory

#### Scenario: Admitted role reads without foreign-schema privileges

- **WHEN** each explicitly named reader uses its actual protected runtime role
- **THEN** eligible public identity lookup MUST work without Relationship schema access
- **AND** missing optional/custom roles MUST not be created or implicitly granted access

#### Scenario: Ordinary non-writer cannot mutate or disable maintenance

- **WHEN** a named reader other than Relationship attempts projection INSERT, UPDATE, DELETE, TRUNCATE, trigger control or grant delegation
- **THEN** actual permissions and forced policies MUST refuse the operation
- **AND** an admitted Relationship source write MUST remain a positive atomic-maintenance case

#### Scenario: Bootstrap replay does not reopen public grants

- **WHEN** the real core/Relationship migration chain and database bootstrap replay broad public grants
- **THEN** the table-specific final ACL/policy installer MUST retain only its enumerated access
- **AND** actual SELECT, prohibited DML and TRUNCATE negatives MUST be rechecked under real runtime roles after replay

#### Scenario: Claimed role and trusted-login limits are explicit

- **WHEN** an ordinary non-reader queries the projection or a protected identity consumer attempts shared connecting-login fallback, including with caller-asserted owner/context fields
- **THEN** SQL MUST deny the ordinary non-reader and consumer admission MUST refuse the shared-login fallback until the actual protected pool selects an admitted runtime role
- **AND** the documented trusted admin, role-membership, table-owner and BYPASSRLS limits MUST not be represented as new credential or process isolation

### Requirement: Verified resumable channel backfill and safe recovery

A backfill SHALL install mandatory source maintenance before seeding and use bounded, idempotent source-keyed batches with RFC 0004 Amendment 4's existing entity/handle lock order, NOWAIT source synchronization, locked reread and finite replanning. Cutover MUST require exact source-key/field parity and real-role reader equivalence, not counts alone. Restore or rollback MUST preserve canonical facts, credentials, private attribution and adoption receipts; uncertain projection freshness SHALL leave identity unavailable/unresolved until authorized reconciliation, without restoring private reads or widening grants.

ID: REQ-entity-identity-008
Source: RFC 0004 Amendment 4 §4.6; REQ-relationship-facts-006–007
Scope: v1-mandatory

#### Scenario: Complete backfill proves actual population and reader parity

- **WHEN** a backfill reports success and requests cutover
- **THEN** it MUST prove the exact eligible source UUID set and every projected field plus actual exact/normalized/bulk/forward/owner/context reader equivalence under real roles
- **AND** its bounded receipt MUST identify environment/code/migration/population and omit raw handles

#### Scenario: Batch contention or crash is incomplete and replayable

- **WHEN** source synchronization refuses, the complete planned lock set changes, or a batch fails or crashes
- **THEN** that batch MUST roll back with a bounded checkpoint and finite retry/replanning rather than a completion claim
- **AND** resumed source-keyed replay MUST not duplicate rows or overwrite newer source effects

#### Scenario: Concurrent source change cannot be resurrected by stale backfill

- **WHEN** assertion, adoption, rotation, retraction or deletion races a backfill/cleanup batch in either order
- **THEN** table synchronization and the source trigger MUST preserve the current committed eligible set without phantom insertion loss or resurrection
- **AND** temporary bounded writer blocking MUST be declared instead of a silently widened lock timeout

#### Scenario: Core-only deployment cannot invent source completeness

- **WHEN** the Relationship source is absent or the projection is only partly populated
- **THEN** infrastructure MAY retain an empty public table but MUST NOT claim source parity or completed cutover
- **AND** reader misses MUST remain unresolved without a private fallback

#### Scenario: Restore and rollback retain canonical source and authority

- **WHEN** restored source/projection revision consistency or freshness cannot be established, or code is rolled back
- **THEN** consumers MUST remain unavailable/unresolved until a separately authorized consistent reconciliation
- **AND** rollback MUST not drop a table beneath readers, restore foreign reads, widen grants or continue source writes with maintenance disabled
- **AND** rebuilding MUST not refill historical NULL attribution or mint private owner/lifecycle evidence
