# Notify Contract — Insight Intent Extension

## Purpose
Extends the notify contract with an `insight` delivery intent for proactive insight delivery, enabling the Messenger butler to render insights with appropriate treatment.

## MODIFIED Requirements

### Requirement: Delivery Intent Validation
Four delivery intents are supported: `send`, `reply`, `react`, and `insight`. Each has specific field requirements.

#### Scenario: Send intent
- **WHEN** `intent="send"` is used
- **THEN** `message` is required and must be non-empty
- **AND** `request_context` is optional

#### Scenario: Reply intent requires request_context
- **WHEN** `intent="reply"` is used
- **THEN** `message` is required
- **AND** `request_context` must include `request_id`, `source_channel`, `source_endpoint_identity`, and `source_sender_identity`
- **AND** for telegram, `source_thread_identity` is required for reply targeting

#### Scenario: React intent requires emoji and thread identity
- **WHEN** `intent="react"` is used
- **THEN** `emoji` is required
- **AND** `request_context` must include `source_thread_identity` (for telegram: `<chat_id>:<message_id>`)
- **AND** `message` is not required

#### Scenario: Insight intent
- **WHEN** `intent="insight"` is used
- **THEN** `message` is required and must be non-empty
- **AND** `request_context` is optional
- **AND** the Messenger butler SHALL treat this as functionally equivalent to `intent="send"` for delivery mechanics
- **AND** the Messenger MAY apply visual differentiation for insight messages (e.g., formatting, labels)

#### Scenario: Missing message for send/reply/insight
- **WHEN** `intent` is `"send"`, `"reply"`, or `"insight"` and `message` is `None` or empty
- **THEN** the tool returns `{"status": "error", "error": "Missing required 'message' parameter..."}`

#### Scenario: Unsupported intent
- **WHEN** `intent` is not one of `send`, `reply`, `react`, `insight`
- **THEN** the tool returns an error response
