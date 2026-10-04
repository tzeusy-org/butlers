## Purpose

Provide deterministic fleet-wide relationships over shared entity anchors through a source-owned public projection, with honest sensitivity accounting and no per-question LLM fan-out. Preserve the complete RFC0031 contract, including mandatory dossier and substrate outcomes that remain incomplete.

## ADDED Requirements

### Requirement: Stable source provenance and projection identity
The graph SHALL identify each projected assertion by its canonical `source_schema`, `source_table` and UUID `source_id`, with at most one projection for that natural key. Each projection SHALL have a UUID identity, source-owned entity anchors and creation/update timestamps; it SHALL record an assertion without replacing its canonical source or entity identity.

ID: REQ-entity-graph-001
Source: RFC0031, Table and Write-Behind Contract; RFC0004 entity identity
Scope: v1-mandatory

#### Scenario: Repeat projection of the same source assertion
- **WHEN** a live writer or historical backfill projects the same source schema, table and row again
- **THEN** it SHALL converge on one projection for that natural key rather than create duplicate edges
- **AND** another source row's provenance SHALL remain distinct even if its relationship has the same anchors and predicate

### Requirement: Atomic eligible source lifecycle projection
Writers of eligible entity-to-entity assertions in Relationship facts, memory facts/rules and commitments MUST project the corresponding insertion, supersession, retraction or deletion in the same outer transaction as the canonical source mutation. A projection failure MUST roll back that source mutation. Eligibility and assertion lifecycle SHALL follow the owning source contract; a literal or unary source without two entity anchors SHALL NOT acquire an invented graph relationship.

ID: REQ-entity-graph-002
Source: RFC0031, Write-Behind Contract and S2; owning Relationship, Memory and commitment lifecycle contracts
Scope: v1-mandatory

#### Scenario: Source and projection commit together
- **WHEN** an eligible source assertion is inserted or its assertion lifecycle changes
- **THEN** its corresponding projection SHALL be inserted, updated or retracted within the source transaction
- **AND** readers SHALL NOT be left with a committed source mutation whose required projection effect failed

#### Scenario: Projection failure prevents source commit
- **WHEN** a required projection write fails after a source mutation has begun
- **THEN** the outer transaction MUST roll back the source mutation and its projection effects
- **AND** the writer MUST NOT silently swallow the failure and commit divergent source state

#### Scenario: Commitment closure preserves a historical assertion
- **WHEN** a directed commitment is resolved while its canonical assertion remains a true historical source row
- **THEN** closure SHALL NOT be treated as source-row deletion or retraction merely because the commitment is no longer active
- **AND** actual source retraction and deletion SHALL continue to follow their owning lifecycle contracts

### Requirement: Count-only sensitivity withholding
The projection SHALL use the `normal`, `pii` and `confidential` sensitivity vocabulary. Eligible `pii` and `confidential` assertions SHALL persist count-only stubs containing the subject anchor, source provenance, sensitivity and `withheld_reason='sensitivity'`, with NULL predicate and object. Live edges SHALL have a predicate and object anchor with NULL withheld reason; the two payload shapes MUST be mutually exclusive.

ID: REQ-entity-graph-003
Source: RFC0031, Withheld Stub Edges; existing Memory write-time sensitivity exclusion
Scope: v1-mandatory

#### Scenario: Sensitive assertion remains countable without payload
- **WHEN** an eligible assertion has a sensitivity excluded from a content-bearing projection
- **THEN** its stub SHALL contribute to withheld coverage for its subject without persisting its predicate or object anchor
- **AND** traversal SHALL never use that stub as a relationship hop

#### Scenario: Normal assertion carries its relationship
- **WHEN** an eligible normal assertion is projected
- **THEN** its live edge SHALL retain its actual predicate and object anchor with NULL withheld reason
- **AND** malformed combinations of withheld and live payload SHALL be rejected by the substrate

### Requirement: Bounded idempotent historical backfill
Historical recovery MUST project eligible existing source assertions in bounded batches under their owning source context, using the same natural-key identity, sensitivity and assertion-lifecycle rules as live writers. Repeated backfill SHALL NOT duplicate edges or resurrect superseded/retracted assertions. Recovery SHALL preserve historical assertions whose source lifecycle retains them, including commitment closure rather than assuming every closed commitment is retracted.

ID: REQ-entity-graph-004
Source: RFC0031, Write-Behind Contract and S2 historical backfill; owning source lifecycle contracts
Scope: v1-mandatory

#### Scenario: Backfill and live writer encounter the same assertion
- **WHEN** a bounded historical batch is repeated or overlaps a live projection of the same eligible source assertion
- **THEN** natural-key convergence SHALL prevent duplicate edges
- **AND** the result SHALL preserve the source's current assertion lifecycle and sensitivity shape

