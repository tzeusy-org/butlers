# core-tool-discovery Specification

## Purpose

Defines the landed immutable tool catalog, exhaustive descriptive presentation inventory, stored exposure-policy accessor, and canonical skill identity. These contracts preserve existing handler authority without claiming an installed per-attempt planner, adapter filter, native search, or receipt pipeline.

## Requirements

### Requirement: Immutable Registered Tool Catalog

The daemon SHALL snapshot final registered FastMCP definitions into an immutable,
callable-free catalog after core/module registration and approval wrapping.
Descriptors SHALL retain canonical identity, ownership, typed schema, description,
presentation classification, sensitivity, and descriptive authorization metadata.
Schema, description, and generation digests SHALL be deterministic for the same definitions.

ID: REQ-core-tool-discovery-001
Source: RFC 0027 §Layered Surface Model; heart-and-soul/architecture.md §Why Tool Surface Discipline Matters
Scope: v1-mandatory

#### Scenario: Final wrapped definitions are snapshotted

- **WHEN** the catalog is built from FastMCP's public list interface
- **THEN** descriptors SHALL reflect the final registered schemas and descriptions without retaining handler callables
- **AND** nested definition values SHALL be immutable
- **AND** duplicate canonical names or a registered tool without an owner SHALL be rejected

#### Scenario: Dark-build definitions do not become live tools

- **WHEN** finalization temporarily includes RFC 0038 dark-build tools in the definition snapshot
- **THEN** it SHALL disable those tools again on FastMCP after snapshot construction, including when construction fails
- **AND** catalog presence SHALL NOT make them listable or callable by current classify-and-route sessions

#### Scenario: Catalog construction leaves invocation unchanged

- **WHEN** a catalog describes a tool wrapped for approval
- **THEN** ordinary FastMCP invocation SHALL still resolve through that registered wrapper
- **AND** constructing or reading the catalog SHALL NOT register an alternate callable

### Requirement: Presentation Inventory Preserves Handler Authority

The checked-in presentation inventory SHALL classify the executable core/module
union by canonical name and owner. Validation SHALL reject duplicate, missing,
stale, and owner-drifted declarations. Classification SHALL NOT change canonical
FastMCP listing, registration gates, approvals, module state, or handler authority.
The catalog SHALL preserve later authorization declarations and conservative
unknown-output provenance without treating them as broker execution.

ID: REQ-core-tool-discovery-002
Source: RFC 0027 §LLM Visibility Classification, §Adapter-Owned Search Corpus and Model Presentation; heart-and-soul/security.md §Session Sandboxing
Scope: v1-mandatory

#### Scenario: Infrastructure classification is metadata only

- **WHEN** the inventory marks `route.execute` or `cancel_session` as not LLM-presentable
- **THEN** canonical FastMCP listing and existing infrastructure invocation SHALL remain unchanged
- **AND** metadata alone SHALL NOT claim an installed adapter filter or native search implementation

#### Scenario: Inventory drift is detected

- **WHEN** the executable tool/owner union differs from the checked-in inventory
- **THEN** inventory validation SHALL reject the mismatch rather than certify exhaustive classification

#### Scenario: Authorization metadata cannot grant authority

- **WHEN** a descriptor is classified complete or grantable
- **THEN** that state SHALL describe definition eligibility only
- **AND** existing call-time checks SHALL continue to govern invocation

### Requirement: Stored Tool Exposure Policy

The runtime-config control plane SHALL retain `eager_filtered` and `auto` as the
closed stored exposure-policy values, defaulting to `eager_filtered`. The direct
policy accessor SHALL read the DB on each call rather than rely on the general
runtime-config TTL cache. This storage/accessor contract SHALL NOT imply an
implemented per-invocation planner or native discovery consumer.

ID: REQ-core-tool-discovery-003
Source: RFC 0027 §Exposure Planning State Machine; heart-and-soul/vision.md #5
Scope: v1-mandatory

#### Scenario: Direct policy read bypasses cached configuration

- **WHEN** `RuntimeConfigAccessor.get_tool_exposure_policy()` is called after a committed policy update and the DB read succeeds
- **THEN** it SHALL query the DB directly and return the valid stored policy
- **AND** it SHALL NOT reuse the general `get()` TTL snapshot

#### Scenario: Legacy row missing the policy column is conservative

- **WHEN** an existing row has no exposure-policy field
- **THEN** the direct accessor SHALL return `eager_filtered`

### Requirement: Canonical Skill Identity Cannot Expand Authority

Skill identity resolution SHALL use `.agents/skills/<name>/SKILL.md` as the canonical
source and retain its content digest. A `.claude/skills` compatibility alias SHALL
resolve to that same physical source or be rejected. Reading this guidance SHALL
NOT register tools, alter presentation metadata, or bypass approval behavior.

ID: REQ-core-tool-discovery-007
Source: RFC 0027 §Skills and Tools; RFC 0002 §Skills Infrastructure
Scope: v1-mandatory

#### Scenario: Compatibility alias preserves canonical identity

- **WHEN** a skill has a `.claude/skills` compatibility alias
- **THEN** resolution SHALL accept only an alias to the canonical physical source
- **AND** a forked, unreadable, invalidly named, or missing canonical source SHALL be rejected

#### Scenario: Guidance remains separate from tools

- **WHEN** skill identities are listed or resolved
- **THEN** the result SHALL contain canonical source identifiers and content digests
- **AND** identity resolution SHALL NOT change FastMCP registration or handler authority
