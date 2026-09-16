## ADDED Requirements

### Requirement: Injected Local Memory Preserves Actionable Stable References

The memory module SHALL render every locally owned fact or rule admitted to
injected memory context with one bounded opaque stable reference containing
only its memory type and UUID. The existing confirm action SHALL resolve fact
or rule references, and the existing helpful and harmful actions SHALL resolve
rule references. References and their labels MUST count inside the existing
section and total context budgets. Reference resolution MUST use the owning
module's server-held sensitivity ceiling and live-memory rules; it MUST NOT
accept caller-asserted authority or expose raw identity, tenant, sensitivity,
source, or content fields beyond the context already authorized for rendering.

#### Scenario: Injected local facts and rules remain actionable

- **WHEN** a local fact or rule is admitted to Profile Facts, Task-Relevant
  Facts, or Active Rules in injected memory context
- **THEN** its rendered line MUST include a canonical `fact:<uuid>` or
  `rule:<uuid>` reference for that exact row
- **AND** `memory_confirm` MUST resolve either valid reference to that row
- **AND** `memory_mark_helpful` and `memory_mark_harmful` MUST resolve only a
  valid rule reference to that rule

#### Scenario: Reference overhead remains inside existing budgets

- **WHEN** adding a reference would make a fact or rule line exceed its
  section allocation
- **THEN** the complete line MUST be omitted rather than truncated or rendered
  beyond the allocation
- **AND** all rendered references, headers, receipts, and content together
  MUST remain within the requested context token budget

#### Scenario: Unavailable references fail without mutation or disclosure

- **WHEN** a confirm, helpful, or harmful action receives a missing, deleted,
  retired, forgotten, malformed, wrong-type, or above-ceiling reference
- **THEN** it MUST return that action's same content-free unavailable result
  for every such case and perform no memory mutation
- **AND** the result MUST NOT reveal whether the UUID exists, its memory type,
  sensitivity, identity fields, source, tenant, or content

#### Scenario: Reference authority remains server-held

- **WHEN** a caller invokes a reference-bearing feedback action
- **THEN** the action MUST obtain its sensitivity authority from the owning
  module runtime configuration
- **AND** no request argument or reference component MAY raise that authority
- **AND** Fleet Knowledge and Recent Episodes MUST NOT receive actionable local
  fact/rule references from this requirement
