## ADDED Requirements

### Requirement: Delegation Core Tool Inventory And Admission Boundary

The core-tool inventory SHALL reserve a delegation group for non-staffer
butlers. Its tool inventory is delegate_ask, delegate_receive, delegate_answer,
and delegate_wake.

delegate_wake is a server-to-server return endpoint, not a user-delivery or
free-form peer control surface. Even when registered, it SHALL require the
trusted Switchboard route caller and the authoritative ledger checks defined by
the cross-butler-delegation specification. Registration alone SHALL not grant a
domain butler authority to create work in a sibling schema.

#### Scenario: Non-staffer delegation inventory is explicit

- **WHEN** a butler-type daemon has the delegation core group enabled by its
  effective runtime configuration
- **THEN** its MCP inventory SHALL include the four delegation tools
- **AND** delegate_wake SHALL remain constrained to the Switchboard-routed
  server-to-server admission path

#### Scenario: Staffers do not gain delegation tools

- **WHEN** a staffer daemon starts, including when its effective groups contain
  delegation
- **THEN** none of delegate_ask, delegate_receive, delegate_answer, or
  delegate_wake SHALL be registered
- **AND** it SHALL not gain a route around the non-staffer delegation boundary

### Requirement: Delegation Inventory Does Not Activate Runtime Configuration

Defining the delegation core-tool inventory SHALL not by itself change
runtime-config validation, any existing effective core_groups value, a runtime
seed, a roster configuration, a schedule, or live MCP availability. Those
activation and guidance changes require their own bounded follow-on change
after the durable callback/task path is verified.

#### Scenario: Inventory definition preserves inactive deployments

- **WHEN** a deployment has no validated delegation group in its effective
  runtime configuration
- **THEN** this contract alone SHALL not alter that configuration or register
  a new recurring/one-shot schedule
- **AND** no user notification, briefing producer, or live configuration PATCH
  SHALL be implied by the inventory definition
