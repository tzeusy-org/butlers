## MODIFIED Requirements

### Requirement: Routing Goes Through The Switchboard
Dispatch of a resolved delegated question to its target butler SHALL go
through the Switchboard's existing `route()` primitive (the same routing
function used by `correct_route` and `route_to_butler`) --
never a bespoke point-to-point dispatch path.

#### Scenario: Non-Switchboard asker dispatches via the Switchboard MCP client
- **WHEN** a non-Switchboard butler's `delegate_ask` has resolved a target
  butler
- **THEN** dispatch calls the Switchboard's `route` MCP tool (via the
  butler's `switchboard_client`) with `target_butler`, `tool_name =
  "delegate_receive"`, and the ledger id, question, and asking butler as args

#### Scenario: Switchboard asking itself dispatches in-process
- **WHEN** the Switchboard butler itself calls `delegate_ask`
- **THEN** dispatch calls the underlying `route()` function directly
  in-process (no MCP round trip to itself), with the same routing/eligibility
  checks as any other route dispatch

#### Scenario: A dispatch failure is recorded honestly
- **WHEN** the Switchboard `route()` call errors (target unreachable, stale,
  quarantined, or the tool call itself fails)
- **THEN** the ledger row transitions to `status = 'failed'` with the error
  recorded in `reason`, and the response to the asking butler names the
  failure and marks it `retryable` when the failure looks transient
  (timeout, connection error, Switchboard not connected)
