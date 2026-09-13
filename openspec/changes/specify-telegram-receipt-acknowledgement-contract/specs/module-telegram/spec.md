# Telegram Module — Delta

## ADDED Requirements

### Requirement: Typing Indicator Dispatch for Ingest Lifecycle

The module SHALL provide a primitive pair for the ingest-pipeline typing-indicator keepalive,
parallel to the existing `react_for_ingest` primitive: `begin_typing_for_ingest` (start or extend
the per-chat keepalive) and `end_typing_for_ingest` (release this message's hold on the per-chat
keepalive). Both operate unconditionally on the caller's request — neither reads ack-style
configuration nor performs owner identity checks (see the `telegram-receipt-acknowledgement`
capability's Ownership Boundary requirement for who calls these and when).

#### Scenario: Begin typing dispatches sendChatAction

- **WHEN** `begin_typing_for_ingest` is called with a chat identity parsed from an
  `external_thread_id` (format: `<chat_id>:<message_id>`, the same format `react_for_ingest`
  already parses) and that chat's in-flight counter transitions from 0 to 1
- **THEN** a `sendChatAction` API call with `action="typing"` is made for that `chat_id`, and a
  keepalive loop re-issues the call at a 4-second cadence while the counter remains above 0

#### Scenario: Concurrent begin calls for the same chat do not stack loops

- **WHEN** `begin_typing_for_ingest` is called for a chat whose in-flight counter is already above 0
- **THEN** the counter increments and no additional keepalive loop is started

#### Scenario: End typing releases this message's hold

- **WHEN** `end_typing_for_ingest` is called for a chat identity
- **THEN** the chat's in-flight counter decrements, and the keepalive loop stops issuing further
  `sendChatAction` calls only when the counter reaches 0

#### Scenario: Unparseable thread identity

- **WHEN** `begin_typing_for_ingest` or `end_typing_for_ingest` is called with `None` or an
  unparseable `external_thread_id`
- **THEN** the call is a silent no-op, matching `react_for_ingest`'s existing behavior for the same
  input shape

#### Scenario: Typing API failure

- **WHEN** the Telegram API rejects a `sendChatAction` call (e.g., unsupported chat type)
- **THEN** the keepalive loop continues on its own schedule and a debug log is emitted (non-fatal),
  matching the existing Reaction API failure requirement's posture for the same class of error

### Requirement: Receipt Acknowledgement Style Configuration

`[modules.telegram.ack]` SHALL be an optional configuration section with a `style` field accepting
`"typing"`, `"reaction"`, `"both"`, or `"none"`, defaulting to `"reaction"` when the section or
field is absent. This is additive to the existing `TelegramConfig` schema (`webhook_url`,
`[modules.telegram.user]`, `[modules.telegram.bot]`) — none of those existing fields or their
validation change.

#### Scenario: Ack config is optional and independent of credential scopes

- **WHEN** `[modules.telegram.ack]` is present or absent in `butler.toml`
- **THEN** it has no effect on `[modules.telegram.user]` or `[modules.telegram.bot]` validation or
  resolution, and vice versa

#### Scenario: Invalid ack style value is rejected at config load

- **WHEN** `[modules.telegram.ack].style` is configured to a value outside
  `{typing, reaction, both, none}`
- **THEN** configuration loading SHALL fail with a structured error naming the allowed values,
  consistent with this module's existing `token_env` validation pattern
