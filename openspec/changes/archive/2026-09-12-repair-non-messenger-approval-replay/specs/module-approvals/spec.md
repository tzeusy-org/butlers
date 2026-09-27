## MODIFIED Requirements

### Requirement: Shared Executor Path

All actual execution — auto-approved actions and manually approved actions dispatched by their owning butler — MUST go through `execute_approved_action()`. The executor calls the original tool function, normalizes non-dict return values to `{"value": ...}`, and only then persists a successful `execution_result`, `executed` status, immutable success audit event, and any auto-approval `use_count` increment. The terminal state and audit append are atomic when the runtime provides a transaction.

Direct non-Messenger producers covered by the executable-command contract MUST
persist a declared executable command: the owning daemon, registered original
tool name, and exact handler kwargs are validated before the row can be
parked. The owning daemon validates its declared command contracts against the
registered MCP surface during startup.

#### Scenario: Tool execution succeeds

- **WHEN** the executor calls the original tool function successfully
- **THEN** the result is persisted as `{"success": true, "result": {...}, "executed_at": "..."}`
- **AND** status transitions from `approved` to `executed`
- **AND** an `action_execution_succeeded` audit event is recorded
- **AND** the successful result is not reported until the persisted result and audit transition complete

#### Scenario: Tool execution fails with exception

- **WHEN** the tool function raises an exception
- **THEN** the error is returned to the caller and an `action_execution_failed` audit event is recorded
- **AND** status remains `approved` with `execution_result = null`
- **AND** no automatic replay occurs; an operator may retry the approved action or reject it explicitly

#### Scenario: Manual approval without executor wired

- **WHEN** `approve_action` is called but no tool executor is wired
- **THEN** status remains `approved` with `execution_result = null`
- **AND** the action remains eligible for the owning-butler dispatch/retry seam

#### Scenario: Declared direct command dispatches through its owner

- **WHEN** an approved direct non-Messenger action has a declared command and a null execution result
- **THEN** `dispatch_approved_action` invokes the owning daemon's registered original handler with exactly the persisted declared kwargs
- **AND** it does not re-enter the approval gate or use a different butler

#### Scenario: Approved dispatch recovery is deliberately narrow

- **WHEN** `dispatch_approved_action` receives an `approved` action with `execution_result = null`
- **THEN** it invokes the owning daemon's registered original tool handler without re-entering the gate
- **AND** a legacy `relationship` action named `entity_merge` is resolved as the registered `memory_entity_merge` callable without rewriting the stored row
- **AND** actions in any other status, or approved actions with a non-null result, are not replayed

#### Scenario: Historic malformed command remains unchanged

- **WHEN** a historic approved action has an unregistered name or incompatible stored arguments
- **THEN** dispatch records the failure and leaves the action approved with a null execution result
- **AND** it does not infer replacement arguments or rewrite the stored action provenance

#### Scenario: At-most-once execution with concurrency lock

- **WHEN** concurrent execution attempts target the same action
- **THEN** a per-action asyncio lock (WeakValueDictionary-based) serializes attempts within one daemon process
- **AND** a persisted `executed` result is replayed without invoking the tool again
