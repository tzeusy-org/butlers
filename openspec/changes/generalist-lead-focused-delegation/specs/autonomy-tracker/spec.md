## MODIFIED Requirements

### Requirement: Approval History Recording

The system SHALL record an entry in `autonomy_approval_history` every time a pending action is manually approved by a human actor. Each entry MUST include `pattern_fingerprint`, `tool_name`, `tool_args` (full JSONB), `action_id` (FK to pending_actions), `approved_at` timestamp, and `time_to_decision_seconds` (elapsed time from `requested_at` to `decided_at`). An approval of an action requested by an exposed goal invocation SHALL NOT be recorded and SHALL NOT count toward promotion thresholds. An approval of an action for a tool whose catalog entry declares the approval mode `always_per_event` (`REQ-capability-authorization-017`) SHALL NOT be recorded either, so it never produces a promotion suggestion.

ID: REQ-autonomy-tracker-001
Source: RFC 0038 D6 and D13; RFC 0021; bu-p5umi2.4 = A (always-per-event approval mode)
Scope: v1-mandatory

#### Scenario: Manual approval records history entry

- **WHEN** a human approves a pending action via `approve_action`
- **THEN** a row MUST be inserted into `autonomy_approval_history` with the computed `pattern_fingerprint`
- **AND** `time_to_decision_seconds` MUST equal the difference between `decided_at` and `requested_at` in seconds

#### Scenario: Auto-approved actions are not recorded

- **WHEN** a standing rule auto-approves an action
- **THEN** no row SHALL be inserted into `autonomy_approval_history`
- **AND** the tracker MUST only count manual human approvals toward promotion thresholds

#### Scenario: Rejected and expired actions are not recorded

- **WHEN** a pending action is rejected or expires
- **THEN** no row SHALL be inserted into `autonomy_approval_history`

#### Scenario: Approvals of exposed requests do not count toward promotion

- **WHEN** a human approves a pending action whose request lineage records that the requesting invocation was exposed
- **THEN** no row SHALL be inserted into `autonomy_approval_history`
- **AND** the approval SHALL NOT contribute to any promotion threshold or suggestion

#### Scenario: Approvals of always-per-event actions do not count toward promotion

- **WHEN** a human approves a pending action for a tool that declares `always_per_event`
- **THEN** no row SHALL be inserted into `autonomy_approval_history`
- **AND** no promotion suggestion SHALL be generated for that tool
