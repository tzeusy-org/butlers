## MODIFIED Requirements

### Requirement: Envelope factory functions
The test suite SHALL provide factory functions for constructing realistic `ingest.v1` payloads for each supported channel.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Email envelope construction
- **WHEN** `email_envelope(sender="alice@example.com", subject="Team lunch Thursday", body="Let's do noon at the usual place")` is called
- **THEN** the returned dict is a valid `ingest.v1` payload with `source.channel="email"`, `source.provider="gmail"`, correct headers (`From`, `Subject`), `normalized_text` containing the body, and a deterministic `idempotency_key` based on a generated message ID

#### Scenario: Telegram envelope construction
- **WHEN** `telegram_envelope(chat_id=12345, text="I ran 5km this morning", from_user="test-user")` is called
- **THEN** the returned dict is a valid `ingest.v1` payload with `source.channel="telegram"`, `source.provider="telegram"`, correct chat/user metadata in `payload.raw`, and `idempotency_key="tg:12345:<generated_message_id>"`

#### Scenario: Thread ID for email replies
- **WHEN** `email_envelope(..., thread_id="thread-abc")` is called with a thread ID
- **THEN** the envelope includes `event.external_thread_id="thread-abc"`
- **AND** no test relies on the thread ID selecting a target butler

### Requirement: Downstream effect verification
After injection, goal admission and goal completion, the test harness SHALL verify downstream effects via DB assertions, tool-call capture of the goal's invocations, and the goal's effect ledger.

Source: RFC 0038 D5, D12, D17
Scope: v1-mandatory

#### Scenario: Calendar event created from email
- **WHEN** an email envelope about "Team lunch Thursday at noon" is injected and admitted as a goal
- **THEN** the goal's effect ledger records a `calendar_create` call from one of its invocations (verified via tool-call capture)
- **AND** when the goal's ceiling and exposure let the effect execute, the calendar schema contains a new event (verified via DB assertion); otherwise the effect is parked awaiting owner approval

#### Scenario: Health measurement logged from telegram
- **WHEN** an owner telegram envelope with text "I weigh 75.5 kg today" is injected and admitted as an owner goal
- **THEN** an invocation of that goal calls the health domain's meal/measurement logging capability and the health schema contains a new measurement record

#### Scenario: Reply sent for interactive message
- **WHEN** an owner telegram envelope with a conversational question is injected and admitted as an owner goal
- **THEN** the goal's lead calls `notify` (or equivalent outbound tool) with a reply targeting the originating chat

### Requirement: Scenario corpus with unified definition
All scenarios SHALL use a single `Scenario` dataclass that combines envelope, expected tool calls, and DB assertions, with no expected routing target.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Scenario definition
- **WHEN** a new test scenario is authored
- **THEN** it specifies: `id`, `description`, `envelope` (via factory function), `expected_tool_calls` (list of tool names), `db_assertions` (list of post-execution queries), and `tags` (for filtering)

#### Scenario: Tag-based filtering
- **WHEN** tests are run with `--scenarios=smoke`
- **THEN** only scenarios tagged with `smoke` are executed
