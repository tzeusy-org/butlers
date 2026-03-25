## ADDED Requirements

### Requirement: Correction Types Taxonomy
The system SHALL support four correction types, each with distinct semantics: `data_correction` (fix incorrect data stored by a previous session), `misroute` (reclassify a message that was routed to the wrong butler), `memory_deletion` (retract an incorrect memory — fact, episode, or rule), and `action_reversal` (reverse or cancel an action taken in error). The correction type determines which preconditions are checked and which downstream operations are performed.

#### Scenario: Valid correction type accepted
- **WHEN** the `correct` tool is called with `correction_type` set to `data_correction`, `misroute`, `memory_deletion`, or `action_reversal`
- **THEN** the correction proceeds to precondition checks for that type

#### Scenario: Unknown correction type rejected
- **WHEN** the `correct` tool is called with an unrecognized `correction_type`
- **THEN** the tool SHALL return an error explaining the valid correction types and their use cases

### Requirement: Correct Tool Interface
Every butler SHALL expose a `correct` core MCP tool that accepts: `correction_type` (enum: `data_correction`, `misroute`, `memory_deletion`, `action_reversal`), `target_session_id` (UUID of the session whose output is being corrected, required for all types), `description` (text explaining what was wrong and why, required), and type-specific parameters. The tool SHALL return a structured result including: `correction_id` (UUID), `status` (enum: `applied`, `partially_applied`, `failed`), `summary` (human-readable description of what was done or why it failed), and `original_data_snapshot` (the original data before correction, for audit).

#### Scenario: Successful data correction
- **WHEN** `correct` is called with `correction_type=data_correction`, a valid `target_session_id`, `state_key` (the key to correct), and `corrected_value` (the new value)
- **THEN** the tool SHALL update the state entry, store a snapshot of the original value in the correction record, and return `status=applied`

#### Scenario: Successful memory deletion correction
- **WHEN** `correct` is called with `correction_type=memory_deletion`, a valid `target_session_id`, `memory_type` (fact, episode, or rule), and `memory_id` (UUID of the memory to retract)
- **THEN** the tool SHALL retract the memory via the memory module, store a snapshot of the original memory content in the correction record, and return `status=applied`

#### Scenario: Successful misroute correction
- **WHEN** `correct` is called with `correction_type=misroute`, a valid `target_session_id` that was spawned from an ingestion event, and `correct_butler` (the butler that should have received the message)
- **THEN** the tool SHALL call the Switchboard's `correct_route` tool with the original request context, store the re-dispatch outcome in the correction record, and return `status=applied`

#### Scenario: Successful action reversal
- **WHEN** `correct` is called with `correction_type=action_reversal`, a valid `target_session_id`, and `action_description` (what action to reverse)
- **THEN** the tool SHALL attempt to reverse the action, report the outcome (which parts succeeded and which could not be reversed), and return the appropriate status

#### Scenario: Partially applied action reversal
- **WHEN** an action reversal can only partially succeed (e.g., a reminder was cancelled but a notification was already delivered)
- **THEN** the tool SHALL return `status=partially_applied` with a `summary` explaining what was reversed and what could not be reversed

#### Scenario: Failed correction with explanation
- **WHEN** a correction fails (preconditions not met, target not found, permission denied)
- **THEN** the tool SHALL return `status=failed` with a `summary` that explains WHY the correction failed in terms the LLM session can act on (e.g., "Session abc123 does not exist", "Memory xyz is already retracted", "Misroute correction requires a session spawned from an ingestion event but session abc123 was triggered by a schedule")

### Requirement: Correction Preconditions
Each correction type SHALL have explicit preconditions that MUST be satisfied before the correction is applied. Precondition failures SHALL produce clear, actionable error messages.

#### Scenario: Data correction preconditions
- **WHEN** `correction_type=data_correction` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing session, the `state_key` MUST exist in the butler's state store, and the corrected value MUST be a valid JSON value

#### Scenario: Misroute correction preconditions
- **WHEN** `correction_type=misroute` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing session that was spawned from an ingestion event (has a non-null `ingestion_event_id`), the `correct_butler` MUST be a registered butler in the Switchboard registry, and the Switchboard MUST be reachable

