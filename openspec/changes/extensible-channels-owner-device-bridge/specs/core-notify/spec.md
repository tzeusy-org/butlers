## ADDED Requirements

### Requirement: SMS Remains Unsupported Pending Separate Effect Authority
This change SHALL preserve the existing deliverable channel set of Telegram and email and the
existing unsupported result for `channel="sms"`. An inbound source catalog entry, owner-device
credential, connector instance, webhook, or successful reachability check MUST NOT make SMS
deliverable. SMS activation requires a later exact owner-approved specification and a usable
Messenger adapter with approval and provider-ambiguity handling.

ID: REQ-core-notify-032
Source: RFC 0033 §Outbound SMS exclusion and future activation gate (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Existing unsupported result is preserved
- **WHEN** `notify(channel="sms", message="Hello")` is called under this change
- **THEN** the tool SHALL return the existing unsupported-channel error
- **AND** it SHALL not resolve a recipient, park an approval, invoke Messenger, call a provider, or record a successful delivery

#### Scenario: Inbound authority cannot enable SMS egress
- **WHEN** an enabled inbound SMS source pair or active owner-device connector exists in a future rollout
- **THEN** `notify(channel="sms")` SHALL remain unsupported
- **AND** no source-catalog, connector-registry, heartbeat, checkpoint, or inbound credential field SHALL be consulted as outbound adapter authority

#### Scenario: Future activation is a separate approved change
- **WHEN** an implementation proposes to add SMS to the deliverable set
- **THEN** its governing approved artifact MUST define the adapter registration, credential and reachability checks, recipient resolution, per-message approval interception, defense-in-depth Messenger gate, immutable attempt identity, provider receipt/reconciliation boundary, partial-effect result, and rollback
- **AND** the implementation MUST compose with the applicable approved RFC 0023 policy rather than duplicating or bypassing its delivery-intent state machine

#### Scenario: Unknown provider outcome never becomes a generic retry
- **WHEN** a future SMS adapter may have handed a message to its provider but receives no conclusive result
- **THEN** the attempt MUST be classified as ambiguous unless provider evidence proves duplicate-safe reconciliation or repetition for the same immutable attempt identity
- **AND** a generic retry, restart, timeout handler, or new attempt identity MUST NOT send the message again automatically
