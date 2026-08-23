## MODIFIED Requirements

### Requirement: Email Tools

The module SHALL register MCP tools for inbox operations and message send/reply.

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
- **AND** `email_send_message` declares `to` as a safety-critical arg (`tool_metadata`) so the approval gate can intercept outbound sends
- **AND** an email send owned by Messenger enters the private correspondence
  admission path before egress and never writes recipient, subject, body, or
  raw error text to a generic `gmail_send` audit record
- **AND** trusted routing derives the opaque correspondence-candidate context
  before invoking Messenger so validation, network, or no-admission failure is
  also content-free

### Requirement: SMTP Email Sending

Email sending SHALL use SMTP via stdlib `smtplib` when the selected provider is SMTP,
but a successful SMTP call is transport acceptance only.  The module SHALL
construct message content in memory only and return a typed, content-free
delivery outcome to the Messenger correspondence owner.

#### Scenario: Send email

- **WHEN** `email_send_message` is called with `to`, `subject`, `body`
- **THEN** a MIME text email is constructed and sent via SMTP
- **AND** TLS STARTTLS is used when `use_tls` is configured
- **AND** a successful provider outcome is reported as categorical `accepted`,
  not as provider-Sent or bidirectional confirmation
- **AND** the result does not echo `to`, `subject`, `body`, headers, or a raw
  provider/error payload

#### Scenario: Reply to thread

- **WHEN** `email_reply_to_thread` is called with `to`, `thread_id`, `body`, and optional `subject`
- **THEN** the email is sent with a subject defaulting to `Re: {thread_id}` if not provided
- **AND** the thread identifier is passed only to the private correspondence
  admission/provider path when the provider contract supports it
- **AND** the response remains a typed, content-free outcome

## ADDED Requirements

### Requirement: Disabled provider-native exact-reference email sending

Messenger SHALL keep generic SMTP as a transport-only path.  An account MAY
enter provider-Sent confirmation only through an explicitly enabled,
Messenger-initiated provider-native send capability.  After private intent
admission, Messenger SHALL dispatch one approved opaque command through the
authenticated Switchboard broker to the credential-owning Gmail connector and
require the documented native send response to return its exact stable provider
message reference directly through a fenced, principal/dispatch-bound
`correspondence.send.report` callback.  Messenger SHALL atomically record that
direct categorical result before any confirmation lease can exist; an identical
send report is idempotent while stale/conflicting reports fail closed.  The
transient send content SHALL not enter generic Switchboard persistence, logs,
audits, inboxes, traces, or retry state.  SMTP acceptance, RFC message
identifiers, Sent cache values, list/search/history, or post-send inference SHALL
NOT create a confirmation lease.

#### Scenario: SMTP never yields a confirmation lease

- **WHEN** Messenger sends with generic SMTP
- **THEN** it may record only categorical `accepted` and later `unknown`
- **AND** it does not issue a provider-confirmation lease for that intent

#### Scenario: A native send returns the only lease-eligible reference

- **WHEN** an approved account capability dispatches a Messenger-native email
  through the authenticated broker
- **THEN** Gmail can act only on that opaque one-time dispatch and returns the
  exact provider reference and categorical acceptance/rejection outcome through
  the fenced `correspondence.send.report` callback directly from its documented
  send response
- **AND** Messenger alone atomically records whether that reference becomes
  lease-eligible before any confirmation poll
- **AND** the connector cannot initiate a generic outbound email or persist
  transient content on the route
