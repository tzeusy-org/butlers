## MODIFIED Requirements

### Requirement: Notify Tool Registration
Every butler daemon SHALL register a `notify(channel, message, contact_id?, recipient?, subject?, intent?, emoji?, request_context?, priority?)` MCP tool during startup. Runtime instances MUST be able to call this tool to send outbound notifications. The tool MUST be available in every butler's MCP tool surface regardless of which modules are enabled.

The `priority` parameter (enum: `high`, `medium`, `low`, default `medium`) is added to support time-aware delivery. Priority determines quiet-hours behavior: high-priority notifications always deliver immediately; medium and low-priority notifications are subject to quiet-hours deferral when delivery preferences are configured.

#### Scenario: Tool available to runtime instance
- **WHEN** a runtime instance spawned by a butler lists available MCP tools
- **THEN** the `notify` tool MUST appear in the tool list with parameters `channel` (required string), `message` (required string), `contact_id` (optional UUID string), `recipient` (optional string), `subject` (optional string), `intent` (optional string), `emoji` (optional string), `request_context` (optional object), and `priority` (optional string, default `medium`)

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

### Requirement: Channel Validation
Only `telegram` and `email` channels are currently supported. Unsupported channels produce an immediate error response.

#### Scenario: Supported channel
- **WHEN** `channel="telegram"` or `channel="email"` is passed
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Unsupported channel
- **WHEN** `channel="sms"` is passed
- **THEN** the tool returns `{"status": "error", "error": "Unsupported channel 'sms'..."}`