#### Scenario: Memory deletion preconditions
- **WHEN** `correction_type=memory_deletion` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing session, the `memory_id` MUST reference an existing memory of the specified `memory_type`, and the memory's current validity MUST be `active` (not already retracted, expired, or superseded)

#### Scenario: Action reversal preconditions
- **WHEN** `correction_type=action_reversal` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing session, and the session MUST have recorded tool calls that can be inspected for reversible actions

### Requirement: Append-Only Corrections Audit Table
Each butler schema SHALL contain a `corrections` table that records every correction attempt. Rows in this table SHALL be insert-only; no UPDATE or DELETE operations are permitted. The table SHALL include: `id` (UUID PK), `correction_type` (TEXT NOT NULL, one of the four correction types), `target_session_id` (UUID NOT NULL, FK to sessions), `correcting_session_id` (UUID NOT NULL, FK to sessions — the session performing the correction), `description` (TEXT NOT NULL, user/LLM explanation of what was wrong), `status` (TEXT NOT NULL, one of: `applied`, `partially_applied`, `failed`), `summary` (TEXT NOT NULL, outcome description), `original_data_snapshot` (JSONB, the original data before correction), `correction_details` (JSONB, type-specific details of what was changed), `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now()).

#### Scenario: Correction record created on success
- **WHEN** a correction is successfully applied
- **THEN** a row SHALL be inserted into `corrections` with `status=applied`, a snapshot of the original data in `original_data_snapshot`, and the correction details in `correction_details`

#### Scenario: Correction record created on failure
- **WHEN** a correction fails precondition checks or execution
- **THEN** a row SHALL still be inserted into `corrections` with `status=failed` and a `summary` explaining the failure reason

#### Scenario: Correction record immutability
- **WHEN** any attempt is made to UPDATE or DELETE a row in the `corrections` table
- **THEN** the operation SHALL be rejected (enforced by application-level guards; database-level triggers are optional)

#### Scenario: Correction audit queryable by target session
- **WHEN** corrections are queried by `target_session_id`
- **THEN** all correction attempts (successful and failed) for that session SHALL be returned, ordered by `created_at`

### Requirement: Original Data Preservation
Corrections SHALL preserve original data through soft-delete or versioning. Hard-delete of original data is never permitted as part of a correction.

#### Scenario: Data correction preserves original value
- **WHEN** a `data_correction` updates a state store entry
- **THEN** the original value SHALL be stored in the correction record's `original_data_snapshot` field before the update is applied

#### Scenario: Memory deletion preserves original content
- **WHEN** a `memory_deletion` retracts a memory
- **THEN** the original memory content, subject, predicate (for facts), and metadata SHALL be stored in the correction record's `original_data_snapshot` before retraction

#### Scenario: Misroute preserves original routing decision
- **WHEN** a `misroute` correction triggers re-dispatch
- **THEN** the original routing decision (target butler, routing rationale) SHALL be stored in the correction record's `original_data_snapshot`

### Requirement: Tool Description Clarity for LLM Sessions
The `correct` tool's MCP tool description SHALL be unambiguous and self-contained, enabling LLM sessions to determine when to use it without external documentation. The description SHALL enumerate all correction types with one-line descriptions, list required parameters per type, and explicitly state that corrections are for fixing previous mistakes (not for normal data operations).

#### Scenario: Tool description includes all correction types
- **WHEN** the `correct` tool's description is registered in MCP
- **THEN** the description SHALL list all four correction types with brief explanations of when each applies

#### Scenario: Tool description distinguishes corrections from normal operations
- **WHEN** an LLM session reads the `correct` tool description
- **THEN** the description SHALL explicitly state: "Use this tool ONLY to fix mistakes from previous sessions. For normal data updates, use state_set. For normal memory management, use memory tools directly."

### Requirement: Canonical Tool Description Text
The `correct` tool's MCP description SHALL use the following exact text (or a functionally equivalent wording that preserves all information). This text is the primary interface between the correction system and LLM sessions — clarity here prevents misuse.

```
correct: Fix mistakes from previous butler sessions. Use ONLY to correct past
errors, not for normal updates.

