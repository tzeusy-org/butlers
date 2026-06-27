# Notify Contract

## Purpose
Defines the `notify` MCP tool and its versioned envelope contract (`notify.v1`) for outbound user interaction requests from non-messenger butlers, routed through Switchboard to the Messenger butler for delivery.

## ADDED Requirements

### Requirement: Notify Tool Registration
Every butler daemon SHALL register a `notify(channel, message, entity_id?, recipient?, subject?, intent?, emoji?, request_context?, priority?)` MCP tool during startup. Runtime instances MUST be able to call this tool to send outbound notifications. The tool MUST be available in every butler's MCP tool surface regardless of which modules are enabled.

Note: target resolution is keyed on `entity_id` (a `public.entities` UUID), resolved against `relationship.entity_facts`. An earlier design used a `contact_id` keyed on `public.contacts` / `public.contact_info`; that identity path was retired in favor of the entity graph. The requirements below reflect the entity-graph reality.

The `priority` parameter (enum: `high`, `medium`, `low`, default `medium`) is added to support time-aware delivery. Priority determines quiet-hours behavior: high-priority notifications always deliver immediately; medium and low-priority notifications are subject to quiet-hours deferral when delivery preferences are configured.

#### Scenario: Tool available to runtime instance
- **WHEN** a runtime instance spawned by a butler lists available MCP tools
- **THEN** the `notify` tool MUST appear in the tool list with parameters `channel` (required string), `message` (required string), `entity_id` (optional UUID string), `recipient` (optional string), `subject` (optional string), `intent` (optional string), `emoji` (optional string), `request_context` (optional object), and `priority` (optional string, default `medium`)

#### Scenario: Tool registered at startup
- **WHEN** a butler daemon starts up
- **THEN** the `notify` tool MUST be registered as a core MCP tool before the butler accepts any triggers

### Requirement: Quiet Hours Delivery Gate
Before constructing the notification envelope, the `notify()` tool SHALL check the butler's `delivery_preferences` for quiet hours enforcement. If the current time (in the user's configured timezone) falls within quiet hours and the notification's priority is not `high`, the notification SHALL be deferred to the `deferred_notifications` table instead of being delivered immediately.

#### Scenario: Notification deferred during quiet hours
- **WHEN** `notify(channel="telegram", message="Weekly report", priority="medium")` is called
- **AND** delivery preferences have `quiet_hours_start="22:00"`, `quiet_hours_end="07:00"`, `timezone="America/New_York"`
- **AND** the current time in America/New_York is 23:15
- **THEN** the notification is stored in `deferred_notifications` with `deliver_at` set to the next 07:00 America/New_York
- **AND** the tool returns `{"status": "deferred", "deliver_at": "<ISO timestamp>", "notification_id": "<uuid>"}`

#### Scenario: High-priority bypasses quiet hours
- **WHEN** `notify(channel="telegram", message="Critical alert", priority="high")` is called during quiet hours
- **THEN** the notification is delivered immediately via the standard envelope pipeline
- **AND** quiet hours are NOT applied

#### Scenario: No delivery preferences configured
- **WHEN** `notify()` is called and no `delivery_preferences` row exists for this butler
- **THEN** the notification is delivered immediately regardless of time or priority (backward compatible)

#### Scenario: Quiet hours with channel override
- **WHEN** `notify(channel="email", message="Report", priority="medium")` is called
- **AND** delivery preferences have `override_channels={"email": {quiet_hours_start: "20:00", quiet_hours_end: "09:00"}}`
- **AND** the current time is 21:00 local
- **THEN** the email-specific quiet hours apply and the notification is deferred

