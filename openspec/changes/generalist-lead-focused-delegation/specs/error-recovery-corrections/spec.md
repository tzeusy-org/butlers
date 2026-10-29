## MODIFIED Requirements

### Requirement: Correction Types Taxonomy
The system SHALL support three correction types, each with distinct semantics: `data_correction` (fix incorrect data stored by a previous invocation or session), `memory_deletion` (retract an incorrect memory — fact, episode, or rule), and `action_reversal` (reverse or cancel an action taken in error). The correction type determines which preconditions are checked and which downstream operations are performed. The `misroute` type SHALL NOT be accepted, because there is no route dispatch to correct.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Valid correction type accepted
- **WHEN** the `correct` tool is called with `correction_type` set to `data_correction`, `memory_deletion`, or `action_reversal`
- **THEN** the correction proceeds to precondition checks for that type

#### Scenario: Unknown correction type rejected
- **WHEN** the `correct` tool is called with an unrecognized `correction_type`
- **THEN** the tool SHALL return an error explaining the valid correction types and their use cases

### Requirement: Correct Tool Interface
Every butler SHALL expose a `correct` core MCP tool that accepts: `correction_type` (enum: `data_correction`, `memory_deletion`, `action_reversal`), `target_session_id` (UUID of the goal invocation, or the pre-cutover session, whose output is being corrected, required for all types), `description` (text explaining what was wrong and why, required), and type-specific parameters. The tool SHALL take the correcting invocation's identity only from the verified broker assertion, never from its arguments. The tool SHALL return a structured result including: `correction_id` (UUID), `status` (enum: `applied`, `partially_applied`, `failed`), `summary` (human-readable description of what was done or why it failed), and `original_data_snapshot` (the original data before correction, for audit).

Source: RFC 0038 D1, D7
Scope: v1-mandatory

#### Scenario: Successful data correction
- **WHEN** `correct` is called with `correction_type=data_correction`, a valid `target_session_id`, `state_key` (the key to correct), and `corrected_value` (the new value)
- **THEN** the tool SHALL update the state entry, store a snapshot of the original value in the correction record, and return `status=applied`

#### Scenario: Successful memory deletion correction
- **WHEN** `correct` is called with `correction_type=memory_deletion`, a valid `target_session_id`, `memory_type` (fact, episode, or rule), and `memory_id` (UUID of the memory to retract)
- **THEN** the tool SHALL retract the memory via the memory module, store a snapshot of the original memory content in the correction record, and return `status=applied`

#### Scenario: Successful misroute correction
- **WHEN** `correct` is called with `correction_type=misroute`
- **THEN** the tool SHALL return `status=failed` with the unknown-correction-type message listing the three valid types
- **AND** no Switchboard call or re-dispatch occurs

#### Scenario: Successful action reversal
- **WHEN** `correct` is called with `correction_type=action_reversal`, a valid `target_session_id`, and `action_description` (what action to reverse)
- **THEN** the tool SHALL attempt to reverse the action, report the outcome (which parts succeeded and which could not be reversed), and return the appropriate status

#### Scenario: Partially applied action reversal
- **WHEN** an action reversal can only partially succeed (e.g., a reminder was cancelled but a notification was already delivered)
- **THEN** the tool SHALL return `status=partially_applied` with a `summary` explaining what was reversed and what could not be reversed

#### Scenario: Failed correction with explanation
- **WHEN** a correction fails (preconditions not met, target not found, permission denied)
- **THEN** the tool SHALL return `status=failed` with a `summary` that explains WHY the correction failed in terms the LLM session can act on (e.g., "Session abc123 does not exist", "Memory xyz is already retracted", "Action type 'email_send' cannot be reversed")

### Requirement: Correction Preconditions
Each correction type SHALL have explicit preconditions that MUST be satisfied before the correction is applied. Precondition failures SHALL produce clear, actionable error messages.

Source: RFC 0038 D1, D17
Scope: v1-mandatory

