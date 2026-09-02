## REMOVED Requirements

### Requirement: Dashboard Ingestion Envelope Construction

Superseded by `Stable Dashboard Ingestion Envelope Construction` because the
public envelope no longer overloads `external_thread_id`.

## ADDED Requirements

### Requirement: Stable Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through the standard Switchboard ingestion pipeline, submitted to the Switchboard's `ingest` MCP tool. RFC 0003 section "ingest.v1 Envelope Format" defines `dashboard` / `internal` as direct owner-dashboard ingress: the dashboard API, rather than a connector startup probe, SHALL assign `dashboard:web:{conversation_id}` as the endpoint identity.

#### Scenario: Envelope structure for dashboard messages

- **WHEN** a dashboard message is submitted for ingestion
- **THEN** the envelope SHALL include `event.external_conversation_id` as `"dashboard:{conversation_id}"` and `event.reply_target_ref` as `"{conversation_id}"`
- **AND** it SHALL preserve the existing schema version, source, event ID, observed timestamp, sender, payload, policy tier, ingestion tier, and optional pinned target fields

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `dashboard` channel SHALL NOT be subject to discretion evaluation

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a per-butler dashboard conversation targets a routable domain butler
- **THEN** `control.pinned_target` SHALL name that butler

#### Scenario: Switchboard-addressed conversations are unpinned until routed

- **WHEN** a Switchboard-addressed conversation has no routed butler
- **THEN** it SHALL proceed through ordinary classification without a pinned target

#### Scenario: Sticky follow-up pinning for classification-routed conversations

- **WHEN** a follow-up has an existing `routed_butler`
- **THEN** `control.pinned_target` SHALL use that butler and bypass classification

#### Scenario: Optional page context on dashboard messages

- **WHEN** the client supplies page context
- **THEN** `payload.raw.page_context` SHALL preserve it unchanged
- **AND** the key SHALL be absent when no page context was supplied
