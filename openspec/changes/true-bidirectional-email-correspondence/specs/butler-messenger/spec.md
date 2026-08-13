## ADDED Requirements

### Requirement: Private email correspondence evidence ownership

Messenger SHALL own the private, metadata-only email correspondence ledger and
the only writer for its state transitions.  This is evidence for a real direct
adapter path, not a replacement delivery queue, retry subsystem, dead-letter
surface, or fabricated health API.  Messenger may run a deterministic,
non-LLM, non-egress maintenance job solely to expire overdue confirmation
attempts and records; it SHALL not initiate domain behavior or scheduled prompts.
Messenger SHALL also own the private coverage and complete account-universe
continuity metadata used for truthful negative evidence.  The account universe is
an owner-approved static configuration inventory, never provider/mailbox
discovery; its successor may preserve continuity only for an identical member set
with no gap.  Messenger SHALL expose no raw account-universe rows to Relationship.

#### Scenario: Approved email egress records a private intent

- **WHEN** Messenger receives an approved email `notify.v1` delivery intent or
  its native email tool is invoked
- **THEN** it writes the private correspondence intent before calling the
  selected adapter
- **AND** it retains native command/request lineage outside the ledger's
  correspondence metadata
- **AND** it does not create a legacy delivery-tracking record

#### Scenario: Native exact-reference sending remains Messenger-owned

- **WHEN** an enabled provider-native correspondence capability sends an approved
  Messenger email
- **THEN** Messenger creates the private intent and one broker-bound dispatch
  before egress and remains the only authority that can accept the returned exact
  provider reference through a principal/dispatch/fence-bound idempotent native
  send report before any confirmation lease can exist
- **AND** Gmail cannot initiate a generic send or convert SMTP/cache/list/search
  evidence into a confirmation lease
- **AND** transient content remains outside generic persistence and raw-error
  paths

#### Scenario: Maintenance cannot initiate delivery

- **WHEN** Messenger correspondence maintenance runs
- **THEN** it may expire a confirmation deadline, prune eligible metadata, or
  service a bounded reconciliation lease
- **AND** it does not compose content, initiate a new email, retry an unknown
  send, or invoke an LLM prompt

#### Scenario: Relationship cannot call a Messenger raw-ledger tool

- **WHEN** a Relationship runtime, MCP/API/on-demand/interactive client, or LLM
  session requests correspondence data
- **THEN** Messenger exposes no MCP/API/on-demand/interactive aggregate path or
  method that lists private ledger rows
- **AND** the only supported consumer path is the bounded security-definer
  aggregate from the fixed Relationship deterministic scheduled job authorized by
  the database-security contract

#### Scenario: Only the authenticated Switchboard broker can record Gmail evidence

- **WHEN** a Gmail connector requests a native-send dispatch, reports its direct
  send result, requests a confirmation lease, or reports a provider confirmation
- **THEN** Messenger accepts the request only through an authenticated
  Switchboard-broker route context bound to the connector/account principal
- **AND** it atomically consumes the relevant dispatch or lease fence and rejects
  direct connector access, a caller-supplied principal, an expired or consumed
  fence, or a provider/account/reference mismatch
- **AND** it exposes no generic correspondence writer or raw ledger reader

#### Scenario: Authenticated ingress conditions close coverage atomically

- **WHEN** the authenticated Switchboard ingress/control route detects invalid
  age, checkpoint gap, re-authentication, rebinding, or principal mismatch
- **THEN** Messenger accepts only the fixed fenced metadata operation
  `messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
  with its checked categorical reason and returns `closed` or `already_closed`
- **AND** it atomically blocks the matching coverage epoch so no prior watermark
  remains fresh
- **AND** it accepts no peer, raw source ID, provider time, content, free text,
  or caller-supplied identity through that operation