#### Scenario: Recovery respects source lifecycle
- **WHEN** historical source rows include retracted facts and retained resolved commitment assertions
- **THEN** recovery SHALL exclude retracted assertions and preserve eligible retained historical assertions
- **AND** an active Relationship assertion SHALL NOT be silently narrowed to effective-now by the graph recovery surface

### Requirement: Bounded zero-LLM graph walk
`entity_graph_walk` SHALL traverse live projected edges using public graph data only, without LLM calls, private-schema reads or per-hop MCP fan-out. It SHALL accept `out`, `in` or `both` direction and optional predicate filtering, enforce a hop depth of 1 through 6 and return at most 500 distinct reached entities. Results SHALL identify each entity's nearest hop, exclude the starting entity, terminate cycles and prioritize nearer hops when truncated.

ID: REQ-entity-graph-005
Source: RFC0031, Traversal Shape and S3; observed bounded graph walk contract
Scope: v1-mandatory

#### Scenario: Filtered multihop walk with a cycle
- **WHEN** a valid bounded walk reaches an entity along multiple paths or encounters a cycle
- **THEN** it SHALL return each reached entity once at its nearest hop within the selected direction and predicates
- **AND** it SHALL exclude the starting entity and all withheld edges

#### Scenario: Depth and result limits
- **WHEN** the caller requests a walk beyond supported depth or a result limit above the hard cap
- **THEN** invalid depth SHALL be rejected and the result limit SHALL remain bounded by 500
- **AND** truncation SHALL retain nearer entities before farther entities

#### Scenario: No reachable relationships
- **WHEN** no live edge is reachable within the requested valid bounds
- **THEN** the walk SHALL return an empty result without fabricating relationships or invoking another source

### Requirement: Shortest path returns exact edge receipts
`entity_graph_path` SHALL return an ordered shortest live path between two entity anchors within the supported depth, direction and predicate bounds, with receipts identifying the actual public edge IDs and anchors. It SHALL return an explicit missing result rather than a guessed partial path when no path exists, and a zero-hop empty path for identical endpoints.

ID: REQ-entity-graph-006
Source: RFC0031, Traversal Shape and S3; observed path receipt contract
Scope: v1-mandatory

#### Scenario: Shortest live path exists
- **WHEN** valid bounded traversal connects the requested source and target entities
- **THEN** the path SHALL contain the actual traversed edge receipts in source-to-target order at minimum hop count
- **AND** withheld stubs SHALL never become path receipts

#### Scenario: Missing and identical endpoints
- **WHEN** the endpoints are identical or no live path connects distinct endpoints within valid bounds
- **THEN** the result SHALL distinguish the identical-endpoint zero-hop path from a missing path
- **AND** a missing path SHALL contain no guessed or partial relationship chain

### Requirement: Graph tools respect existing registration authority
The walk and path tools SHALL belong to the `graph` core tool group and obey the existing core group allowlist and runtime tool-surface authority. No butler type or name alone SHALL confer registration outside that group authority. Graph registration SHALL preserve existing always-on infrastructure controls.

ID: REQ-entity-graph-007
Source: RFC0031 S3; RFC0002 core-tool groups and existing core-daemon/runtime tool-discovery source
Scope: v1-mandatory

#### Scenario: Graph group included or omitted
- **WHEN** the configured core tool groups include graph, omit graph or use the existing all-groups setting
- **THEN** graph walk and path SHALL follow that same group authority
- **AND** omitting graph SHALL NOT disable always-on routing and cancellation infrastructure

### Requirement: Catalog coverage uses actual graph accounting
Entity-anchored catalog search results SHALL expose known and withheld relationship coverage drawn from actual projected edges under the catalog's existing server-held read authority. Counts SHALL include each incident live edge once whether the entity is subject or object, and count a withheld stub only for its persisted subject. Unanchored results SHALL omit graph coverage; an anchored entity without projected edges SHALL preserve the existing absent-coverage representation.

ID: REQ-entity-graph-008
Source: RFC0031 S4; existing memory catalog read authority and observed coverage response
Scope: v1-mandatory

#### Scenario: Live and withheld incident edges
- **WHEN** an authorized catalog result is anchored to an entity with live incident edges and withheld subject stubs
- **THEN** coverage SHALL report the actual known and withheld counts separately
- **AND** no withheld predicate or object SHALL be reconstructed to calculate or display coverage

#### Scenario: Missing anchor or missing projected edges
- **WHEN** a catalog result has no entity anchor or its anchor has no projected edges
- **THEN** the result SHALL distinguish omitted coverage for the unanchored row from the existing absent-coverage value for the anchored row
- **AND** neither case SHALL receive fabricated relationship counts

