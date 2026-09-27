## MODIFIED Requirements

### Requirement: Email Tools

The implementation SHALL provide the behavior described by this requirement.
The module registers MCP tools for inbox operations and message send/reply.

#### Scenario: Email read tools

- **WHEN** the email module registers tools
- **THEN** the following read tools are available:
  - `email_search_inbox` (search inbox by query)
  - `email_read_message` (read a specific message by ID)

#### Scenario: Email write tools

- **WHEN** the email module registers tools AND `send_tools = true` is configured (default `false`)
- **THEN** the following write tools are available:
  - `email_send_message` (compose and send a new email)
  - `email_reply_to_thread` (reply to an existing email thread)
- **AND** when `send_tools = false` these tools are NOT registered (only butlers that opt in, such as the Messenger, enable them)
- **AND** `email_send_message` declares `to` as a safety-critical arg (`tool_metadata`) so the approval gate can intercept outbound sends, enforces the email send permission before SMTP, and writes a `gmail_send` audit event
- **AND** a Messenger-owned send records its correspondence intent before egress

### Requirement: SMTP Email Sending

The implementation SHALL provide the behavior described by this requirement.
Email sending uses SMTP via stdlib `smtplib`; a successful SMTP call is
transport acceptance only and SHALL NOT be reported as provider-Sent
confirmation.

#### Scenario: Send email

- **WHEN** `email_send_message` is called with `to`, `subject`, `body`
- **THEN** a MIME text email is constructed and sent via SMTP
- **AND** TLS STARTTLS is used when `use_tls` is configured
- **AND** the response includes a categorical outcome (`accepted` or `failed`) rather than `sent`

#### Scenario: Reply to thread

- **WHEN** `email_reply_to_thread` is called with `to`, `thread_id`, `body`, and optional `subject`
- **THEN** the email is sent with a subject defaulting to `Re: {thread_id}` if not provided
- **AND** the `thread_id` is included in the response

## ADDED Requirements

### Requirement: Disabled-by-default Gmail native send

The module SHALL offer a per-account, disabled-by-default Gmail API send path for
Messenger that returns the provider message ID so the correspondence ledger can
confirm it by exact-ID lookup. SMTP sends SHALL never become confirmable.

#### Scenario: Native send returns an exact reference

- **WHEN** the Gmail native path is enabled and a send succeeds
- **THEN** the returned message ID is recorded on the ledger row as `accepted`

#### Scenario: SMTP is never confirmable

- **WHEN** a message is sent via SMTP
- **THEN** its ledger row has no provider message ID and cannot become `confirmed`
