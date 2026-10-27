## MODIFIED Requirements

### Requirement: Relationship Butler Registered Tool Surface

The relationship butler SHALL expose its currently approved manifesto-owned
personal CRM tool set. The original eight relationship-module groups
(`contacts`, `contacts_extended`, `interactions`, `relationships`, `social`,
`notes`, `tracking`, and `management`) own 62 tools. The mandatory
`relationship_assert_fact` approval-dispatch handler registers unconditionally,
for 63 relationship-module handlers in total. The `entity` group SHALL contain exactly six reads after the adopted
six-read/three-write split. The roster SHALL select those six reads and leave
`entity_write` inactive, including `entity_update`,
`relationship_record_coverage`, and `entity_set_posture`. It MUST NOT select a
mixed read/write entity group.
The 62 tools in those eight groups SHALL include 60 existing domain tools and the two existing identity handlers `identity_resolve_channels` and `identity_assert_sender_channel`. When separately selected, the `contacts_ingress` group SHALL contain exactly `contacts_report_channel` and `contacts_resolve_channel`, making 64 group-owned and one unconditional handler, for 65 Relationship-module handlers with Contacts ingress. Their registration SHALL preserve the complete Contacts backfill/identity and central-writer provenance, candidate, owner, policy, temporal and privacy contracts and SHALL confer no source-authentication or authority exemption. Closed owner decision `bu-t89d9m` option A (2026-10-20 14:36 UTC) authorizes implementation of the exact six-read/three-write split and source roster selection through normal PR review; it does not earn deployment, runtime activation, temporal cutover or real-Postgres proof.

ID: REQ-butler-relationship-002
Source: Existing Relationship manifesto and baseline Registered Tool Surface; add-meeting-debrief approved tools; bu-ss4o2u adopted Contacts P0-P6 and seven outcomes, actual fixed grouped tool inventory and core Tool Group Filtering contract; closed `bu-t89d9m` option A (2026-10-20 14:36 UTC)
Scope: v1-mandatory

#### Scenario: Exact current registered inventory

- **WHEN** a runtime instance is spawned for the relationship butler with the original eight-group allowlist
- **THEN** all 60 existing domain tools and both existing identity handlers owned by those eight configured groups SHALL be registered
- **AND** the inventory SHALL include `contact_create`, `contact_update`, `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`, `relationship_add`, `relationship_list`, `relationship_remove`, `date_add`, `date_list`, `upcoming_dates`, `note_create`, `note_list`, `note_search`, `interaction_log`, `interaction_list`, `fact_set`, `fact_list`, `feed_get`, `meeting_debrief_pending`, and `meeting_debrief_answer`
- **AND** `relationship_assert_fact` SHALL be the additional mandatory unconditional handler, making 63 legacy Relationship-module handlers total
- **AND** `entity_resolve`, `entity_get`, `entity_neighbors`, `relationship_fact_evidence`, `relationship_predicate_coverage`, `relationship_lookup`, `entity_update`, and `relationship_record_coverage` SHALL all be absent
- **AND** no bare `entity_create` MCP tool or alias SHALL be registered
- **AND** the separately configured memory module MAY expose `memory_entity_create` under that canonical prefixed name

#### Scenario: Restricted legacy groups retain their exact registered behavior

- **WHEN** Relationship is registered with the original eight-group allowlist without `contacts_ingress`
- **THEN** its original 63 actual handlers SHALL remain registered, including the unconditional central writer and both existing identity handlers
- **AND** neither Contacts ingress handler SHALL appear in that restricted configuration

#### Scenario: Contacts ingress does not activate the held entity surface

- **WHEN** the configured Relationship group allowlist selects the eight legacy groups plus `contacts_ingress`
- **THEN** both fixed owning Contacts operations SHALL be callable with all original handlers preserved, for exactly 65 Relationship-module handlers
- **AND** the six unselected reads, `entity_update`, `relationship_record_coverage`, and bare `entity_create` SHALL remain absent
- **AND** the closed Entity implementation authority SHALL NOT be treated as completion of any temporal owner, cutover, deployment or real-Postgres hold
- **AND** neither unavailable lookup nor candidate/pending approval SHALL be treated as authoritative empty lookup or active success

#### Scenario: Tool inventory after the read-only split

- **WHEN** the separately owner-approved implementation activates the read-only Relationship `entity` group after its exact six-read/three-write split is implemented
- **THEN** it SHALL preserve every handler in the then-current configured inventory and add exactly the six Relationship entity reads (`entity_resolve`, `entity_get`, `entity_neighbors`, `relationship_fact_evidence`, `relationship_predicate_coverage`, and `relationship_lookup`): 69 total on the original eight-group configuration or 71 total when Contacts ingress is also selected
- **AND** `relationship_assert_fact` SHALL remain the additional mandatory unconditional handler, included exactly once in those totals
- **AND** `entity_update`, `relationship_record_coverage`, and `entity_set_posture` SHALL remain absent from the active surface
- **AND** the separately configured memory module MAY expose `memory_entity_create` under that canonical prefixed name
- **AND** no bare `entity_create` MCP tool or alias SHALL be registered
