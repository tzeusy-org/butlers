## ADDED Requirements

### Requirement: Direct non-Messenger approval commands are declared and executable

The system MUST require every direct non-Messenger producer covered by this
contract that creates a `pending_actions` row to construct its stored command
from a declared executable-command contract. The contract SHALL identify the
owning daemon, registered MCP tool name, and exact handler argument keys. The
owning daemon SHALL validate its declared contracts against its FastMCP
registry after tool registration and before accepting approval dispatch.

#### Scenario: Connector disconnect parks an executable Switchboard command

- **WHEN** a dashboard caller requests disconnect for an active connector
- **THEN** the pending action stores `tool_name = "connector_disconnect"` and
  exactly `connector_type` plus `endpoint_identity` arguments
- **AND** Switchboard's registered original handler accepts those arguments
  after approval without re-entering the gate

#### Scenario: Relationship curation parks an executable reclassification command

- **WHEN** episodic-predicate curation proposes reclassifying an active fact
- **THEN** the pending action stores `tool_name = "memory_reclassify"` and
  exactly `memory_type`, `memory_id`, and `permanence_target` arguments
- **AND** the command accepts only `memory_type = "fact"` and
  `permanence_target = "volatile"`
- **AND** standing rules must pin all three safety-critical arguments
- **AND** Relationship's registered original handler accepts those arguments
  after approval without re-entering the gate

#### Scenario: Declared handler drift fails before new work is accepted

- **WHEN** an owning daemon starts with a declared command whose handler is
  missing, variadic, or has a different argument shape
- **THEN** daemon startup fails with a diagnostic naming the invalid command
- **AND** it does not silently leave a queue producer that can only fail after
  an owner approves it

### Requirement: Unrepresentable direct commands fail before parking

A direct producer SHALL reject an action before calling the pending-action park
path when it cannot persist a safe, exact replay command. The rejection SHALL
append an audit entry with a safe failure reason and SHALL not create a pending
action.

#### Scenario: Credential rotation has no durable replay reference

- **WHEN** a connector token-rotation request carries no authorized credential
  reference or deterministic provider rotation command
- **THEN** the dashboard connector lifecycle endpoint returns HTTP 409 before
  calling the approvals park path
- **AND** it appends an error audit signal that names the unreplayable rotation
  without exposing a credential value
- **AND** no `connector_rotate_token` pending action is persisted
- **AND** no `connector_rotate_token` action can reach owner approval

### Requirement: Historic malformed commands remain truthful evidence

The system SHALL not rewrite a stored historic tool name or arguments to make
an approved action executable. A failed historic dispatch SHALL preserve the
row's stored command and leave it approved with no execution result while
recording the existing execution-failed audit event.

#### Scenario: Historic unregistered command is retried

- **WHEN** an operator retries an approved historic action whose stored command
  is not registered by its owning daemon
- **THEN** dispatch reports the missing handler truthfully
- **AND** the row's stored tool name and arguments remain unchanged
- **AND** the action does not transition to executed
