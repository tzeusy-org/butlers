## ADDED Requirements

### Requirement: MCP Tools Raise on Invalid Input
MCP tools registered by modules SHALL raise an exception on invalid input rather than returning a success-shaped empty payload. "Invalid input" includes (but is not limited to): required arguments missing or `null`, arguments of the wrong type, and values that fail schema validation (e.g. an empty string where a non-empty name is required). The exception SHALL be one that the runtime adapter renders as a tool-call error to the agent, not a silent empty result.

The motivating failure mode: a looping agent that invokes a tool with `null` arguments must see a typed error, not an empty list. Returning an empty list makes the failing call indistinguishable from a well-formed call that simply matched nothing, and rewards the agent for retrying.

This rule is cross-cutting: it applies to every MCP tool in every module. It establishes the normative contract so new tools comply by construction; existing tools are brought into compliance incrementally as they are touched (the `memory_entity_resolve` tool that triggered the motivating incident already complies — see `module-memory`).

#### Scenario: Missing required argument raises
- **WHEN** a module's MCP tool is invoked with a required argument missing or set to `null`
- **THEN** the tool SHALL raise an exception (e.g. `ValueError`, `TypeError`, or a module-specific `InvalidInputError`)
- **AND** SHALL NOT return an empty-success payload such as `[]`, `{}`, or `None`

#### Scenario: Empty-success payload is reserved for well-formed no-match
- **WHEN** a search-style MCP tool is invoked with valid, well-formed arguments that simply do not match any record
- **THEN** the tool MAY return an empty collection (e.g. `[]`) to indicate "no results for a valid query"
- **AND** this is the ONLY condition under which an empty-success payload is permitted

#### Scenario: Raised exception surfaces as a tool-call error to the agent
- **WHEN** a tool raises per this requirement
- **THEN** the runtime adapter SHALL surface the exception as a failed tool call in the session's event stream (not as a silent empty result)
- **AND** the spawner's degenerate-loop detector SHALL still treat identical failing calls as identical for loop-detection purposes