### Requirement: notify.v1 Envelope Schema
The notify envelope includes `schema_version` ("notify.v1"), `origin_butler` (requesting butler's name), `delivery` (intent, channel, message, optional recipient/subject/emoji), and optional `request_context` for reply/react targeting.

#### Scenario: Send intent envelope
- **WHEN** `notify(channel="telegram", message="Hello", intent="send")` is called
- **THEN** a `notify.v1` envelope is constructed with `delivery.intent="send"`, `delivery.channel="telegram"`, and `delivery.message="Hello"`
- **AND** `origin_butler` matches the calling butler's name

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
The `notify` tool accepts `entity_id` (UUID) and `recipient` (string) as optional parameters for specifying the target. Resolution priority SHALL be: (1) if `entity_id` is provided, resolve the target's channel identifier from `relationship.entity_facts` (active triple preferred) for the channel predicate (e.g. a `telegram:<id>` fact for the telegram channel); (2) if `recipient` string is provided, use it as-is; (3) if neither is provided, default to the owner and the channel's default order (telegram, then email).

#### Scenario: Entity-based recipient resolution
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Your dental appointment is tomorrow', entity_id='abc-123')`
- **AND** entity `abc-123` has an active `relationship.entity_facts` triple for the telegram channel with value `12345`
- **THEN** the butler daemon MUST resolve the Telegram chat ID to `12345` and deliver to that recipient

#### Scenario: Entity-based resolution prefers the active triple
- **WHEN** an entity has multiple `relationship.entity_facts` triples for the email channel
- **AND** `notify(channel='email', message='...', entity_id=...)` is called
- **THEN** the daemon MUST use the active triple's value
- **AND** if no active triple exists for the channel, the notify call MUST follow the Missing Channel Identifier Fallback below

#### Scenario: Omitted entity_id and recipient defaults to system owner
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Alert')` without `entity_id` or `recipient`
- **THEN** the butler daemon MUST resolve the owner's channel identifier from the owner entity's `relationship.entity_facts`
- **AND** MUST deliver to that resolved identifier

#### Scenario: Explicit recipient string provided
- **WHEN** a runtime instance calls `notify(channel='email', message='Report', recipient='user@example.com')`
- **THEN** the butler daemon MUST forward the call to the Switchboard with `recipient='user@example.com'`
- **AND** `entity_id`-based resolution MUST NOT be attempted

### Requirement: Missing Channel Identifier Fallback
When `entity_id` is provided but the entity has no `relationship.entity_facts` triple for the requested channel, the `notify` tool MUST NOT silently fail. Instead, it MUST park the notification as a `pending_action` in the approval system and notify the owner to provide the missing channel identifier.

#### Scenario: Entity missing Telegram identifier
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Reminder', entity_id='abc-123')`
- **AND** entity `abc-123` has no `relationship.entity_facts` triple for the telegram channel
- **THEN** the notify tool MUST create a `pending_action` with `tool_name='notify'`, `status='pending'`, and `agent_summary` explaining that the entity has no Telegram identifier on file
- **AND** the tool MUST return `{"status": "pending_missing_identifier", "action_id": "...", "message": "Cannot deliver telegram notification -- no telegram identifier on file."}`

#### Scenario: Owner notified of missing identifier
- **WHEN** a notification is parked due to a missing channel identifier
- **THEN** the owner MUST be notified via their preferred channel with the missing identifier details and a link to the contact's page

### Requirement: Role-Based Approval Gating for Notify
The `notify` tool SHALL apply approval gating based on whether the target is the owner. Notifications to the owner MUST bypass the approval gate. Notifications to a non-owner entity MUST be subject to the approval gate (checking standing rules, else pending).

#### Scenario: Notification to owner bypasses approval
- **WHEN** `notify(channel='telegram', message='Alert')` is called with no entity_id (defaults to owner)
- **THEN** the notification MUST be delivered without requiring approval

#### Scenario: Notification to non-owner requires approval
- **WHEN** `notify(channel='telegram', message='Reminder', entity_id='abc-123')` is called
- **AND** entity `abc-123` is not the owner
- **THEN** the notification MUST be checked against standing approval rules
- **AND** if no rule matches, it MUST be parked as a pending action

#### Scenario: Standing rule auto-approves non-owner notification
- **WHEN** a standing approval rule exists matching `tool_name='notify'` with constraint `entity_id='abc-123'`
- **AND** `notify(channel='telegram', message='Hi', entity_id='abc-123')` is called
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

### Requirement: Messenger route.execute Approval Gate (Defense-in-Depth)
The Messenger's `route.execute` handler calls channel module methods directly (`_send_email()`, `_send_message()`, `_reply_to_message()`), bypassing MCP tool wrappers. Because the MCP-level approval gate is not in this code path, `route.execute` MUST independently re-enforce role-based approval gating before invoking any outbound channel adapter.

This requirement exists because the delivery architecture has two layers: `notify()` MCP tool → Switchboard `deliver()` → Messenger `route.execute` → direct module call. If only the first layer gates, any bypass of the MCP tool layer (e.g., direct `route.execute` invocation) would allow ungated delivery.

#### Scenario: Messenger route.execute blocks non-owner email without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope with `channel="email"`
- **AND** the email target resolves to a non-owner contact (or is unknown)
- **AND** no standing approval rule matches `email_send_message` or `email_reply_to_thread` for that target
- **THEN** delivery MUST be blocked and a descriptive error returned

#### Scenario: Messenger route.execute blocks non-owner telegram without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope with `channel="telegram"` and `intent` in `("send", "reply")`
- **AND** the telegram target resolves to a non-owner contact (or is unknown)
- **AND** no standing approval rule matches `telegram_send_message` or `telegram_reply_to_message` for that target
- **THEN** delivery MUST be blocked and a descriptive error returned

#### Scenario: Messenger route.execute permits owner delivery without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope
- **AND** the target contact has the `owner` role
- **THEN** delivery proceeds immediately without checking standing rules

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

### Requirement: [TARGET-STATE] Notify Media Attachments
The `notify` tool SHALL accept an optional `delivery.attachments` list so butlers can deliver files and images alongside or instead of text. Each attachment references a blob already persisted in the S3-compatible blob store (`s3-blob-storage`) by `storage_ref`, and the Messenger uploads it to the target channel using channel-native media transport.

#### Scenario: Attachment delivered with message
- **WHEN** `notify(channel="telegram", message="Your report", attachments=[{type:"document", storage_ref:"s3://bucket/key.pdf", filename:"report.pdf", mime_type:"application/pdf"}])` is called
- **THEN** the `notify.v1` envelope includes `delivery.attachments` with each entry's `type`, `storage_ref`, `filename`, and `mime_type`
- **AND** the Messenger fetches each blob by `storage_ref` and uploads it to the channel as native media (Telegram document/photo, email MIME attachment)
- **AND** the text `message`, when present, accompanies the attachment as a caption or body

#### Scenario: Attachment-only delivery
- **WHEN** `notify(channel="email", attachments=[...])` is called with no `message`
- **THEN** delivery proceeds with the attachment(s) and an empty or default body

#### Scenario: Missing blob fails closed
- **WHEN** an attachment `storage_ref` cannot be resolved in the blob store at delivery time
- **THEN** the notify response returns `status="error"` with an error identifying the unresolved `storage_ref`
- **AND** no partial message is delivered without its referenced attachment

### Requirement: [TARGET-STATE] Draft Delivery Intent
A fifth delivery intent `draft` SHALL be supported. Instead of delivering a message, `intent="draft"` creates a reviewable draft in the target channel (e.g. Gmail Drafts) or presents the proposed message to the owner for explicit confirmation before any send. Drafts are non-destructive by design and never reach an external recipient without a subsequent approved send.

#### Scenario: Email draft created, not sent
- **WHEN** `notify(channel="email", intent="draft", recipient="alice@example.com", subject="Re: lunch", message="Sounds good")` is called
- **THEN** a draft is created in the owner's Gmail Drafts and no email is sent to the recipient
- **AND** the notify response returns `status="ok"` with a `draft_ref` identifying the created draft

#### Scenario: Draft never auto-sends
- **WHEN** a draft is created via `intent="draft"`
- **THEN** the message is NOT delivered to any external recipient until a separate, approval-gated send is invoked

### Requirement: [TARGET-STATE] Multi-Channel Delivery
The `notify` tool SHALL accept a list of channels in a single call so one message is delivered to multiple destinations (e.g. telegram and email) with per-channel formatting. A single approval covers all named channels. Delivery status is reported per channel; partial failure does not silently drop the message on other channels.

#### Scenario: Single call fans out to multiple channels
- **WHEN** `notify(channels=["telegram","email"], message="Trip confirmed", subject="Itinerary")` is called
- **THEN** the message is delivered to both telegram and email, each with channel-appropriate formatting (Markdown/Telegram-HTML for telegram, HTML/plain for email)
- **AND** the response reports per-channel outcome (`{telegram:"ok", email:"ok"}`)

#### Scenario: Partial delivery surfaced
- **WHEN** a multi-channel notify succeeds on email but fails on telegram
- **THEN** the response reports `{email:"ok", telegram:"error"}` rather than a single aggregate status
- **AND** the successful channel's delivery is not rolled back

#### Scenario: Single approval covers all channels
- **WHEN** a multi-channel notify to a non-owner requires approval
- **THEN** one pending action is created that names all target channels, and approving it permits delivery to all of them
