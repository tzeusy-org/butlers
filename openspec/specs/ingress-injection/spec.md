# Ingress Injection

## Purpose
Defines the envelope factory functions, direct ingestion injection, downstream effect verification, and unified scenario corpus for end-to-end ingress testing.

## Requirements

### Requirement: Envelope factory functions
The test suite SHALL provide factory functions for constructing realistic `ingest.v1` payloads for each supported channel.

#### Scenario: Email envelope construction
- **WHEN** `email_envelope(sender="alice@example.com", subject="Team lunch Thursday", body="Let's do noon at the usual place")` is called
- **THEN** the returned dict is a valid `ingest.v1` payload with `source.channel="email"`, `source.provider="gmail"`, correct headers (`From`, `Subject`), `normalized_text` containing the body, and a deterministic `idempotency_key` based on a generated message ID

#### Scenario: Telegram envelope construction
- **WHEN** `telegram_envelope(chat_id=12345, text="I ran 5km this morning", from_user="test-user")` is called
- **THEN** the returned dict is a valid `ingest.v1` payload with `source.channel="telegram"`, `source.provider="telegram"`, correct chat/user metadata in `payload.raw`, and `idempotency_key="tg:12345:<generated_message_id>"`

#### Scenario: Thread ID for email replies
- **WHEN** `email_envelope(..., thread_id="thread-abc")` is called with a thread ID
- **THEN** the envelope includes `event.external_thread_id="thread-abc"` enabling thread affinity routing

### Requirement: Direct injection into ingest_v1()
All ingress scenarios SHALL call `ingest_v1(pool, envelope)` directly, bypassing connectors and MCP transport.

#### Scenario: Successful injection
- **WHEN** a valid email envelope is injected via `ingest_v1(pool, envelope)`
- **THEN** the function returns `IngestAcceptedResponse` with `status="accepted"` and a `request_id`

#### Scenario: Deduplication on replay
- **WHEN** the same envelope is injected twice
- **THEN** the second call returns `duplicate=True` with the same `request_id`

### Requirement: Downstream effect verification
After injection and session completion, the test harness SHALL verify downstream effects via DB assertions and tool-call capture.

#### Scenario: Calendar event created from email
- **WHEN** an email envelope about "Team lunch Thursday at noon" is injected and routed to the appropriate butler
- **THEN** the butler's session calls `calendar_create` (verified via tool-call capture) and the calendar schema contains a new event (verified via DB assertion)

#### Scenario: Health measurement logged from telegram
- **WHEN** a telegram envelope with text "I weigh 75.5 kg today" is injected and routed to `health`
- **THEN** the butler's session calls the meal/measurement logging tool and the health schema contains a new measurement record

#### Scenario: Reply sent for interactive message
- **WHEN** a telegram envelope with a conversational question is injected and routed to the appropriate butler
- **THEN** the butler's session calls `notify` (or equivalent outbound tool) with a reply targeting the originating chat

### Requirement: Scenario corpus with unified definition
All scenarios SHALL use a single `Scenario` dataclass that combines envelope, expected routing, expected tool calls, and DB assertions.

#### Scenario: Scenario definition
- **WHEN** a new test scenario is authored
- **THEN** it specifies: `id`, `description`, `envelope` (via factory function), `expected_routing` (butler name), `expected_tool_calls` (list of tool names), `db_assertions` (list of post-execution queries), and `tags` (for filtering)

#### Scenario: Tag-based filtering
- **WHEN** tests are run with `--scenarios=smoke`
- **THEN** only scenarios tagged with `smoke` are executed