#### Scenario: Data correction preconditions
- **WHEN** `correction_type=data_correction` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing goal invocation or pre-cutover session, the `state_key` MUST exist in the butler's state store, and the corrected value MUST be a valid JSON value

#### Scenario: Misroute correction preconditions
- **WHEN** `correction_type=misroute` is requested
- **THEN** no misroute preconditions are evaluated and the request fails as an unknown correction type

#### Scenario: Memory deletion preconditions
- **WHEN** `correction_type=memory_deletion` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing goal invocation or pre-cutover session, the `memory_id` MUST reference an existing memory of the specified `memory_type`, and the memory's current validity MUST be `active` (not already retracted, expired, or superseded)

#### Scenario: Action reversal preconditions
- **WHEN** `correction_type=action_reversal` is requested
- **THEN** preconditions are: the `target_session_id` MUST reference an existing goal invocation or pre-cutover session, and the target MUST have recorded effects or tool calls that can be inspected for reversible actions

### Requirement: Append-Only Corrections Audit Table
Each butler schema SHALL contain a `corrections` table that records every correction attempt. Rows in this table SHALL be insert-only; no UPDATE or DELETE operations are permitted. The table SHALL include: `id` (UUID PK), `correction_type` (TEXT NOT NULL, one of the correction types; historic rows may hold `misroute`), `target_session_id` (UUID NOT NULL, the goal invocation or pre-cutover session whose output is corrected), the correcting invocation's lineage (`goal_id`, `invocation_id`, `depth`, taken from the verified broker assertion and never from tool arguments or spawner context; rows written before cutover keep `correcting_session_id` instead), `description` (TEXT NOT NULL, user/LLM explanation of what was wrong), `status` (TEXT NOT NULL, one of: `applied`, `partially_applied`, `failed`), `summary` (TEXT NOT NULL, outcome description), `original_data_snapshot` (JSONB, the original data before correction), `correction_details` (JSONB, type-specific details of what was changed), `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now()).

Source: RFC 0038 D7, D17
Scope: v1-mandatory

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

#### Scenario: Correcting lineage comes from the assertion
- **WHEN** a goal invocation calls `correct`
- **THEN** the row's correcting `goal_id`, `invocation_id` and `depth` SHALL equal the verified assertion's values
- **AND** any correcting identity supplied in the tool arguments SHALL be ignored

### Requirement: Original Data Preservation
Corrections SHALL preserve original data through soft-delete or versioning. Hard-delete of original data is never permitted as part of a correction.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Data correction preserves original value
- **WHEN** a `data_correction` updates a state store entry
- **THEN** the original value SHALL be stored in the correction record's `original_data_snapshot` field before the update is applied

#### Scenario: Memory deletion preserves original content
- **WHEN** a `memory_deletion` retracts a memory
- **THEN** the original memory content, subject, predicate (for facts), and metadata SHALL be stored in the correction record's `original_data_snapshot` before retraction

#### Scenario: Misroute preserves original routing decision
- **WHEN** a `corrections` row recorded a `misroute` correction before cutover
- **THEN** its `original_data_snapshot` with the original routing decision SHALL remain stored and readable
- **AND** no new misroute snapshot is written after cutover

### Requirement: Tool Description Clarity for LLM Sessions
The `correct` tool's MCP tool description SHALL be unambiguous and self-contained, enabling LLM sessions to determine when to use it without external documentation. The description SHALL enumerate all correction types with one-line descriptions, list required parameters per type, and explicitly state that corrections are for fixing previous mistakes (not for normal data operations).

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Tool description includes all correction types
- **WHEN** the `correct` tool's description is registered in MCP
- **THEN** the description SHALL list all three correction types with brief explanations of when each applies

#### Scenario: Tool description distinguishes corrections from normal operations
- **WHEN** an LLM session reads the `correct` tool description
- **THEN** the description SHALL explicitly state: "Use this tool ONLY to fix mistakes from previous sessions. For normal data updates, use state_set. For normal memory management, use memory tools directly."

