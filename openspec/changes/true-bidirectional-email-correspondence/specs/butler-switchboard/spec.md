## ADDED Requirements

### Requirement: Authenticated correspondence native-send and confirmation broker

Switchboard SHALL be the only connector-facing broker for provider-native
correspondence send and provider-Sent confirmation.  It SHALL expose only
`correspondence.send.dispatch`, `correspondence.send.report`,
`correspondence.confirmation.poll`, and `correspondence.confirmation.report` to
an authenticated connector principal.  The transport derives that principal's
connector type and canonical endpoint/account from its scope; the broker SHALL
not trust caller-supplied provider, connector, endpoint, or account fields.

For a valid principal, Switchboard SHALL route a short-lived, one-time opaque
native-send dispatch, a confirmation lease, or a categorical report to Messenger
through authenticated internal routing.  The dispatch SHALL include an opaque
dispatch ID, fence, and deterministic send-report ID bound to that principal and
one private admitted intent.  `send.report` SHALL carry only that dispatch
material, a categorical direct-send outcome, the direct exact reference only when
one exists, and an allowed timestamp.  Messenger SHALL atomically consume it
before Switchboard can request a confirmation lease.  A confirmation lease SHALL
include an opaque lease ID, fence, and deterministic report ID bound to that
principal and one recorded accepted exact reference.  Switchboard SHALL not
consume a dispatch or lease separately from Messenger's outcome transaction.  It
SHALL not grant the connector Messenger access, enumerate Messenger rows, or turn
the broker into a general cross-butler data plane.  Broker errors, route errors,
logs, and metrics SHALL be categorical and content-free; the correspondence path
SHALL not persist raw exceptions through the generic routing-log error field.

#### Scenario: A valid scoped connector receives one opaque lease

- **WHEN** an enabled Gmail connector with a valid account-scoped transport
  credential calls `correspondence.confirmation.poll`
- **THEN** Switchboard derives its principal from the credential and requests at
  most one Messenger lease bound to that principal
- **AND** the connector receives only the opaque lease ID/fence/report ID, exact
  already-known reference, and allowed timestamps
- **AND** it receives no raw Messenger row, provider payload, peer address, or
  correspondence content

#### Scenario: A native send is only Messenger-initiated

- **WHEN** a valid Gmail principal receives
  `correspondence.send.dispatch`
- **THEN** Switchboard has authenticated a one-time Messenger-issued dispatch
  bound to that principal and a private admitted intent
- **AND** it forwards transient content only through the dedicated route and
  never generic notification, inbox, audit, trace, or routing-log persistence
- **AND** Gmail returns an exact stable provider reference directly from the
  documented native send response or a bounded categorical result
- **AND** a connector cannot call this tool to initiate arbitrary egress

#### Scenario: A native send report precedes confirmation leasing

- **WHEN** the authenticated Gmail connector finishes a one-time native send
  dispatch
- **THEN** it returns only its dispatch ID/fence/deterministic send-report ID,
  categorical direct-send outcome, direct exact reference when present, and
  allowed timestamp through `correspondence.send.report`
- **AND** Switchboard derives the principal again and routes that result through
  the scrubbed internal path without consuming it
- **AND** Messenger atomically validates/consumes the dispatch and records the
  direct native result before any confirmation lease is available
- **AND** an identical report returns the prior categorical result while a
  stale, conflicting, cross-account, or altered-fence report fails closed

#### Scenario: Cross-account or unauthenticated broker calls fail closed

- **WHEN** a caller is missing authentication, has a revoked/wrong scope, or
  supplies a provider/account that differs from its verified principal
- **THEN** Switchboard rejects the call before invoking Messenger
- **AND** it does not disclose whether a lease or correspondence record exists
- **AND** it returns and records only a bounded categorical failure class

#### Scenario: Messenger atomically fences and records a confirmation report

- **WHEN** an authenticated Gmail connector reports a provider confirmation
- **THEN** Switchboard forwards only its derived principal, opaque lease
  ID/fence/deterministic report ID, categorical outcome, and observed timestamp
  through the scrubbed internal route
- **AND** Messenger atomically validates and consumes the lease and records the
  outcome using the principal, lease ID/fence, and report ID as its idempotency
  fence
- **AND** an identical retried report returns the already-recorded categorical
  result without another state transition
- **AND** a conflicting, stale, cross-account, altered-reference, or
  altered-fence report fails closed without a Messenger state transition
- **AND** any internal route failure is scrubbed before it reaches
  `switchboard.routing_log` or an operator-facing result

