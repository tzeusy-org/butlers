# Self-Healing Skill

## Purpose

A shared skill installed in `roster/shared/skills/self-healing/` that teaches all butler agents the protocol for interacting with the self-healing module's MCP tools. The skill is referenced from `BUTLER_SKILLS.md` so it's automatically included in every butler's system prompt. It defines when to report errors, what context to include, and how to interpret healing status responses.

## ADDED Requirements

### Requirement: Skill Location and Discovery
The skill SHALL be located at `roster/shared/skills/self-healing/SKILL.md` and referenced in `roster/shared/BUTLER_SKILLS.md` for automatic inclusion in all butler system prompts.

#### Scenario: Skill discovered by all butlers
- **WHEN** the spawner composes the system prompt for any butler
- **AND** `BUTLER_SKILLS.md` references the `self-healing` skill
- **THEN** the skill content is included in the butler's system prompt via the existing include mechanism

#### Scenario: BUTLER_SKILLS.md entry
- **WHEN** `roster/shared/BUTLER_SKILLS.md` is read
- **THEN** it includes an entry: `- **self-healing** — How to report unexpected errors for automated investigation. Consult this skill when you encounter an exception you cannot resolve yourself.`

### Requirement: Error Reporting Protocol
The skill SHALL teach butler agents a clear protocol for when and how to call `report_error`.

#### Scenario: When to report
- **WHEN** a butler agent encounters an unexpected exception during tool execution, API call, or data processing
- **AND** the error appears to be a code bug (not a user input error, not a transient network blip)
- **THEN** the skill instructs the agent to call `report_error` before continuing or giving up

#### Scenario: When NOT to report
- **WHEN** a butler agent encounters a user input validation error, a known transient error (rate limit, temporary network timeout), or an error it can handle and recover from
- **THEN** the skill instructs the agent NOT to call `report_error` (these are not code bugs)

#### Scenario: What context to include
- **WHEN** the agent calls `report_error`
- **THEN** the skill instructs the agent to include:
  - `error_type`: the fully qualified exception class name
  - `error_message`: the exact exception message
  - `traceback`: the full traceback if available
  - `call_site`: the file and function where the error occurred
  - `context`: the agent's own analysis — what it was trying to do, what it thinks went wrong, and any hypotheses about the root cause
  - `tool_name`: which MCP tool was being called (if applicable)
  - `severity_hint`: the agent's assessment of impact (`critical` for data loss/security, `high` for broken functionality, `medium` for degraded behavior, `low` for cosmetic/non-blocking)

#### Scenario: Context field guidelines
- **WHEN** the agent writes the `context` field
- **THEN** the skill instructs the agent to:
  - Describe what operation it was performing and why
  - State what it expected to happen vs. what actually happened
  - Include any relevant parameter patterns (WITHOUT actual user data values)
  - Suggest potential root causes if it has a hypothesis
  - Keep it under 500 words (enough for the healing agent, not a novel)

### Requirement: Handling the Response
The skill SHALL teach butler agents how to interpret the `report_error` response and what to do next.

#### Scenario: Error accepted
- **WHEN** `report_error` returns `{"accepted": true, ...}`
- **THEN** the skill instructs the agent to acknowledge the report internally and continue with its session (attempt a workaround or inform the user that the issue has been flagged for investigation)

#### Scenario: Error deduplicated
- **WHEN** `report_error` returns `{"accepted": false, "reason": "already_investigating", ...}`
- **THEN** the skill instructs the agent to note that this error is already being investigated and continue without re-reporting

#### Scenario: Error rejected by gate
- **WHEN** `report_error` returns `{"accepted": false, "reason": "cooldown", ...}` or any other rejection
- **THEN** the skill instructs the agent to continue its session normally — the self-healing system has decided not to investigate at this time, and that's fine

### Requirement: Status Querying Protocol
The skill SHALL teach butler agents when and how to use `get_healing_status`.

#### Scenario: When to check status
- **WHEN** a butler agent encounters an error it previously reported (same exception type + call site pattern)
- **THEN** the skill instructs the agent to optionally call `get_healing_status` to check if a fix is in progress or has been merged

#### Scenario: Status indicates fix merged
- **WHEN** `get_healing_status` returns an attempt with `status: "pr_merged"`
- **THEN** the skill instructs the agent to note that a fix was deployed and the error may resolve after a restart

### Requirement: Data Safety Instructions
The skill SHALL explicitly instruct butler agents on what NOT to include in error reports.

#### Scenario: No user data in reports
- **WHEN** the agent prepares a `report_error` call
- **THEN** the skill instructs the agent to NEVER include:
  - Actual user data values (names, emails, messages, financial data)
  - Session prompt content or user instructions
  - Credentials, API keys, or tokens
  - Personally identifiable information of any kind
- **AND** to instead describe patterns and types: "user's email address" not "john@example.com", "the message body" not the actual text

### Requirement: Skill Content Structure
The SKILL.md SHALL follow the existing skill format with clear, actionable instructions.

#### Scenario: Skill file structure
- **WHEN** the `SKILL.md` file is read
- **THEN** it contains:
  - A brief purpose statement (1-2 sentences)
  - A "When to Report" section with clear criteria
  - A "How to Report" section with `report_error` parameter guidance
  - A "Data Safety" section with explicit exclusion rules
  - A "Handling Responses" section with response interpretation
  - A "Checking Status" section for `get_healing_status` usage
  - Example calls showing good and bad `report_error` usage
