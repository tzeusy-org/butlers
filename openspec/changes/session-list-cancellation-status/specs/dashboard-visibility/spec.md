## MODIFIED Requirements

### Requirement: Data Model Contracts for Visibility Surfaces

The frontend TypeScript interfaces SHALL define the data contracts that all visibility surfaces depend on. These contracts MUST be satisfied by the backend API.

#### Scenario: SessionSummary contract (list views)

- **WHEN** the sessions list API responds
- **THEN** each item conforms to: `id` (string), `butler` (optional string), `prompt` (string), `trigger_source` (string), `request_id` (optional string | null), `success` (boolean | null), `cancelled_by_owner` (boolean), `started_at` (ISO 8601 string), `completed_at` (string | null), `duration_ms` (number | null), `input_tokens` (number | null), `output_tokens` (number | null)

#### Scenario: Owner-cancelled list row is distinct from a failure

- **WHEN** `SessionTable` renders a session summary with `success = false` and
  `cancelled_by_owner = true`
- **THEN** it renders `Cancelled`, not `Failed`
- **AND** a failed summary with `cancelled_by_owner = false` renders `Failed`
- **AND** success and non-terminal rows retain their existing labels

#### Scenario: SessionDetail contract (drill-down views)

- **WHEN** the session detail API responds
- **THEN** the item retains its existing detail contract: `result` (string | null), `tool_calls` (array of unknown), `trace_id` (string | null), `cost` (object | null), `error` (string | null), `model` (string | null), and `parent_session_id` (string | null)
- **AND** it does not require the list-only `cancelled_by_owner` discriminator

#### Scenario: TimelineEvent contract

- **WHEN** the timeline API responds
- **THEN** each event conforms to: `id` (string), `type` (string), `butler` (string), `timestamp` (ISO 8601 string), `summary` (string), `data` (object)
- **AND** the response meta includes `cursor` (string | null) and `has_more` (boolean) for pagination

#### Scenario: NotificationSummary contract

- **WHEN** the notifications API responds
- **THEN** each item conforms to: `id` (string), `source_butler` (string), `channel` (string), `recipient` (string | null), `message` (string), `metadata` (object | null), `status` (string), `error` (string | null), `session_id` (string | null), `trace_id` (string | null), `created_at` (ISO 8601 string)

#### Scenario: NotificationStats contract

- **WHEN** the notification stats API responds
- **THEN** the data conforms to: `total` (number), `sent` (number), `failed` (number), `by_channel` (object mapping channel name to count), `by_butler` (object mapping butler name to count)

#### Scenario: AuditEntry contract

- **WHEN** the audit log API responds
- **THEN** each entry conforms to: `id` (string), `butler` (string), `operation` (string), `request_summary` (object), `result` (string: "success" | "error"), `error` (string | null), `user_context` (object), `created_at` (ISO 8601 string)

#### Scenario: Issue contract

- **WHEN** the issues API responds
- **THEN** each issue conforms to: `severity` (string), `type` (string), `butler` (string), `description` (string), `link` (string | null), `error_message` (optional string | null), `occurrences` (optional number), `first_seen_at` (optional string | null), `last_seen_at` (optional string | null), `butlers` (optional string array for multi-butler issues)
