## MODIFIED Requirements

### Requirement: Same-Window Coalescing of Deferred Notifications
When the deferred-notification flush pass (`_tick_deferred_notification_pass`) finds more than one due (`status='pending' AND deliver_at <= now`) row targeting the same delivery target (channel + recipient), it SHALL compose them into ONE message and deliver them via a single `notify_fn` call instead of one send per row. A delivery target with exactly one due row SHALL be delivered unchanged (its stored envelope, verbatim).

#### Scenario: Multiple same-target due notifications compose into one send
- **WHEN** the flush pass runs and finds 3 due notifications all addressed to
  the same (channel, recipient) pair
- **THEN** exactly one `notify_fn` call is made, carrying a composed message
  that includes all 3 underlying messages
- **AND** all 3 underlying rows are marked `status='delivered'` with the same
  `delivered_at`

#### Scenario: A solo due notification is delivered unchanged
- **WHEN** the flush pass finds exactly one due notification for a given
  delivery target
- **THEN** `notify_fn` is called with that row's stored envelope, unmodified
- **AND** the row is marked `delivered` with no coalescing applied

#### Scenario: Different recipients are never coalesced
- **WHEN** two due notifications target different explicit recipients (or one
  targets an explicit recipient and the other targets none, i.e. the owner's
  default channel)
- **THEN** each is delivered via its own `notify_fn` call — never folded into
  one composed message together

#### Scenario: A failed composed send leaves the whole group pending
- **WHEN** `notify_fn` raises for a composed multi-row digest
- **THEN** every row in that group remains `status='pending'` for retry on
  the next tick — no row in the group is marked `delivered` while others are
  not

### Requirement: notify.v1 Envelope Schema

The notify envelope SHALL include `schema_version` ("notify.v1"), `origin_butler` (requesting butler's name), `delivery` (intent, channel, message, optional recipient/subject/emoji), and optional `request_context` for reply/react targeting.

#### Scenario: Send intent envelope
- **WHEN** `notify(channel="telegram", message="Hello", intent="send")` is called
- **THEN** a `notify.v1` envelope is constructed with `delivery.intent="send"`, `delivery.channel="telegram"`, and `delivery.message="Hello"`
- **AND** `origin_butler` matches the calling butler's name

### Requirement: Delivery Intent Validation

Four delivery intents SHALL be supported: `send`, `reply`, `react`, and `insight`. Each has specific field requirements.

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

### Requirement: Request Context Propagation

For `reply` and `react` intents, the `request_context` MUST carry lineage from the originating inbound request. This enables the Messenger butler to route the delivery to the correct conversation thread.

#### Scenario: Request context forwarded to envelope
- **WHEN** `notify(intent="reply", request_context={...})` is called with valid context
- **THEN** the `request_context` is included in the `notify.v1` envelope as-is

#### Scenario: Request context from runtime session
- **WHEN** a notify call happens during a routed session
- **THEN** the runtime can pass the `request_context` from its session's routing lineage

### Requirement: NotifyRequestContextInput Schema

The `request_context` parameter SHALL follow the `NotifyRequestContextInput` TypedDict with required fields (`request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`) and optional fields (`source_thread_identity`, `received_at`).

#### Scenario: Valid request context
- **WHEN** `request_context` includes all required fields
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Missing required context field for reply
- **WHEN** `intent="reply"` and `request_context` is missing `request_id`
- **THEN** the tool returns a validation error

### Requirement: [TARGET-STATE] Messenger Routing via Switchboard

The `notify.v1` envelope SHALL be carried inside a Switchboard-routed `route.v1` payload and executed by the Messenger butler's `route.execute`. The Messenger returns `route_response.v1` with a `notify_response.v1` nested result.

#### Scenario: Notify routed through Switchboard
- **WHEN** a butler calls `notify()`
- **THEN** the daemon routes the `notify.v1` envelope through the Switchboard MCP client to the Messenger butler

### Requirement: Origin Butler Identity

Every outbound interaction MUST include the originating butler's identity as `origin_butler` in the envelope. This is set automatically from the daemon's configuration.

#### Scenario: Origin butler set automatically
- **WHEN** the `health` butler calls `notify()`
- **THEN** the envelope's `origin_butler` field is `"health"`

### Requirement: [TARGET-STATE] Idempotency and Replay Tolerance

Because fanout is at-least-once, butlers MUST tolerate duplicate routed subrequests where request lineage matches.

#### Scenario: Duplicate notify tolerated
- **WHEN** the same `notify.v1` envelope is delivered twice with the same `request_context.request_id`
- **THEN** the Messenger applies deduplication or the butler tolerates the duplicate response
