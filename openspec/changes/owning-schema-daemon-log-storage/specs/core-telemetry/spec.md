## ADDED Requirements

### Requirement: Owning-schema structured daemon log storage

For each validated configured butler/staffer owning schema migrated by the shared core chain, structured daemon log storage SHALL provide the existing compatible `butler_logs` relation and timestamp-descending/level indexes within that schema. The relation SHALL retain the existing eight-column shape, four-level constraint, nullable structured metadata and append-only write semantics. A new forward migration SHALL establish absent storage and preserve compatible existing rows, identities, sequence state and indexes on repeated execution; it SHALL visibly refuse incompatible preexisting storage rather than overwrite or falsely adopt it. The writer and selected dashboard reader SHALL resolve only the owning log relation through their existing authorized pool identities. An absent/wrong owning path, unavailable storage or unverified runtime authority SHALL not be disguised as successful empty data or satisfied by public/sibling log storage. Core-only operation without a configured butler target SHALL remain supported without creating/adopting public log storage as proof of a butler repair. Migration and source rollback SHALL preserve preexisting compatible log data. Existing logger failure visibility/nonfatal caller behavior, contextual isolation, API owner authentication, credential redaction, file/console telemetry and cross-schema restrictions SHALL remain unchanged. This guarantee grants no new privilege, role, fleet-log aggregation, retention policy or operational deployment authority.

ID: REQ-core-telemetry-016
Source: bu-hc9n55 full adopted preserving readiness design/seven acceptance outcomes, genuinely qualified C94+mode1; existing core-telemetry structured logging, dashboard-butler-management full Logs tab/five scenarios, butler-base-spec isolation/migration, database-security runtime/dashboard pool authority, RFC0001/0005/0006/0030
Scope: v1-mandatory

#### Scenario: Previously omitted owning schema is repaired forward
- **WHEN** the current validated concierge owning target advances from its previous core head through the new forward repair
- **THEN** compatible structured log storage and both existing indexes exist in concierge
- **AND** applied historical revisions and all foreign schema bodies/data remain unchanged by the repair

#### Scenario: Newly configured target uses the framework's own schema contract
- **WHEN** a valid newly configured butler target follows the complete discovered core chain
- **THEN** its log storage is created in that target through the same validated owning path
- **AND** no extra hardcoded roster copy or arbitrary catalog sweep supplies authority

#### Scenario: Repeated repair preserves compatible data and rejects incompatibility
- **WHEN** the forward repair repeats against compatible preexisting storage containing a committed synthetic log row
- **THEN** the row, identity/sequence state, constraints and indexes remain intact
- **AND** an incompatible preexisting relation visibly refuses without destructive replacement and succeeds only after the separately correct prerequisite is restored

#### Scenario: Core-only migration keeps a truthful target boundary
- **WHEN** a supported core-only migration has no configured butler target
- **THEN** its existing supported migration behavior is retained
- **AND** public or unrelated log storage cannot stand in for a successful concierge repair

#### Scenario: Runtime role and durable owning readback are enforced
- **WHEN** the real migrated runtime acquires under its verified existing owning role and writes a uniquely identified synthetic log entry
- **THEN** the owning relation and sequence privileges permit that write and a separate acquisition reads the committed structured row
- **AND** release/reset/reacquisition and actual selected API pool preserve the same ownership without role/grant widening

#### Scenario: Decoys and missing dependencies cannot fake an empty or healthy viewer
- **WHEN** public/sibling decoys exist and the owning relation/path/authority is missing or unavailable
- **THEN** the writer's existing visible nonfatal failure and reader's failed-load distinction remain truthful
- **AND** no decoy supplies the owning row, no authentication bypass is introduced, and genuine successful empty query results remain empty

#### Scenario: Logger context and privacy companions remain intact
- **WHEN** own, foreign-context and logging-error records exercise the registered handler with ordinary structured metadata
- **THEN** only the authorized own-context record is forwarded, recursion is excluded and supported timestamp/request/source/metadata behavior is preserved
- **AND** existing credential redaction, httpx/httpcore WARNING settings, file/console logs and fatal migration readiness semantics remain intact

#### Scenario: Rollback retains the compatible append-only store
- **WHEN** the previous source runs against the compatible repaired store or the new repair is downgraded through its documented preserving operation
- **THEN** existing log rows, identities/sequence state and compatible relation/indexes remain available
- **AND** destructive removal, deployed restart, credential changes and new operational authority remain separately unreleased