Types:
- data_correction: Fix incorrect data in state store (wrong value recorded)
- memory_deletion: Retract a wrong fact, episode, or rule from memory
- misroute: Message was sent to the wrong butler — reroute to correct one
- action_reversal: Reverse/cancel a mistaken action (best-effort)

NOT for: normal state updates (use state_set), routine memory management
(use memory tools), or new actions.

Required: correction_type, target_session_id (UUID of session that made the
mistake), description (what was wrong and why)
Optional: target_butler (query another butler's schema for cross-butler
corrections), correct_butler (for misroute), state_key/corrected_value
(for data_correction), memory_type/memory_id (for memory_deletion),
action_description (for action_reversal)
```

#### Scenario: Tool description text registered verbatim
- **WHEN** the `correct` tool is registered in the MCP server
- **THEN** the tool's `description` field SHALL contain the canonical text above (whitespace normalization is permitted, but all information items MUST be present)

#### Scenario: Tool description tested for completeness
- **WHEN** a test validates the `correct` tool description
- **THEN** the test SHALL verify the description contains: all four correction type names, the "NOT for" exclusion list, all required parameter names, and all optional parameter names

### Requirement: Failure Message Dictionary
Each precondition failure SHALL produce a specific, actionable error message from the dictionary below. These messages are the primary feedback mechanism for LLM sessions — they MUST tell the LLM what went wrong AND what to do next. Implementations SHALL use these exact message templates (substituting `{placeholders}` with actual values).

| Precondition Failure | Error Message Template |
|---|---|
| Session not found | `Session {id} does not exist. Run sessions_list to find the correct session UUID.` |
| State key not found | `State key '{key}' not found. Use state_list to see available keys.` |
| Memory already retracted | `Memory {id} was already retracted on {date}. No action needed.` |
| Memory superseded | `Memory {id} was superseded by {successor_id}. Correct the newer version instead.` |
| Butler not registered | `Butler '{name}' not registered. Available butlers: {comma_separated_list}.` |
| Ingestion event expired | `Original message expired (>30 days). Ask the user to re-send to butler '{correct_butler}'.` |
| Action not reversible | `Action type '{type}' cannot be reversed. Reversible types: {comma_separated_list}.` |
| Unknown correction type | `Unknown correction_type '{type}'. Valid types: data_correction, memory_deletion, misroute, action_reversal.` |
| Missing required parameter | `Parameter '{param}' is required for correction_type '{type}'. See tool description for required parameters.` |
| Session has no ingestion event | `Session {id} was not triggered by an ingestion event (trigger_source='{source}'). Misroute corrections require a session spawned from message routing.` |
| Memory not found | `Memory {id} of type '{memory_type}' not found. Use memory_recall to verify the memory ID and type.` |
| Switchboard unreachable | `Cannot reach Switchboard for misroute re-dispatch. Try again later or escalate to the user.` |

#### Scenario: Failure message includes remediation hint
- **WHEN** a correction fails a precondition check
- **THEN** the error message SHALL include both the failure reason AND a specific next action the LLM can take (e.g., which tool to call, what to ask the user)

#### Scenario: Failure messages are tested against dictionary
- **WHEN** tests validate precondition failures
- **THEN** each test SHALL assert the error message matches the corresponding template from the failure message dictionary (with appropriate placeholder substitution)

### Requirement: Correction Type Decision Tree
The `correct` tool implementation SHALL include an internal decision tree (documented in code comments and available as a helper function) that LLM sessions can use to select the appropriate correction type. The decision tree follows this logic:

```
Is the mistake about STORED DATA (a state_get/state_set value is wrong)?
  YES → correction_type = data_correction
  NO  ↓

Is the mistake about a MEMORY (a fact, episode, or rule that is wrong)?
  YES → correction_type = memory_deletion
  NO  ↓

Did the message go to the WRONG BUTLER entirely?
  YES → correction_type = misroute
  NO  ↓

Did the butler TAKE AN ACTION we want to undo (sent message, created event,
set reminder, etc.)?
  YES → correction_type = action_reversal
  NO  ↓

