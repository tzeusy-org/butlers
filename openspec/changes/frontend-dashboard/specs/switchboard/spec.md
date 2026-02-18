# Switchboard (Delta)

Delta spec for the `switchboard` capability. Adds a `deliver` tool for outbound notification dispatch and a `notifications` table for delivery tracking.

---

## ADDED Requirements

### Requirement: deliver tool

The Switchboard SHALL expose an MCP tool named `deliver` with the signature `deliver(channel: str, message: str, recipient: str | None = None, metadata: dict | None = None)` that dispatches an outbound notification to the specified channel's module (telegram or email), logs the delivery to the `notifications` table, and returns the delivery result.

#### Scenario: Deliver a Telegram message

- **WHEN** `deliver(channel="telegram", message="Your daily summary is ready.", recipient="12345")` is called
- **THEN** the Switchboard MUST invoke the Telegram module's send function to deliver the message to chat ID `12345`
- **AND** a row MUST be inserted into the `notifications` table with `channel` set to `'telegram'`, `recipient` set to `'12345'`, `message` set to the message text, and `status` set to `'sent'`
- **AND** the tool MUST return a result indicating successful delivery

#### Scenario: Deliver an email message

- **WHEN** `deliver(channel="email", message="Weekly health report attached.", recipient="user@example.com", metadata={"subject": "Weekly Report"})` is called
- **THEN** the Switchboard MUST invoke the Email module's send function to deliver the message to the specified email address
- **AND** a row MUST be inserted into the `notifications` table with `channel` set to `'email'`, `recipient` set to `'user@example.com'`, `metadata` containing the provided metadata object, and `status` set to `'sent'`

#### Scenario: Deliver with no explicit recipient

- **WHEN** `deliver(channel="telegram", message="System alert: health butler is unresponsive.")` is called without a `recipient`
- **THEN** the Switchboard MUST use the channel module's default recipient (e.g., the configured default Telegram chat ID)
- **AND** the `notifications` row MUST have `recipient` set to NULL

#### Scenario: Deliver captures source butler context

- **WHEN** `deliver()` is called during a runtime session spawned on behalf of the `health` butler (via `route()` or `notify()`)
- **THEN** the `notifications` row's `source_butler` field MUST be set to `'health'`
- **AND** the `session_id` field MUST be set to the current session's UUID if available
- **AND** the `trace_id` field MUST be set to the current OpenTelemetry trace ID if available

#### Scenario: Deliver with metadata

- **WHEN** `deliver(channel="telegram", message="Reminder: call Mom", metadata={"priority": "high", "butler": "relationship"})` is called
- **THEN** the `notifications` row's `metadata` field MUST contain the JSONB representation of `{"priority": "high", "butler": "relationship"}`

---

### Requirement: Notifications table provisioning

The `notifications` table SHALL be provisioned in the Switchboard butler's database via an Alembic migration in the `switchboard` version chain.

#### Scenario: Switchboard starts with a fresh database

- **WHEN** the Switchboard butler starts up against a newly provisioned database
- **THEN** the `notifications` table MUST exist with columns:
  - `id` (UUID PRIMARY KEY DEFAULT gen_random_uuid())
  - `source_butler` (TEXT NOT NULL)
  - `channel` (TEXT NOT NULL)
  - `recipient` (TEXT, nullable)
  - `message` (TEXT NOT NULL)
  - `metadata` (JSONB DEFAULT '{}')
  - `status` (TEXT DEFAULT 'sent')
  - `error` (TEXT, nullable)
  - `session_id` (UUID, nullable)
  - `trace_id` (TEXT, nullable)
  - `created_at` (TIMESTAMPTZ DEFAULT now())
- **AND** an index MUST exist on `(source_butler, created_at DESC)`
- **AND** an index MUST exist on `(channel, created_at DESC)`
- **AND** an index MUST exist on `(status)`

#### Scenario: Notifications table coexists with existing Switchboard tables

- **WHEN** the Switchboard database is provisioned
- **THEN** the `notifications` table MUST coexist with the `butler_registry` and `routing_log` tables without conflicts
- **AND** the Alembic migration for `notifications` MUST be applied after the existing Switchboard migrations

---

### Requirement: deliver handles module errors

When the target channel module (telegram or email) fails during delivery, the `deliver` tool SHALL record the failure in the `notifications` table and return an error result rather than raising an exception.

#### Scenario: Telegram module fails to send

- **WHEN** `deliver(channel="telegram", message="Hello", recipient="12345")` is called and the Telegram module raises an error (e.g., network timeout, invalid chat ID)
- **THEN** a row MUST still be inserted into the `notifications` table
- **AND** the row's `status` MUST be set to `'failed'`
- **AND** the row's `error` MUST contain the error message from the Telegram module
- **AND** the tool MUST return a result indicating delivery failure with the error message
- **AND** the tool MUST NOT raise an unhandled exception

#### Scenario: Email module fails to send

- **WHEN** `deliver(channel="email", message="Report", recipient="user@example.com")` is called and the Email module raises an error (e.g., SMTP connection refused)
- **THEN** the `notifications` row's `status` MUST be set to `'failed'`
- **AND** the `error` field MUST contain the error message from the Email module

#### Scenario: Unknown channel requested

- **WHEN** `deliver(channel="sms", message="Hello")` is called and no module is registered for the `sms` channel
- **THEN** the `notifications` row MUST be inserted with `status` set to `'failed'` and `error` indicating that no module is available for channel `'sms'`
- **AND** the tool MUST return an error result