### Requirement: Canonical Tool Description Text
The `correct` tool's MCP description SHALL use the following exact text (or a functionally equivalent wording that preserves all information). This text is the primary interface between the correction system and LLM sessions — clarity here prevents misuse.

```
correct: Fix mistakes from previous sessions. Use ONLY to correct past
errors, not for normal updates.

Types:
- data_correction: Fix incorrect data in state store (wrong value recorded)
- memory_deletion: Retract a wrong fact, episode, or rule from memory
- action_reversal: Reverse/cancel a mistaken action (best-effort)

NOT for: normal state updates (use state_set), routine memory management
(use memory tools), or new actions.

Required: correction_type, target_session_id (UUID of the invocation or
session that made the mistake), description (what was wrong and why)
Optional: state_key/corrected_value (for data_correction),
memory_type/memory_id (for memory_deletion), action_description
(for action_reversal)
```

Source: RFC 0038 D1, D4
Scope: v1-mandatory

#### Scenario: Tool description text registered verbatim
- **WHEN** the `correct` tool is registered in the MCP server
- **THEN** the tool's `description` field SHALL contain the canonical text above (whitespace normalization is permitted, but all information items MUST be present)

#### Scenario: Tool description tested for completeness
- **WHEN** a test validates the `correct` tool description
- **THEN** the test SHALL verify the description contains: all three correction type names, the "NOT for" exclusion list, all required parameter names, and all optional parameter names

### Requirement: Failure Message Dictionary
Each precondition failure SHALL produce a specific, actionable error message from the dictionary below. These messages are the primary feedback mechanism for LLM sessions — they MUST tell the LLM what went wrong AND what to do next. Implementations SHALL use these exact message templates (substituting `{placeholders}` with actual values).

| Precondition Failure | Error Message Template |
|---|---|
| Session not found | `Session {id} does not exist. Run sessions_list to find the correct session UUID.` |
| State key not found | `State key '{key}' not found. Use state_list to see available keys.` |
| Memory already retracted | `Memory {id} was already retracted on {date}. No action needed.` |
| Memory superseded | `Memory {id} was superseded by {successor_id}. Correct the newer version instead.` |
| Action not reversible | `Action type '{type}' cannot be reversed. Reversible types: {comma_separated_list}.` |
| Unknown correction type | `Unknown correction_type '{type}'. Valid types: data_correction, memory_deletion, action_reversal.` |
| Missing required parameter | `Parameter '{param}' is required for correction_type '{type}'. See tool description for required parameters.` |
| Memory not found | `Memory {id} of type '{memory_type}' not found. Use memory_recall to verify the memory ID and type.` |

Source: RFC 0038 D1
Scope: v1-mandatory

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

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Decision tree available as helper
- **WHEN** an LLM session needs to determine the correct correction_type
- **THEN** the tool description's type-level descriptions (in the canonical tool description text) SHALL be sufficient to make this determination without consulting external documentation

#### Scenario: Decision tree tested for coverage
- **WHEN** tests validate the correction type selection logic
- **THEN** the tests SHALL include at least one scenario per decision tree branch, including the "none of the above" fallthrough

## REMOVED Requirements

### Requirement: Cross-Schema Correction Resolution
**Reason**: Under RFC 0038 a domain executes only its own capabilities against its own schema (D2, D4); a butler reading or writing another butler's schema on a model's behalf bypasses the owning domain's grant and assertion checks (D7).
**Migration**: The goal's lead, or a child it delegates, calls the owning domain's `correct` capability directly through the broker (`REQ-goal-orchestration-004`); historic rows whose `correction_details` carry `target_butler` remain readable.

### Requirement: Misroute Re-dispatch Traceability
**Reason**: Misroute corrections and Switchboard `correct_route` re-dispatch are removed with route dispatch (RFC 0038 D1).
**Migration**: When work was handled wrongly, the lead corrects the wrong effects with the owning domains' `correct` capabilities and performs the intended work in the same goal (`REQ-goal-orchestration-001`); historic misroute rows remain readable.