None of the above match.
  → You probably do not need the correct tool.
    For normal data updates, use state_set.
    For normal memory management, use memory tools directly.
    For new actions, use the appropriate action tool.
    If unsure, ask the user what specifically was wrong.
```

#### Scenario: Decision tree available as helper
- **WHEN** an LLM session needs to determine the correct correction_type
- **THEN** the tool description's type-level descriptions (in the canonical tool description text) SHALL be sufficient to make this determination without consulting external documentation

#### Scenario: Decision tree tested for coverage
- **WHEN** tests validate the correction type selection logic
- **THEN** the tests SHALL include at least one scenario per decision tree branch, including the "none of the above" fallthrough

### Requirement: Cross-Schema Correction Resolution
The `correct` tool SHALL support corrections that target sessions belonging to other butlers via an optional `target_butler` parameter. This enables a butler to fix mistakes that were originally made by a different butler's session (e.g., when a user asks butler A to fix something butler B did wrong, beyond simple misroute cases).

#### Scenario: Correction within own schema (default)
- **WHEN** the `correct` tool is called WITHOUT a `target_butler` parameter
- **THEN** the tool SHALL query the current butler's own schema for the `target_session_id`

#### Scenario: Correction targeting another butler's schema
- **WHEN** the `correct` tool is called WITH `target_butler` set to a valid butler name
- **THEN** the tool SHALL query the specified butler's schema for the `target_session_id`
- **AND** the correction record SHALL be written to the CURRENT butler's `corrections` table (not the target butler's), with `correction_details` including `target_butler` for audit

#### Scenario: Target butler does not exist
- **WHEN** `target_butler` is provided but does not correspond to a registered butler
- **THEN** the tool SHALL return `status=failed` with message: `Butler '{name}' not registered. Available butlers: {comma_separated_list}.`

#### Scenario: Cross-schema data_correction
- **WHEN** `correction_type=data_correction` is requested with a `target_butler`
- **THEN** the tool SHALL read the state key from the target butler's schema, update it in the target butler's schema, and record the correction in the current butler's `corrections` table

### Requirement: Misroute Re-dispatch Traceability
When a `misroute` correction is successfully applied, the correction result SHALL include the `new_session_id` of the re-dispatched session, enabling the correcting LLM session to follow up on the re-routed request.

#### Scenario: Misroute correction returns new session ID
- **WHEN** a `misroute` correction is successfully applied and the message is re-dispatched to the correct butler
- **THEN** the tool SHALL return `status=applied` with `correction_details` containing `new_session_id` (the UUID of the session created by the re-dispatch)
- **AND** the `summary` SHALL include: `Message re-dispatched to butler '{correct_butler}'. New session: {new_session_id}.`

#### Scenario: Misroute correction result stored in audit
- **WHEN** a misroute correction is recorded in the `corrections` table
- **THEN** the `correction_details` JSONB SHALL include: `correct_butler`, `new_session_id`, `original_butler`, and `original_request_id`

### Requirement: Correction Rate Limiting
To prevent correction loops (where an LLM session repeatedly corrects and re-corrects the same data), the `correct` tool SHALL enforce a rate limit of 10 corrections per source session per rolling hour. This is a safety valve, not a hard security boundary.

#### Scenario: Corrections within rate limit
- **WHEN** a session has made fewer than 10 corrections in the past hour
- **THEN** the correction proceeds normally

#### Scenario: Rate limit exceeded
- **WHEN** a session has made 10 or more corrections in the past rolling hour
- **THEN** the tool SHALL return `status=failed` with message: `Rate limit exceeded: {count} corrections in the past hour (limit: 10). This may indicate a correction loop. Ask the user to confirm before continuing.`

#### Scenario: Rate limit is per-session, not per-butler
- **WHEN** multiple sessions are active on the same butler
- **THEN** each session has its own independent rate limit counter

#### Scenario: Rate limit counter resets naturally
- **WHEN** corrections older than 1 hour fall outside the rolling window
- **THEN** they no longer count toward the rate limit (no explicit reset action needed)
