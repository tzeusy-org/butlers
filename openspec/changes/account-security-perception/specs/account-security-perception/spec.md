## ADDED Requirements

### Requirement: Deterministic Account-Security Classification

The system SHALL classify an email as an account-security alert only when its sender is an exact address on a first-party identity-provider allowlist and its subject matches a fixed phrase table, yielding one kind of `new_sign_in`, `password_changed`, `mfa_changed`, `recovery_changed` or `deletion_scheduled`. Classification MUST use only the sender, the subject and the `Authentication-Results` header, MUST NOT use an LLM, and MUST NOT read, store, log or emit a message body, code or link.

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

The Switchboard ingest tool SHALL publish one `switchboard.security_event` per classified alert, independent of the ingestion policy decision, with a payload limited to kind, provider, provider domain, sender verification, source request id, external event id and observed time. A repeat of the same provider, kind and external event id MUST NOT publish a second event. Only an `authenticated` event MAY interrupt the owner.

#### Scenario: A skip decision does not hide the alert

- **WHEN** a promoted global rule resolves to skip for an allowlisted sender and the message is a classified alert
- **THEN** the ingest tool still publishes the event

#### Scenario: The payload carries no message content

- **WHEN** an event is published
- **THEN** its payload contains no subject text, body, code, link or mailbox address

### Requirement: Was-This-You Answer Door

The system SHALL record the owner's answer per event id. A `no` answer MUST open or reuse exactly one fleet case for the event and contribute one evidence row holding the provider's static recovery door. A `yes` answer MUST NOT open or close a case.

#### Scenario: A no answer opens one case

- **WHEN** the owner answers `no` twice for one event
- **THEN** exactly one fleet case and one evidence row exist for that event
