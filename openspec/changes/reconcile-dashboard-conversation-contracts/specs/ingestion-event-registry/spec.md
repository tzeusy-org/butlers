## MODIFIED Requirements

### Requirement: Ingestion Event Table

The `public.ingestion_events` table SHALL be the canonical first-class record
of every event that enters the butler ecosystem through a connector or direct
owner-dashboard ingress. One row SHALL exist per canonical ingestion event
after deduplication. The UUID7 primary key SHALL be the `request_id` returned to
connectors or direct internal callers and propagated to all downstream sessions
and traces. Connector-specific `filtered_events` joins and status semantics
SHALL remain scoped to connector records and SHALL NOT be inferred for dashboard
events.

ID: REQ-ingestion-event-registry-001
Source: RFC 0003 § ingest.v1 Envelope Format; dashboard-conversations § Dashboard Ingestion Envelope Construction; design.md Decision 1a
Scope: v1-mandatory

#### Scenario: Row created on new ingest accept

- **WHEN** the Switchboard accepts an ingest envelope and no existing row
  matches the computed dedupe key
- **THEN** it SHALL insert a new `public.ingestion_events` row inside the same
  advisory-lock transaction used for deduplication with fields: `id` (UUID7),
  `received_at`, `source_channel`, `source_provider`,
  `source_endpoint_identity`, `source_sender_identity`,
  `source_sender_display_name`, `source_thread_identity`,
  `external_event_id`, `dedupe_key`, `dedupe_strategy`, `ingestion_tier`,
  `policy_tier`, `triage_decision`, and `triage_target`
- **AND** `source_sender_display_name` SHALL be the raw sender display name
  from `sender.display_name`, or `NULL` when the envelope carried none; unlike
  `source_sender_identity` it SHALL remain verbatim for identity enrichment
- **AND** the row `id` SHALL be returned as `request_id` in
  `IngestAcceptedResponse`

#### Scenario: Duplicate submission returns existing row ID

- **WHEN** the Switchboard receives an envelope whose computed dedupe key
  matches an existing `public.ingestion_events` row
- **THEN** it SHALL insert no new row and return the existing row `id` as
  `request_id` with `duplicate=true`

#### Scenario: UUID7 is time-ordered

- **WHEN** two ingestion events are accepted in sequence
- **THEN** the second event `id` SHALL be lexicographically greater than the
  first
- **AND** UUID7 ordering SHALL support recency queries without a separate
  `received_at` index

#### Scenario: Dashboard event enters the unified registry

- **WHEN** the dashboard API submits a direct `dashboard` / `internal`
  `ingest.v1` envelope
- **THEN** the resulting `public.ingestion_events` row SHALL participate in the
  same dedupe and request-ID lineage as connector-originated events without
  being described as connector provenance

#### Scenario: Dashboard row retains direct-ingress semantics

- **WHEN** a caller reads a dashboard-originated ingestion event
- **THEN** it SHALL NOT infer a `connectors.filtered_events` row or
  connector-specific status semantics merely because the event appears in the
  unified registry
