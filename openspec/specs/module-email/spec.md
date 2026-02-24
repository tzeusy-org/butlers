# Email Module

## Purpose

The Email module provides email MCP tools for inbox search, message reading, sending, and replying using IMAP and SMTP, with classification pipeline integration for incoming message routing.

## ADDED Requirements

### Requirement: Email Tools

The module registers MCP tools for inbox operations and message send/reply.

#### Scenario: Email read tools

- **WHEN** the email module registers tools
- **THEN** the following read tools are available:
  - `email_search_inbox` (search inbox by query)
  - `email_read_message` (read a specific message by ID)

#### Scenario: Email write tools

- **WHEN** the email module registers tools
- **THEN** the following write tools are available:
  - `email_send_message` (compose and send a new email)
  - `email_reply_to_thread` (reply to an existing email thread)

### Requirement: EmailConfig with Credential Scoping

Configuration supports independent enable/disable per identity scope with configurable env var names for credentials.

#### Scenario: Config structure

- **WHEN** `[modules.email]` is configured
- **THEN** it includes `smtp_host` (default "smtp.gmail.com"), `smtp_port` (default 587), `imap_host` (default "imap.gmail.com"), `imap_port` (default 993), `use_tls` (default true)
- **AND** `[modules.email.user]` with `enabled` (default false), `address_env`, `password_env`
- **AND** `[modules.email.bot]` with `enabled` (default true), `address_env`, `password_env`

#### Scenario: Env var name validation

- **WHEN** credential env var names are configured
- **THEN** they must match the pattern `^[A-Za-z_][A-Za-z0-9_]*$`
- **AND** empty or whitespace-only values are rejected

### Requirement: Credential Resolution

Credentials are resolved at startup via CredentialStore (DB-first, then env) and cached.

#### Scenario: Startup credential resolution

- **WHEN** `on_startup` is called with a credential store
- **THEN** all configured credential keys are resolved and cached in `_resolved_credentials`
- **AND** runtime helpers use the cached values first, falling back to `os.environ`

#### Scenario: credentials_env property

- **WHEN** `credentials_env` is queried
- **THEN** it returns the env var names for all enabled scopes (address and password for each)

### Requirement: IMAP Inbox Search

Email inbox search uses IMAP SEARCH commands via stdlib `imaplib`.

#### Scenario: Search inbox

- **WHEN** `email_search_inbox` is called with a query string
- **THEN** IMAP SEARCH is executed against the INBOX folder
- **AND** up to 50 most recent matching message headers are returned with `message_id`, `from`, `subject`, `date`
- **AND** blocking IMAP calls are run via `asyncio.to_thread`

### Requirement: IMAP Message Reading

Full message reading via IMAP FETCH.

#### Scenario: Read a message

- **WHEN** `email_read_message` is called with a message_id
- **THEN** the full RFC822 message is fetched via IMAP
- **AND** the response includes `message_id`, `from`, `to`, `subject`, `date`, `rfc_message_id`, `body`
- **AND** multipart messages extract the text/plain part; single-part messages decode the payload

### Requirement: SMTP Email Sending

Email sending uses SMTP via stdlib `smtplib`.

#### Scenario: Send email

- **WHEN** `email_send_message` is called with `to`, `subject`, `body`
- **THEN** a MIME text email is constructed and sent via SMTP
- **AND** TLS STARTTLS is used when `use_tls` is configured
- **AND** the response includes `{"status": "sent", "to": ..., "subject": ...}`

#### Scenario: Reply to thread

- **WHEN** `email_reply_to_thread` is called with `to`, `thread_id`, `body`, and optional `subject`
- **THEN** the email is sent with a subject defaulting to `Re: {thread_id}` if not provided
- **AND** the `thread_id` is included in the response

### Requirement: Classification Pipeline Integration (Deprecated)

The `email_check_and_route_inbox` tool is deprecated in favor of GmailConnector.

#### Scenario: Check and route inbox

- **WHEN** `email_check_and_route_inbox` is called
- **THEN** a deprecation warning is emitted
- **AND** unseen emails are fetched, classified via the attached `MessagePipeline`, and routed
- **AND** each email's routing result includes `target_butler` and status

#### Scenario: Pipeline not configured

- **WHEN** `email_check_and_route_inbox` is called without a pipeline
- **THEN** `{"status": "no_pipeline", "message": "No classification pipeline configured"}` is returned
