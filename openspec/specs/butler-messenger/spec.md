# Messenger Staffer Role

## Purpose

Messenger is the outbound channel execution plane. It is a staffer and does
not classify messages or own domain logic.

The approval-recovery protocol is owned by [approval-delivery-intent-recovery](../approval-delivery-intent-recovery/spec.md); this spec binds its integration seam to that canonical contract.

## Requirements

### Requirement: Messenger Butler Identity and Runtime
Messenger SHALL operate as a delivery-only staffer on port 41104.

#### Scenario: Identity and module profile
- **WHEN** Messenger starts
- **THEN** it runs with `type = "staffer"` and the calendar, Telegram, email,
  WhatsApp, and approvals modules
- **AND** it does not load a delivery-tracking module

#### Scenario: Excluded from inbound routing
- **WHEN** Switchboard classifies an incoming user message
- **THEN** Messenger SHALL NOT be a routing candidate

### Requirement: Messenger Channel Ownership
Messenger SHALL own the external user-channel send and reply tools.

#### Scenario: Direct approved adapter egress
- **WHEN** Messenger receives an approved `notify.v1` delivery intent
- **THEN** it executes the selected owned channel adapter directly
- **AND** it preserves origin and request-context lineage without creating a
  legacy delivery-tracking record

### Requirement: Approval-Gated Delivery
Sensitive channel sends SHALL remain approval-gated before execution.

#### Scenario: Gated channel tools
- **WHEN** Messenger starts with approvals enabled
- **THEN** its configured Telegram, email, and WhatsApp send/reply tools require
  approval before production use

### Requirement: Truthful Delivery Observability
Messenger SHALL not expose tracking, retry, dead-letter, or health endpoints
that do not observe the live adapter path.

#### Scenario: Retired health surface
- **WHEN** a client requests a former Messenger tracking or health endpoint
- **THEN** the endpoint is absent rather than returning fabricated empty data

### Requirement: Trusted wired approval egress handoff reconciliation
Messenger SHALL durably own the actual-provider handoff/reconciliation record for a recovery-keyed approval presentation, keyed by the trusted tuple of authenticated issuer, owning schema, immutable presentation key, and approved presentation mode. It SHALL accept the subject-to-presentation relation only from Switchboard's authenticated source-schema attestation, not infer it with a peer pool or cross-schema read grant. It SHALL not treat a bare `notify.v1` key or caller-provided origin as authority, and it SHALL not restore an unwired generic delivery-tracking module or endpoint.

ID: REQ-butler-messenger-001
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: First approval handoff records the real boundary
- **WHEN** Messenger accepts a valid trusted recovery `approval_request` for a new presentation key
- **THEN** it persists the trusted tuple and a pre-provider handoff record before invoking its owned channel adapter
- **AND** any provider receipt/reference retained is bounded, opaque, non-secret, and unavailable to unrelated tracking surfaces

#### Scenario: Same-presentation recovery reconciles instead of resending
- **WHEN** Messenger receives the same trusted presentation key after a source restart or lost response
- **THEN** it returns the stored confirmed result, a proof-bearing safe-retry result, or an ambiguous result for that tuple
- **AND** it does not invoke the provider again unless its adapter contract proves the same-presentation retry cannot duplicate delivery

#### Scenario: Mismatched recovery principal never reaches the ledger
- **WHEN** a recovery request has an action/cohort-subject schema prefix, claimed origin, or mode that conflicts with Switchboard's trusted issuer/schema context
- **THEN** Messenger rejects it before inserting/updating a handoff record or invoking an adapter
- **AND** the rejection includes only a closed safe class, not the key, callback, recipient, or message

### Requirement: Honest provider uncertainty
Messenger SHALL classify a provider result as ambiguous whenever a provider-start marker exists and neither a receipt nor a provider-supported idempotency/reconciliation result proves completion or safe absence.

ID: REQ-butler-messenger-002
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Crash after provider start is not a duplicate send
- **WHEN** Messenger restarts after recording provider start for an approval presentation key but before persisting a final provider outcome
- **THEN** recovery returns an ambiguous classification unless the channel adapter reconciles that same presentation key
- **AND** no default retry path sends another Telegram, email, or WhatsApp approval request

#### Scenario: Provider idempotency proves a retry safe
- **WHEN** an adapter supports a stable idempotency key or receipt lookup and proves that a repeated presentation key has no additional provider effect
- **THEN** Messenger may return `safe_retry` or `confirmed` with the same presentation key
- **AND** it records the normalized proof class without exposing the raw provider response