### Requirement: Graph readers preserve source and identity boundaries
Traversal and dossier readers MUST NOT write source or projection rows or query private source schemas to reconstruct relationships. The graph SHALL remain an assertion projection without belief revision, contradiction resolution or ontology unification. Existing entity identity, channel resolution and memory-catalog anchors SHALL remain unchanged; the distinct `entity_neighbors` default Google-account exclusion SHALL remain governed by entity-identity rather than be generalized into a new graph rule.

ID: REQ-entity-graph-009
Source: RFC0031, Governing Intent, Non-Goals and Integration; vision rules 3 and 4; entity-identity
Scope: v1-mandatory

#### Scenario: Read and retry a public graph result
- **WHEN** a reader performs or repeats a walk, path or dossier read
- **THEN** it SHALL derive its graph result from the public projection without writing rows or acquiring peer-schema source authority
- **AND** it SHALL preserve canonical identity and report asserted relationships rather than resolve conflicting predicates into an invented truth

### Requirement: [TARGET-STATE] Entity dossier receipts and honest coverage
`GET /api/entities/{id}/dossier` SHALL return per-source receipts for the entity's known relationships and an actual known/withheld coverage statement drawn from the projection. It MUST NOT fabricate counts, disclose withheld predicates or objects, or use the endpoint as authority to read canonical private content. It SHALL use existing dashboard authentication and response conventions and preserve truthful empty versus unavailable behavior.

ID: REQ-entity-graph-010
Source: RFC0031 S5; existing dashboard authentication and response conventions
Scope: v1-mandatory

#### Scenario: Dossier contains live relationships and sensitive stubs
- **WHEN** an authenticated dossier read finds live projected assertions from multiple sources and withheld subject stubs
- **THEN** it SHALL return receipts identifying actual source provenance and the corresponding known and withheld counts
- **AND** it SHALL disclose no predicate, object or private source payload from a withheld assertion

#### Scenario: Empty or unavailable dossier
- **WHEN** an existing entity has no projected relationships or the required graph read is unavailable
- **THEN** the response SHALL distinguish a successful empty dossier from unavailability using the existing response conventions
- **AND** it SHALL NOT substitute invented receipts, counts or a private-source fan-out

### Requirement: [TARGET-STATE] Entity detail graph dossier panel
EntityDetailPage SHALL consume the dossier API and present its actual per-source receipts and known/withheld coverage. The panel SHALL preserve existing entity identity, neighbor and activity surfaces and the dashboard's design-language conventions. It SHALL distinguish loading, successful empty and unavailable/retry states without reconstructing hidden relationships or presenting an unrelated panel as a graph dossier.

ID: REQ-entity-graph-011
Source: RFC0031 S6; dashboard-relationship and dashboard-design-language
Scope: v1-mandatory

#### Scenario: Read the entity dossier in the dashboard
- **WHEN** the owner opens an entity's dossier panel and the API returns live receipts and withheld accounting
- **THEN** the panel SHALL render those actual receipts and counts without exposing withheld relationship content
- **AND** existing entity navigation and activity surfaces SHALL remain usable

#### Scenario: Dossier loading, empty and failure states
- **WHEN** the dossier is loading, successfully empty or unavailable
- **THEN** the panel SHALL present the corresponding distinct state under the existing dashboard conventions
- **AND** a retry SHALL remain a read and SHALL NOT manufacture relationships or change source authority

### Requirement: Migrated projection substrate and cooperative grants
The migrated public projection MUST enforce source natural-key uniqueness, real subject/live-object entity foreign keys with deletion cascade, sensitivity vocabulary and mutually exclusive live/withheld payloads, and retain subject, object and withheld-subject indexes. Core-only installation and shared core replay SHALL retain a compatible substrate. Existing graph writer roles SHALL retain SELECT, INSERT, UPDATE and DELETE needed for source-owned projection effects; these cooperative grants SHALL NOT be represented as row ownership enforcement or new private-schema authority. Catalog DELETE restrictions SHALL remain governed separately by database-security's existing catalog/central-GC contract.

ID: REQ-entity-graph-012
Source: RFC0031, Table, S1 and Grant Model; RFC0006 role isolation; database-security Write Authorization Matrix
Scope: v1-mandatory

#### Scenario: Invalid anchors or withheld payload
- **WHEN** a projection references a missing entity, duplicates source identity or mixes withheld and live payload
- **THEN** the migrated substrate MUST enforce its actual foreign-key, uniqueness and payload-shape rules
- **AND** genuine live and count-only withheld rows SHALL remain valid

#### Scenario: Installation, replay and graph source mutation
- **WHEN** core is installed alone, replayed for another schema or followed by bootstrap rerun and a legitimate graph source mutation
- **THEN** the substrate and intended graph writer operations SHALL remain compatible
- **AND** graph DELETE needs SHALL NOT silently change the separate catalog DELETE posture or widen private-schema access