### Requirement: Authenticated ingress epochs and pre-route candidate privacy

For an enabled email account, Switchboard SHALL derive connector/provider/endpoint
identity from the authenticated transport and verify it against a canonical
account binding before it permits correspondence-qualified inbound evidence.  It
SHALL create/continue a metadata-only ingress coverage epoch only after a
committed authenticated checkpoint; historical/pre-enforcement envelopes and
caller-supplied endpoint identity SHALL not qualify.  Reset, gap,
re-authentication, rebinding, or mismatch SHALL atomically invoke
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
with the corresponding fixed category (`checkpoint_gap`, `reauth`, `rebind`, or
`principal_mismatch`) in the authenticated ingress/control transaction that
detects it.

At the credential-bound provider-adapter seam, Switchboard SHALL accept only a
strictly typed transient provider-age assertion derived from the provider's
documented event-time field, not a generic caller/raw-envelope timestamp.  It
SHALL validate the assertion against the derived principal, server clock, and
owner-approved maximum delay/future-skew budget, then discard it.  It SHALL not
persist or expose the assertion, or use it as the 180-day timestamp.  Missing,
malformed, future-skewed, or over-age assertions SHALL create no qualified
observation and SHALL atomically invoke that same function with `age_invalid`
before the generic event path returns.

For a qualifying accepted ingress decision, Switchboard SHALL derive a non-
reversible account-scoped opaque deduplication token from the authenticated
provider's immutable source-event key using a secret outside the database.  In
the same transaction it SHALL invoke only the fixed Messenger-owned projection
function `messenger.record_qualified_email_ingress(text, text, text, uuid,
timestamptz, bytea)`, with only its derived provider/account/peer/epoch and
once-captured server receipt time plus opaque deduplication token.  It SHALL have
only the narrow `USAGE ON SCHEMA messenger` plus exact function execute grant,
and no direct Messenger table or other object grant.  A duplicate/retry SHALL
return only the existing `recorded` or `duplicate` categorical projection result
and SHALL NOT create a new observation, replace receipt time, or advance coverage.
If that projection cannot commit, the correspondence evidence path remains
unavailable rather than falling back to a best-effort post-ingest write.  The
only other Messenger SQL write callable by Switchboard on this qualified-ingress
path is
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`.
It receives only derived provider/account/epoch, one of the five checked
categories, and once-captured server closure time; returns only `closed` or
`already_closed`; has no peer, raw source ID, provider time, content, free text,
or caller-principal input; and is idempotent.  Both functions use only `USAGE ON
SCHEMA messenger` and their exact hardened function-execute grants to
`butler_switchboard_rw`; Switchboard has no other Messenger object access.

Switchboard SHALL not discover, infer, mutate, or roll the complete account
universe from connector traffic, mailbox data, provider listings, or historical
ingestion.  That private configuration inventory is owner-approved elsewhere;
the ingress broker only reports categorical coverage state for its already-bound
canonical account.

Before invoking a Messenger email route, Switchboard SHALL derive an opaque,
non-caller-spoofable correspondence-candidate context from validated native
command/approval lineage.  It contains no email content, recipient, header,
credential, or full envelope and persists through validation, network, timeout,
and no-admission errors.  Candidate failures SHALL be categorized before any
generic routing, notification, inbox, audit, metric, or caller result.

#### Scenario: Historical or unauthenticated ingress is excluded

- **WHEN** a Gmail event lacks an account-bound authenticated ingress epoch or
  predates enforcement
- **THEN** Switchboard does not emit qualified correspondence metadata from it
- **AND** it cannot later be used as same-account evidence

#### Scenario: Delayed or duplicate ingress cannot become fresh evidence

- **WHEN** a provider-age assertion is absent, malformed, delayed beyond budget,
  or future-skewed
- **THEN** Switchboard emits no qualified correspondence observation and retains
  no provider time
- **AND** it atomically invokes
  `messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
  with `age_invalid`, so no previous coverage watermark remains fresh
- **WHEN** a qualified source event is retried
- **THEN** the fixed projection returns its original categorical result without
  changing server `received_at` or coverage

#### Scenario: A route fails before Messenger admission

- **WHEN** a trusted correspondence candidate encounters validation, network,
  timeout, or Messenger-admission failure
- **THEN** Switchboard records and returns only a bounded categorical outcome
- **AND** it creates no content-bearing generic notification, inbox, routing-log,
  audit, trace, or metric record for that candidate
