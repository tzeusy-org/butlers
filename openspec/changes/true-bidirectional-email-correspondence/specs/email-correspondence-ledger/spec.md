## ADDED Requirements

### Requirement: Messenger-private metadata-only correspondence ledger

Messenger SHALL record each approved outbound email in a private
`messenger.email_correspondence` table limited to an opaque idempotency key,
provider, account reference, normalized bare peer address, optional provider
message ID, categorical state, and lifecycle timestamps. The table SHALL NOT hold
JSONB, free text, subject, body, headers, attachments, raw provider responses,
credentials, or error text. Content-bearing stores (`public.audit_log`,
`switchboard.notifications`, `switchboard.message_inbox`) SHALL NOT be used as
correspondence evidence.

#### Scenario: An intent records only allowlisted metadata

- **WHEN** Messenger admits an approved email send
- **THEN** the ledger row contains only the allowlisted columns
- **AND** no message content or raw provider value is stored

#### Scenario: Content-bearing stores are not evidence

- **WHEN** the aggregate is computed
- **THEN** it reads only the private ledger, never audit, notification, or inbox rows

### Requirement: Transactional intent and conservative states

Messenger SHALL commit the intent row before any provider call and SHALL NOT send
if that commit fails. States SHALL be `unknown`, `accepted`, `confirmed`, and
`failed`, with monotonic transitions except `accepted -> unknown` when the
confirmation deadline passes. A send SHALL NOT be retried after an ambiguous
dispatch unless the provider's idempotency contract is bound to the same key.

#### Scenario: Intent persistence failure prevents egress

- **WHEN** the intent row cannot be committed
- **THEN** no provider call is made

#### Scenario: Crash after dispatch is indeterminate

- **WHEN** the process fails after dispatch but before an outcome is recorded
- **THEN** the row remains `unknown` and the message is not resent

#### Scenario: SMTP acceptance is not confirmation

- **WHEN** SMTP accepts a message
- **THEN** the row becomes `accepted` and, at its deadline, `unknown`
- **AND** it never becomes `confirmed`

### Requirement: Exact-reference provider-Sent confirmation

A row SHALL become `confirmed` only when Messenger, using a provider-native send
that returned an exact message ID, verifies through one exact-ID metadata lookup
that the provider holds that message in Sent. Confirmation SHALL NOT list,
search, or traverse a mailbox, backfill, read content, or use the Gmail
connector's Sent-ID cache. The native path SHALL be disabled by default per
account.

#### Scenario: Exact Gmail confirmation

- **WHEN** a Gmail API send returned message ID M and a lookup of M shows the `SENT` label
- **THEN** the row for M becomes `confirmed`

#### Scenario: Confirmation cannot enumerate mail

- **WHEN** confirmation runs
- **THEN** it issues only exact-ID lookups for recorded message IDs

### Requirement: Bounded aggregate and retention

Messenger SHALL expose `messenger.v_confirmed_email_outbound`, a read-only view
returning per normalized peer a capped confirmed count and last confirmed time
over the trailing 180 days, and nothing else. Every ledger row SHALL be
hard-deleted no later than 180 days after its intent time.

#### Scenario: Aggregate hides raw rows

- **WHEN** Relationship selects from the view
- **THEN** it receives only peer, capped count, and last confirmed time
- **AND** no message ID, account reference, or state row is exposed

#### Scenario: Expired evidence is purged

- **WHEN** a row passes 180 days after its intent time
- **THEN** the maintenance job deletes it and it no longer affects the view
