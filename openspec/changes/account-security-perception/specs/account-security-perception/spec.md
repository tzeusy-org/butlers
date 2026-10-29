## ADDED Requirements

### Requirement: Deterministic Account-Security Classification

The system SHALL classify an email as an account-security alert only when its sender is an exact address on a first-party identity-provider allowlist and its subject matches a fixed phrase table, yielding one kind of `new_sign_in`, `password_changed`, `mfa_changed`, `recovery_changed` or `deletion_scheduled`. Classification MUST use only the sender, the subject and the `Authentication-Results` header, MUST NOT use an LLM, and MUST NOT read, store, log or emit a message body, code or link.

Classification SHALL remain deterministic service-plane work under RFC 0038 D1/D2. The security label SHALL NOT authenticate a sender. An `authenticated` sender-verification verdict authenticates the provider sender only; neither that verdict nor the security label SHALL establish owner origin, clear exposure or taint, enlarge an origin ceiling or grant capabilities. Only a channel and sender authorized by RFC 0017, or existing dashboard owner authentication, SHALL establish owner origin under RFC 0038 D5.

ID: REQ-account-security-perception-001
Source: proposal.md; design.md; RFC 0038 D1/D2/D5/D6/D18; RFC 0017 owner-channel authorization
Scope: v1-mandatory

#### Scenario: A first-party alert is classified

- **WHEN** an email from the allowlisted Google alert address has the subject "Security alert"
- **THEN** the classifier returns provider `google` and kind `new_sign_in`

#### Scenario: A lookalike sender is not classified

- **WHEN** an email has a subject "Security alert" and a sender that is a subdomain or suffix-extension of an allowlisted domain
- **THEN** the classifier returns no classification

#### Scenario: A failed authentication result rejects the alert

- **WHEN** the `Authentication-Results` header reports `dmarc=fail` for the message
- **THEN** the classifier returns no classification

#### Scenario: Missing authentication results are unverified

- **WHEN** an allowlisted alert has no `Authentication-Results` header
- **THEN** the classification carries `sender_verification` of `unverified`

### Requirement: Typed Security Event

The Switchboard ingest tool SHALL publish one fixed `switchboard.security_event` per ingested classified alert, independent of the global ingestion policy decision, with a payload limited to kind, provider, provider domain, sender verification, source request id, external event id and observed time. A repeat of the same provider, kind and external event id MUST NOT publish a second event. Only an `authenticated` event MAY interrupt the owner; this is a necessary condition, not authorization to notify or bypass admission, exposure or tool approval policy. Connector-scope `block` precedence SHALL remain unchanged.

Fixed extraction and publication SHALL NOT require model judgment or goal admission. Any model judgment about an alert, including owner-facing composition, SHALL instead run in an admitted goal under RFC 0038 D1/D2/D5/D6/D10/D18. An event consumer SHALL be explicitly declared before subscribing; publication alone SHALL create no subscriber or grant. Admission SHALL derive origin and cause from server-held context, preserve applicable assertion lineage and exposure, enforce the origin ceiling and causal-chain limit, and reserve the finite goal budget. Effect-caused admission SHALL carry `caused_by_goal` and `caused_by_effect`, inherit the causing goal's exposure and intersect the declared subscription set with the causing goal's origin ceiling. Domain-published input without goal lineage SHALL start exposed unless stamped as derived only from untainted owner-authority records; missing or unreadable stamps SHALL count as exposed. The label or sender verdict SHALL NOT relax these rules. These admission rules SHALL NOT add message content or authority-bearing fields to the fixed event payload.

ID: REQ-account-security-perception-002
Source: proposal.md; design.md; RFC 0038 D1/D2/D5/D6/D10/D18
Scope: v1-mandatory

#### Scenario: A skip decision does not hide the alert

- **WHEN** a promoted global rule resolves to skip for an allowlisted sender and the message is a classified alert
- **THEN** the ingest tool still publishes the event

#### Scenario: The payload carries no message content

- **WHEN** an event is published
- **THEN** its payload contains no subject text, body, code, link or mailbox address

### Requirement: Was-This-You Answer Door

The system SHALL record the owner's answer per event id. A `no` answer MUST open or reuse exactly one fleet case for the event and contribute one evidence row holding the provider's static recovery door. A `yes` answer MUST NOT open or close a case.

Answer recording SHALL remain deterministic and SHALL use the existing owner-verification contract; a security label or authenticated provider sender SHALL NOT authenticate the owner answering. This requirement SHALL NOT create an interactive notification consumer or complete the deferred prompt or manifesto ownership decision.

ID: REQ-account-security-perception-003
Source: proposal.md; design.md; RFC 0038 D1/D2/D5/D18
Scope: v1-mandatory

#### Scenario: A no answer opens one case

- **WHEN** the owner answers `no` twice for one event
- **THEN** exactly one fleet case and one evidence row exist for that event
