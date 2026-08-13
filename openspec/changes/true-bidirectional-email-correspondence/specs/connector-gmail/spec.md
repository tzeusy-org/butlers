## ADDED Requirements

### Requirement: Exact-reference Gmail Sent confirmation for correspondence

The Gmail connector SHALL participate in email correspondence confirmation only
through an explicitly enabled provider capability.  It SHALL receive a bounded
lease from authenticated Switchboard correspondence tools, containing one
already-known canonical account reference and one exact provider message
reference, then return only a typed categorical confirmation outcome and
observed timestamps through that same broker.  Gmail SHALL not contact a
Messenger endpoint, select Messenger tables, or claim a provider/account
identity in tool arguments; Switchboard derives the immutable connector/account
principal from the connector transport credential.  It SHALL not use the in-memory Sent-ID cache,
enumerate the Sent mailbox, list/search/history-traverse mail, process content,
backfill historical mail, or mutate provider data for correspondence
confirmation.

It SHALL perform provider-native correspondence send only when authenticated
Switchboard forwards a one-time Messenger-issued opaque dispatch for its own
canonical account.  The native send returns the exact stable provider reference
directly from the documented send response; the connector SHALL not create that
reference from SMTP, RFC message IDs, cache/list/search/history results, or
post-hoc correlation.  It SHALL not expose the native-send path as generic
connector egress, and its transient content SHALL never enter generic
Switchboard persistence, logs, audit, traces, inboxes, or retry data.

After a native send, Gmail SHALL return its direct categorical result only through
`correspondence.send.report`, bound to the broker-derived principal and the
one-time dispatch ID/fence/deterministic send-report ID.  It SHALL pass an exact
reference only when returned directly by the documented native send response.
Messenger records that result atomically before Gmail can receive a confirmation
lease.  An uncertain send may retry only the same report; Gmail SHALL NOT resend
the provider operation absent its documented idempotency contract.

For correspondence-qualified inbound ingress, Gmail MAY derive a strictly typed
transient provider-age assertion from the documented Gmail provider event-time
field at its credential-bound adapter seam.  It SHALL send that assertion only as
authenticated ingress transport metadata for Switchboard's budget validation and
SHALL NOT copy the assertion or an additional provider-time value into the
correspondence projection, correspondence-specific logs, metrics, traces, or
aggregate.  It is never the 180-day evidence timestamp.  Existing generic Gmail
ingestion envelope/persistence behavior remains governed by its current contract
and is neither correspondence evidence nor changed by this delta.

When that assertion is absent, malformed, delayed beyond budget, or future-skewed,
Gmail SHALL receive only the Switchboard categorical rejection outcome.  The
authenticated Switchboard path, not Gmail, invokes
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
with `age_invalid` before the generic ingress path returns; Gmail receives no
Messenger endpoint or direct closure capability.

#### Scenario: A known Gmail message is confirmed through an exact reference

- **WHEN** Gmail holds an enabled capability and leases an exact provider
  message reference for its own canonical account
- **THEN** it uses only the provider operation documented to prove that exact
  message is in Sent
- **AND** it returns only the approved typed metadata outcome through Switchboard
- **AND** it does not persist the provider response or message payload

#### Scenario: Gmail cannot manufacture native correspondence egress

- **WHEN** Gmail receives a native-send request without a valid one-time
  Messenger/Switchboard dispatch binding for its authenticated account
- **THEN** it rejects the request categorically before calling Gmail
- **AND** it cannot use SMTP, a cache value, or a caller-supplied reference to
  create a confirmation lease

#### Scenario: Gmail returns a direct native result through the fenced broker

- **WHEN** Gmail completes a Messenger-issued native send dispatch
- **THEN** it calls `correspondence.send.report` with only the dispatch
  ID/fence/deterministic send-report ID, direct categorical outcome, direct
  reference when present, and allowed timestamp
- **AND** it cannot call `correspondence.confirmation.poll` for that intent until
  Messenger has atomically recorded an accepted direct reference
- **AND** an identical report is safe to retry while a stale or altered report
  fails closed without another Gmail send

#### Scenario: Gmail uses only its scoped broker principal

- **WHEN** Gmail polls or reports native-send or correspondence-confirmation
  work through Switchboard
- **THEN** it authenticates with a secret-authority managed credential scoped
  to the Gmail connector and its canonical endpoint/account
- **AND** Switchboard ignores or rejects a caller-supplied provider/account
  mismatch and routes only a broker-validated one-time lease/result to Messenger
- **AND** Gmail cannot invoke Messenger directly or access a Messenger schema

#### Scenario: Broad Sent priority cache is not correspondence evidence

- **WHEN** Gmail refreshes the existing bounded Sent Message-ID cache for
  `reply_to_outbound` priority assignment
- **THEN** that cache remains independent from the correspondence ledger
- **AND** no cache value can mark an outbound attempt `confirmed`

#### Scenario: Disabled or insufficient Gmail capability stays indeterminate

- **WHEN** Gmail lacks an enabled account capability, the exact reference, the
  required documented scope, or a fresh confirmation result
- **THEN** it reports no positive confirmation through Switchboard
- **AND** Messenger keeps or transitions the attempt to `unknown`

#### Scenario: Gmail does not persist provider time for qualified ingress

- **WHEN** Gmail derives the allowed transient provider-age assertion for an
  authenticated ingress event
- **THEN** it sends only the typed assertion on the scoped transport boundary
- **AND** it does not copy the assertion or an additional source provider time to
  a correspondence projection, correspondence-specific persistence, log, trace,
  metric, or aggregate
- **AND** existing generic ingestion envelope/persistence behavior remains
  outside this correspondence capability and cannot be used as evidence
