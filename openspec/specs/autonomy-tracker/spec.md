## ADDED Requirements

### Requirement: Pattern Fingerprint Computation

The system SHALL compute a deterministic `pattern_fingerprint` for every tool invocation by hashing the canonical representation of `(tool_name, sorted((arg_key, arg_value) for all args))`. The fingerprint MUST use SHA-256 over a canonical JSON string where keys are sorted alphabetically and values are JSON-serialized.

#### Scenario: Same tool and args produce identical fingerprint

- **WHEN** `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})` is called twice
- **THEN** both calls MUST return the same SHA-256 hex digest

#### Scenario: Different arg values produce different fingerprints

- **WHEN** `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})` is computed
- **AND** `compute_fingerprint("send_telegram", {"chat_id": "dad_456", "text": "hello"})` is computed
- **THEN** the two fingerprints MUST be different

#### Scenario: Arg key order does not affect fingerprint

- **WHEN** `compute_fingerprint("notify", {"channel": "email", "to": "a@b.com"})` is computed
- **AND** `compute_fingerprint("notify", {"to": "a@b.com", "channel": "email"})` is computed
- **THEN** both MUST produce the same fingerprint

#### Scenario: Tool name is part of the fingerprint

- **WHEN** `compute_fingerprint("send_telegram", {"to": "mom"})` is computed
- **AND** `compute_fingerprint("send_email", {"to": "mom"})` is computed
- **THEN** the two fingerprints MUST be different

### Requirement: Approval History Recording

The system SHALL record an entry in `autonomy_approval_history` every time a pending action is manually approved by a human actor. Each entry MUST include `pattern_fingerprint`, `tool_name`, `tool_args` (full JSONB), `action_id` (FK to pending_actions), `approved_at` timestamp, and `time_to_decision_seconds` (elapsed time from `requested_at` to `decided_at`).

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

### Requirement: Approval Frequency Query

The system SHALL provide a function to query the number of times a given `pattern_fingerprint` has been manually approved. The count MUST only include entries from `autonomy_approval_history`.

#### Scenario: Query approval count for a pattern

- **WHEN** `get_approval_count(pool, pattern_fingerprint)` is called
- **THEN** it MUST return the total number of rows in `autonomy_approval_history` matching that fingerprint

#### Scenario: No history for pattern returns zero

- **WHEN** `get_approval_count(pool, fingerprint)` is called for a fingerprint with no history
- **THEN** it MUST return `0`

### Requirement: Promotion Threshold Detection

After each manual approval is recorded, the system SHALL check whether the pattern's approval count has reached the configurable promotion threshold (default: 5). If the threshold is met and no active promotion suggestion or matching standing rule exists for that pattern, the system MUST create a new promotion suggestion.

#### Scenario: Fifth approval triggers suggestion creation

- **WHEN** a manual approval is recorded for pattern fingerprint `fp_abc`
- **AND** the total approval count for `fp_abc` is now 5
- **AND** no active suggestion or matching standing rule exists for `fp_abc`
- **THEN** a new `autonomy_suggestion` row MUST be created with status `pending`

#### Scenario: Threshold not yet met

- **WHEN** a manual approval is recorded for pattern fingerprint `fp_abc`
- **AND** the total approval count for `fp_abc` is now 3 (below threshold of 5)
- **THEN** no suggestion SHALL be created

#### Scenario: Matching standing rule already exists

- **WHEN** a manual approval is recorded and the count reaches the threshold
- **AND** a matching standing rule already exists for that `tool_name` and `tool_args`
- **THEN** no suggestion SHALL be created

#### Scenario: Active suggestion already exists

- **WHEN** a manual approval is recorded and the count reaches the threshold
- **AND** a `pending` suggestion already exists for that `pattern_fingerprint`
- **THEN** no duplicate suggestion SHALL be created

#### Scenario: Dismissed suggestion within cooldown

- **WHEN** a manual approval is recorded and the count exceeds the threshold
- **AND** a suggestion for that `pattern_fingerprint` was dismissed less than 30 days ago
- **THEN** no new suggestion SHALL be created

#### Scenario: Dismissed suggestion after cooldown expires

- **WHEN** a manual approval is recorded and the count exceeds the threshold
- **AND** a suggestion for that `pattern_fingerprint` was dismissed more than 30 days ago
- **THEN** a new `pending` suggestion MUST be created

### Requirement: Approval Velocity Tracking

The system SHALL track approval velocity for each `pattern_fingerprint` as the rolling average of `time_to_decision_seconds` over the last N approvals (configurable, default: 10). The velocity metric MUST be stored in the butler's state store under key `autonomy:velocity:{pattern_fingerprint}`.

#### Scenario: Velocity computed after each approval

- **WHEN** a manual approval is recorded for pattern `fp_abc`
- **THEN** the system MUST compute the rolling average `time_to_decision_seconds` for the most recent N entries of `fp_abc`
- **AND** store the result as `{"avg_seconds": <float>, "sample_count": <int>, "updated_at": "<iso>"}` in the state store

#### Scenario: First approval for a pattern

- **WHEN** the first manual approval is recorded for pattern `fp_abc`
- **THEN** the velocity MUST be stored with `sample_count: 1` and `avg_seconds` equal to that single decision time

#### Scenario: Velocity decreasing signals annoyance

- **WHEN** the rolling average `time_to_decision_seconds` for a pattern drops below 5 seconds
- **THEN** the velocity entry MUST include `"fast_approval": true` as a signal for the dashboard

### Requirement: Autonomy Approval History Table Schema

The `autonomy_approval_history` table MUST be created in the butler's schema with columns: `id` (UUID PK), `pattern_fingerprint` (VARCHAR(64), indexed), `tool_name` (TEXT), `tool_args` (JSONB), `action_id` (UUID, FK to pending_actions), `approved_at` (TIMESTAMPTZ), `time_to_decision_seconds` (FLOAT).

#### Scenario: Table created via migration

- **WHEN** the Alembic migration runs
- **THEN** the `autonomy_approval_history` table MUST exist with all specified columns
- **AND** an index MUST exist on `pattern_fingerprint`
- **AND** an index MUST exist on `(pattern_fingerprint, approved_at)` for count queries with time filtering

### Requirement: Configurable Tracker Parameters

The tracker MUST read configuration from `[modules.approvals]` in `butler.toml`:
- `promotion_threshold` (integer, default: 5) -- number of manual approvals before suggesting promotion
- `velocity_window` (integer, default: 10) -- number of recent approvals for rolling velocity average
- `suggestion_cooldown_days` (integer, default: 30) -- days before a dismissed suggestion can be re-proposed

#### Scenario: Custom threshold from config

- **WHEN** `butler.toml` sets `promotion_threshold = 3`
- **THEN** the tracker MUST create suggestions after 3 manual approvals of the same pattern

#### Scenario: Default values when config is absent

- **WHEN** no tracker-specific config keys are present in `[modules.approvals]`
- **THEN** the tracker MUST use default values: threshold=5, velocity_window=10, cooldown_days=30
