# Notify Contract

## Purpose
Defines the `notify` MCP tool and its versioned envelope contract (`notify.v1`) for outbound user interaction requests from non-messenger butlers, routed through Switchboard to the Messenger butler for delivery.

## ADDED Requirements

### Requirement: Notify Tool Registration
Every butler registers a `notify` core MCP tool that sends a `notify.v1` envelope through the Switchboard `deliver()` pathway. The tool accepts `channel`, `message`, `recipient`, `subject`, `intent`, `emoji`, and `request_context` parameters.

#### Scenario: Notify tool is a core tool
- **WHEN** the daemon completes startup
- **THEN** the `notify` tool is registered in the butler's MCP server as part of `CORE_TOOL_NAMES`

### Requirement: notify.v1 Envelope Schema
The notify envelope includes `schema_version` ("notify.v1"), `origin_butler` (requesting butler's name), `delivery` (intent, channel, message, optional recipient/subject/emoji), and optional `request_context` for reply/react targeting.

#### Scenario: Send intent envelope
- **WHEN** `notify(channel="telegram", message="Hello", intent="send")` is called
- **THEN** a `notify.v1` envelope is constructed with `delivery.intent="send"`, `delivery.channel="telegram"`, and `delivery.message="Hello"`
- **AND** `origin_butler` matches the calling butler's name

### Requirement: Delivery Intent Validation
Three delivery intents are supported: `send`, `reply`, and `react`. Each has specific field requirements.

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

#### Scenario: Missing message for send/reply
- **WHEN** `intent="send"` and `message` is `None` or empty
- **THEN** the tool returns `{"status": "error", "error": "Missing required 'message' parameter..."}`

#### Scenario: Unsupported intent
- **WHEN** `intent` is not one of `send`, `reply`, `react`
- **THEN** the tool returns an error response

### Requirement: Channel Validation
Only `telegram` and `email` channels are currently supported. Unsupported channels produce an immediate error response.

#### Scenario: Supported channel
- **WHEN** `channel="telegram"` or `channel="email"` is passed
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Unsupported channel
- **WHEN** `channel="sms"` is passed
- **THEN** the tool returns `{"status": "error", "error": "Unsupported channel 'sms'..."}`

### Requirement: Request Context Propagation
For `reply` and `react` intents, the `request_context` must carry lineage from the originating inbound request. This enables the Messenger butler to route the delivery to the correct conversation thread.

#### Scenario: Request context forwarded to envelope
- **WHEN** `notify(intent="reply", request_context={...})` is called with valid context
- **THEN** the `request_context` is included in the `notify.v1` envelope as-is

#### Scenario: Request context from runtime session
- **WHEN** a notify call happens during a routed session
- **THEN** the runtime can pass the `request_context` from its session's routing lineage

### Requirement: NotifyRequestContextInput Schema
The `request_context` parameter follows the `NotifyRequestContextInput` TypedDict with required fields (`request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`) and optional fields (`source_thread_identity`, `received_at`).

#### Scenario: Valid request context
- **WHEN** `request_context` includes all required fields
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Missing required context field for reply
- **WHEN** `intent="reply"` and `request_context` is missing `request_id`
- **THEN** the tool returns a validation error

### Requirement: [TARGET-STATE] Messenger Routing via Switchboard
The `notify.v1` envelope is carried inside a Switchboard-routed `route.v1` payload and executed by the Messenger butler's `route.execute`. The Messenger returns `route_response.v1` with a `notify_response.v1` nested result.

#### Scenario: Notify routed through Switchboard
- **WHEN** a butler calls `notify()`
- **THEN** the daemon routes the `notify.v1` envelope through the Switchboard MCP client to the Messenger butler

### Requirement: [TARGET-STATE] Notify Response Envelope
Successful delivery returns `notify_response.v1` with `status="ok"` and delivery metadata. Failed delivery returns `status="error"` with canonical error class and message.

#### Scenario: Successful delivery response
- **WHEN** the Messenger successfully delivers the message
- **THEN** the notify tool returns a response with `status="ok"` and `delivery.channel` and `delivery.delivery_id`

#### Scenario: Failed delivery response
- **WHEN** the Messenger fails to deliver
- **THEN** the notify tool returns a response with `status="error"`, `error.class`, and `error.message`

### Requirement: Origin Butler Identity
Every outbound interaction must include the originating butler's identity as `origin_butler` in the envelope. This is set automatically from the daemon's configuration.

#### Scenario: Origin butler set automatically
- **WHEN** the `health` butler calls `notify()`
- **THEN** the envelope's `origin_butler` field is `"health"`

### Requirement: [TARGET-STATE] Idempotency and Replay Tolerance
Because fanout is at-least-once, butlers must tolerate duplicate routed subrequests where request lineage matches.

#### Scenario: Duplicate notify tolerated
- **WHEN** the same `notify.v1` envelope is delivered twice with the same `request_context.request_id`
- **THEN** the Messenger applies deduplication or the butler tolerates the duplicate response
