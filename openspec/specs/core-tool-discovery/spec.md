# core-tool-discovery Specification

## Purpose
Defines the immutable catalog, presentation inventory and skill identity that describe a butler's registered tool surface without changing it.

## Requirements

### Requirement: Immutable Tool Catalog

For every butler daemon, the system SHALL build one immutable, callable-free
catalog of the final registered FastMCP tool surface after core and module
registration and approval wrapping. Each descriptor SHALL carry the canonical
name, owning module, group, namespace, LLM-presentable flag, load posture
(`eager` or `deferred`), classification-complete flag, frozen input schema and
description with their digests, and argument-sensitivity declarations. The
catalog SHALL expose a generation digest that changes when any descriptor,
schema, description, or classification changes. The catalog SHALL describe the
registered surface only: it SHALL NOT register, alias, hide, or authorize a
handler, and canonical FastMCP `tools/list` and handler-level call authority
SHALL remain unchanged.

ID: REQ-core-tool-discovery-001
Source: RFC 0027 §Layered Surface Model, §Tool Metadata Contract (bu-ondtw.3, PR #4244); RFC 0038 §Hard cutover (D4 catalog metadata)
Scope: v1-mandatory

#### Scenario: Catalog snapshots the final definitions

- **WHEN** the daemon has finished registering and wrapping its tools
- **THEN** the catalog contains one descriptor per listed canonical name, read through FastMCP's public list interface
- **AND** it retains no handler callable
- **AND** the frozen schema and description reflect the post-approval-wrapping definition

#### Scenario: Generation digest is stable and drift-sensitive

- **WHEN** the catalog is read repeatedly, including concurrently, for an unchanged registered surface
- **THEN** every read returns the same generation digest
- **AND** a change to any name, owner, classification, schema, or description yields a different digest

#### Scenario: Unclassified tool keeps the safe baseline

- **WHEN** a registered tool has no presentation metadata
- **THEN** its descriptor is LLM-presentable with an eager load posture and `classification_complete` false
- **AND** the catalog reports classification incomplete without removing the handler

#### Scenario: Invalid metadata fails closed

- **WHEN** declared metadata is partial, names a different canonical tool or owner, or carries an invalid group, namespace, presentable flag, or load posture
- **THEN** building the catalog fails with a typed error
- **AND** a registered tool with no owner or a duplicate listed name also fails the build

### Requirement: Exhaustive Presentation Inventory

The system SHALL keep a checked-in presentation inventory that declares, for
every canonical core and module tool name, its owning module, group, namespace,
LLM-presentable flag, and load posture. The inventory SHALL be validated
against the executable union of registered tools and SHALL reject duplicate,
missing, stale, and owner-mismatched declarations. Infrastructure-only handlers
(`route.execute`, `cancel_session`) SHALL be declared not LLM-presentable, and
spec-mandated LLM tools such as `notify` SHALL be declared presentable.
Declarations are representation metadata only and SHALL NOT change which tools
are registered.

ID: REQ-core-tool-discovery-002
Source: RFC 0027 §LLM Visibility Classification (bu-ondtw.3, PR #4244); heart-and-soul/security.md §Session Sandboxing
Scope: v1-mandatory

#### Scenario: Drifted inventory is rejected

- **WHEN** the inventory has a duplicate declaration, omits a registered tool, declares a tool that is not registered, or names a different owning module than registration
- **THEN** validation fails and names the offending tools

#### Scenario: Infrastructure handlers are not presentable but stay callable

- **WHEN** the inventory classifies `route.execute` or `cancel_session` as not LLM-presentable
- **THEN** the handlers remain registered and callable by their infrastructure callers
- **AND** classification does not replace any handler-specific caller validation, approval, or module-state check

#### Scenario: Required LLM tools stay presentable

- **WHEN** an existing capability contract requires an LLM-facing tool such as `notify`
- **THEN** the inventory declares it LLM-presentable when its owning configuration admits it

### Requirement: Skill Identity Cannot Expand Tool Authority

The system SHALL resolve each runtime skill to one canonical identity under the
butler's `.agents/skills` source, including skills reached through
runtime-specific aliases, without reading or returning guidance content in the
identity. An unresolvable or unreadable skill source SHALL fail explicitly.
Skill resolution SHALL NOT add, unhide, or reconfigure MCP tools.

ID: REQ-core-tool-discovery-003
Source: RFC 0027 §Skills and Tools (bu-ondtw.3, PR #4244); RFC 0002 §Skills Infrastructure
Scope: v1-mandatory

#### Scenario: Alias resolves to the canonical source

- **WHEN** a runtime-specific skill path is an alias of a skill under `.agents/skills`
- **THEN** resolution returns the canonical `.agents/skills` physical source as the skill identity

#### Scenario: Missing or unreadable source is explicit

- **WHEN** a skill's canonical source is missing or unreadable
- **THEN** resolution fails with a typed skill-identity error rather than returning a partial identity
