# Notify Contract

## Purpose
Defines the `notify` MCP tool and its versioned envelope contract (`notify.v1`) for outbound user interaction requests from non-messenger butlers, routed through Switchboard to the Messenger butler for delivery.

## ADDED Requirements

### Requirement: Notify Tool Registration
Every butler daemon SHALL register a `notify(channel, message, contact_id?, recipient?, subject?, intent?, emoji?, request_context?)` MCP tool during startup. Runtime instances MUST be able to call this tool to send outbound notifications. The tool MUST be available in every butler's MCP tool surface regardless of which modules are enabled.

#### Scenario: Tool available to runtime instance
- **WHEN** a runtime instance spawned by a butler lists available MCP tools
- **THEN** the `notify` tool MUST appear in the tool list with parameters `channel` (required string), `message` (required string), `contact_id` (optional UUID string), `recipient` (optional string), `subject` (optional string), `intent` (optional string), `emoji` (optional string), and `request_context` (optional object)

#### Scenario: Tool registered at startup
- **WHEN** a butler daemon starts up
- **THEN** the `notify` tool MUST be registered as a core MCP tool before the butler accepts any triggers

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

### Requirement: Default Recipient Resolution
The `notify` tool accepts `contact_id` (UUID) and `recipient` (string) as optional parameters for specifying the target. Resolution priority SHALL be: (1) if `contact_id` is provided, resolve the target's channel identifier from `shared.contact_info`; (2) if `recipient` string is provided, use it as-is; (3) if neither is provided, resolve the owner contact's channel identifier from `shared.contact_info`.

#### Scenario: Contact-based recipient resolution
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Your dental appointment is tomorrow', contact_id='abc-123')`
- **AND** contact `abc-123` has a `contact_info` entry with `type='telegram'`, `value='12345'`, `is_primary=true`
- **THEN** the butler daemon MUST resolve the Telegram chat ID to `12345` and deliver to that recipient

#### Scenario: Contact-based resolution with multiple entries
- **WHEN** a contact has two `contact_info` entries of `type='email'`: one with `is_primary=true` and one with `is_primary=false`
- **AND** `notify(channel='email', message='...', contact_id=...)` is called
- **THEN** the daemon MUST use the `is_primary=true` entry's value
- **AND** if no primary entry exists, the daemon MUST use the first entry of matching type

#### Scenario: Omitted contact_id and recipient defaults to system owner
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Alert')` without `contact_id` or `recipient`
- **THEN** the butler daemon MUST resolve the owner contact (the contact with `'owner' = ANY(roles)`)
- **AND** MUST look up the owner's `contact_info` entry of the matching channel type
- **AND** MUST deliver to that resolved identifier

#### Scenario: Explicit recipient string provided
- **WHEN** a runtime instance calls `notify(channel='email', message='Report', recipient='user@example.com')`
- **THEN** the butler daemon MUST forward the call to the Switchboard with `recipient='user@example.com'`
- **AND** `contact_id`-based resolution MUST NOT be attempted

### Requirement: Missing Channel Identifier Fallback
When `contact_id` is provided but the contact has no `contact_info` entry for the requested channel, the `notify` tool MUST NOT silently fail. Instead, it MUST park the notification as a `pending_action` in the approval system and notify the owner to provide the missing channel identifier.

#### Scenario: Contact missing Telegram identifier
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Reminder', contact_id='abc-123')`
- **AND** contact `abc-123` has no `contact_info` entry with `type='telegram'`
- **THEN** the notify tool MUST create a `pending_action` with `tool_name='notify'`, `status='pending'`, and `agent_summary` explaining that the contact has no Telegram identifier on file
- **AND** the tool MUST return `{"status": "pending_missing_identifier", "action_id": "...", "message": "Cannot deliver telegram notification -- no telegram identifier on file."}`

#### Scenario: Owner notified of missing identifier
- **WHEN** a notification is parked due to a missing channel identifier
- **THEN** the owner MUST be notified via their preferred channel with the missing identifier details and a link to the contact's page

### Requirement: Role-Based Approval Gating for Notify
The `notify` tool SHALL apply approval gating based on the target contact's roles. Notifications to contacts with `'owner'` in their roles MUST bypass the approval gate. Notifications to contacts without the `'owner'` role MUST be subject to the approval gate (checking standing rules, else pending).

#### Scenario: Notification to owner bypasses approval
- **WHEN** `notify(channel='telegram', message='Alert')` is called with no contact_id (defaults to owner)
- **THEN** the notification MUST be delivered without requiring approval

#### Scenario: Notification to non-owner requires approval
- **WHEN** `notify(channel='telegram', message='Reminder', contact_id='abc-123')` is called
- **AND** contact `abc-123` has `roles = []` (non-owner)
- **THEN** the notification MUST be checked against standing approval rules
- **AND** if no rule matches, it MUST be parked as a pending action

#### Scenario: Standing rule auto-approves non-owner notification
- **WHEN** a standing approval rule exists matching `tool_name='notify'` with constraint `contact_id='abc-123'`
- **AND** `notify(channel='telegram', message='Hi', contact_id='abc-123')` is called
- **THEN** the notification MUST be auto-approved and delivered immediately

#### Scenario: Unresolvable target requires approval
- **WHEN** `notify(channel='telegram', message='Hi', recipient='unknown@example.com')` is called
- **AND** reverse-lookup of `('email', 'unknown@example.com')` returns no contact
- **THEN** the notification MUST require approval (conservative default)

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
