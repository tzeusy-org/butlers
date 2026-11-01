## MODIFIED Requirements

### Requirement: Ingestion Event Table
The `public.ingestion_events` table is the canonical first-class record of every event that enters the butler ecosystem through a connector or direct internal dashboard ingress. One row exists per canonical ingestion event after deduplication. The UUID7 primary key is the `request_id` returned to connectors or direct internal callers and propagated to all downstream sessions and traces. Connector-specific `filtered_events` joins and status semantics remain limited to connector records and SHALL NOT be inferred for dashboard events.

ID: REQ-ingestion-event-registry-001
Source: RFC 0003 § ingest.v1 Envelope Format; dashboard-conversations § Dashboard Ingestion Envelope Construction; design.md Decision 1a
Scope: v1-mandatory

#### Scenario: Row created on new ingest accept
- **WHEN** the Switchboard accepts an ingest envelope and no existing row matches the computed dedupe key
- **THEN** a new row is inserted into `public.ingestion_events` inside the same advisory-lock transaction used for deduplication, with fields: `id` (UUID7), `received_at` (server timestamp), `source_channel`, `source_provider`, `source_endpoint_identity`, `source_sender_identity`, `source_sender_display_name`, `source_thread_identity`, `external_event_id`, `dedupe_key`, `dedupe_strategy`, `ingestion_tier`, `policy_tier`, `triage_decision`, `triage_target`, and `sender_authentication`
- **AND** `source_sender_display_name` is the raw sender display name from the envelope's `sender.display_name` (e.g. the email `From:` header's display part), or `NULL` when the envelope carried no display name; unlike `source_sender_identity` it is stored verbatim (not normalized) and is used by identity enrichment to name recurring correspondents from the real display name rather than a guess derived from the address local-part
- **AND** the row's `id` is returned as `request_id` in `IngestAcceptedResponse`

#### Scenario: Duplicate submission returns existing row ID
- **WHEN** the Switchboard receives an envelope whose computed dedupe key matches an existing `public.ingestion_events` row
- **THEN** no new row is inserted
- **AND** the existing row's `id` is returned as `request_id` with `duplicate=true`

#### Scenario: UUID7 is time-ordered
- **WHEN** two ingestion events are accepted in sequence
- **THEN** the second event's `id` is lexicographically greater than the first's
- **AND** UUID7 ordering can substitute for a separate `received_at` index for recency queries
