## ADDED Requirements

### Requirement: Tool Exposure Metadata

Modules that register LLM-facing MCP tools SHALL expose stable metadata for
canonical tool identity, logical namespace, LLM presentation eligibility, and
load posture (`eager` or `deferred`) in addition to any existing
argument-sensitivity declarations. Exposure metadata SHALL refine only the
tools already admitted by module configuration and SHALL not override group
filtering, module state, approval rules, channel-egress ownership, or handler
schema validation.

ID: REQ-core-modules-001
Source: RFC 0027 §Tool Metadata Contract; RFC 0002 §Tool Sensitivity Metadata
Scope: v1-mandatory

#### Scenario: Module declares deferred LLM tool metadata

- **WHEN** a module registers a tool intended for LLM presentation and suitable for deferred loading
- **THEN** its metadata supplies one canonical identity, namespace, LLM-presentable status, and deferred load posture
- **AND** the metadata refers to the same registered handler and typed schema

#### Scenario: Metadata cannot revive an excluded group

- **WHEN** exposure metadata describes a tool whose module group is excluded by effective configuration
- **THEN** the tool remains unregistered and absent from every discovery mode
- **AND** metadata does not create a callable alias or alternate handler

#### Scenario: Approval metadata remains authoritative

- **WHEN** a tool carries both argument-sensitivity and discovery metadata
- **THEN** discovery presentation preserves the existing sensitivity and approval behavior
- **AND** deferred loading cannot downgrade the tool's approval requirements

#### Scenario: Existing module without classification uses compatibility posture

- **WHEN** an existing module tool has no exposure metadata during the migration window
- **THEN** it retains its current LLM-facing eager-filtered behavior
- **AND** native deferred presentation remains unavailable for that tool until classification is complete
