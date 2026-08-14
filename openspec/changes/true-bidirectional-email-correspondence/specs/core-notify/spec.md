## ADDED Requirements

### Requirement: Email correspondence pre-route candidate privacy

Before invoking a route, Switchboard SHALL derive a server-only opaque
correspondence-candidate context from a validated native email command and its
trusted request/approval lineage.  The candidate contains no recipient,
subject, message, header, credential, or full envelope; a caller cannot set or
spoof it.  It SHALL persist through validation, network, timeout, and
no-admission outcomes.  In candidate mode Switchboard SHALL not create a new
content-bearing `switchboard.notifications` or outbound
`switchboard.message_inbox` mirror, and it SHALL not persist recipient,
subject, message, headers, raw provider output, or raw error text in a generic
audit/attention record.  It SHALL convert errors to bounded categories before
any generic log, metric, route result, or persistence surface.

#### Scenario: Routed email avoids generic content mirrors

- **WHEN** trusted routing derives a correspondence candidate for a valid routed
  email command
- **THEN** Switchboard records at most an opaque correspondence intent reference
  after admission and a categorical delivery outcome needed by the control flow
- **AND** it does not insert the email message or recipient into
  `switchboard.notifications` or `switchboard.message_inbox`

#### Scenario: Non-email notification behavior remains explicit

- **WHEN** a routed notification has no trusted correspondence candidate
- **THEN** its existing notification persistence behavior remains governed by
  the normal notify contract
- **AND** an untrusted caller cannot request a correspondence candidate to
  suppress its own audit trail

#### Scenario: A pre- or post-admission correspondence failure remains content-free

- **WHEN** a trusted correspondence candidate fails validation, network route,
  Messenger admission, provider dispatch, or a timeout
- **THEN** the notify/control-plane result uses a bounded categorical failure
  class and an opaque reference when one exists
- **AND** it does not persist or return a raw provider exception, subject,
  message, recipient, header, or payload
