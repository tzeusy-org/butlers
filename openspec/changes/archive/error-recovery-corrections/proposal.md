## Why

Users currently have no structured way to correct butler mistakes — wrong data stored, messages routed to the wrong butler, incorrect memories, or actions taken in error. Today, recovering from errors requires ad-hoc manual intervention or hoping the butler figures it out next time. This is a user-facing recovery mechanism (distinct from the self-healing system, which handles system-level crash recovery). Users need a `correct` tool that works across all butlers with consistent semantics, an audit trail, and safe data preservation.

## What Changes

- Add a `correct` core MCP tool available on ALL butlers that handles four correction types: data corrections, misroute reclassification, memory deletion, and action reversal
- Introduce an append-only `corrections` audit table that records every correction attempt (successful or failed), preserving the original data via soft-delete/versioning rather than hard-delete
- Define clear preconditions and postconditions for each correction type so LLM sessions can reason about when a correction is applicable
- Link corrections to sessions for full traceability (which session was corrected, which session performed the correction)
- Extend the Switchboard to support misroute re-dispatch: when a user says "that should have gone to finance, not health", the correction tool reclassifies and re-dispatches
- Extend the memory module to support correction-driven retraction: when a user says "that memory is wrong, delete it", the fact/episode is retracted (not hard-deleted) with a correction audit link
- Ensure failed corrections include a clear explanation of WHY they failed, since LLM sessions need unambiguous feedback for retry decisions

## Capabilities

### New Capabilities
- `error-recovery-corrections`: Core correction tool, correction types taxonomy (data_correction, misroute, memory_deletion, action_reversal), append-only audit log, preconditions/postconditions per type, failure explanations

### Modified Capabilities
- `core-daemon`: Registers the `correct` tool in `CORE_TOOL_NAMES` so it is available on every butler
- `core-sessions`: Adds correction audit linkage — sessions reference corrections they performed, and corrections reference the session being corrected
- `butler-switchboard`: Adds misroute re-dispatch capability — corrections of type `misroute` trigger reclassification and re-routing of the original request to the correct butler
- `module-memory`: Adds correction-driven retraction — corrections of type `memory_deletion` retract facts/episodes via the existing validity lifecycle rather than hard-deleting

## Impact

- **Database**: New `corrections` table in each butler's schema (append-only audit log); FK references to `sessions` table
- **Core daemon**: `correct` added to `CORE_TOOL_NAMES`; new core tool registration in startup sequence
- **Core sessions**: Schema extension for correction-session linkage
- **Switchboard**: New re-dispatch path triggered by misroute corrections; must handle the case where the original ingestion event has expired from `message_inbox`
- **Memory module**: New `retracted` handling in fact/episode validity lifecycle, triggered by correction rather than decay
- **Self-healing boundary**: This is explicitly NOT self-healing — corrections are user-initiated, self-healing is system-initiated. The two systems share no code paths but both link back to sessions
- **Tool descriptions**: Must be exceptionally clear since LLM sessions need to understand when to suggest/use corrections vs. when to just fix things normally
