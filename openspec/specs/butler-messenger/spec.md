# Messenger Staffer Role

## Purpose

Messenger is the outbound channel execution plane. It is a staffer and does
not classify messages or own domain logic.

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
