## MODIFIED Requirements

### Requirement: Ingestion Event Table

The `public.ingestion_events` table SHALL be the canonical first-class record
of every event that enters the butler ecosystem through a connector or direct
owner-dashboard ingress. One row SHALL exist per canonical ingestion event
after deduplication. The UUID7 primary key SHALL be the `request_id` returned to
connectors or direct internal callers. Before cutover it SHALL propagate to
all downstream sessions and traces. After the RFC 0038 cutover, reasoning
ingress SHALL propagate it to the admitted goal and that goal's lead/child
invocations and traces, not a new target-butler session; stored pre-cutover
session lineage remains readable. Deterministic service-plane ingestion SHALL
remain goal-free under D1. Connector-specific `filtered_events` joins and status
semantics SHALL remain scoped to connector records and SHALL NOT be inferred
for dashboard events.

ID: REQ-ingestion-event-registry-001
Source: RFC 0003 § ingest.v1 Envelope Format; dashboard-conversations § Dashboard Ingestion Envelope Construction; design.md Decision 1a and Goal-admission conversion; RFC 0038 D1, D2, D19
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
  `policy_tier`, `triage_decision`, `triage_target`, and `sender_authentication`
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
- **AND** after cutover, duplicate dashboard reasoning ingress SHALL return
  the goal already admitted from that envelope rather than create another goal

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

#### Scenario: Dashboard ingress and routed-session provenance remain distinct

**Before the RFC 0038 cutover (legacy contract):**

- **WHEN** the dashboard API submits a direct `dashboard` / `internal`
  `ingest.v1` envelope and the Switchboard routes it to a target butler
- **THEN** the canonical ingestion-event row SHALL retain
  `source_channel = "dashboard"` and `source_provider = "internal"`
- **AND** the routed target-butler session SHALL record
  `trigger_source = "route"`; direct dashboard ingress provenance SHALL NOT
  be conflated with the downstream route trigger boundary

**After the RFC 0038 cutover (successor contract):**

- **WHEN** the dashboard API submits an authenticated immutable `dashboard` /
  `internal` reasoning envelope and Switchboard admits its owner goal
- **THEN** the canonical ingestion-event row SHALL retain
  `source_channel = "dashboard"` and `source_provider = "internal"`
- **AND** its deduplicated `request_id` SHALL link to the admitted goal and its
  lead/child invocations through the goal ledger, never a new routed-session
  `trigger_source`
- **AND** pre-cutover routed sessions SHALL remain readable with their stored
  trigger and request lineage; dashboard ingress SHALL NOT acquire connector
  filtered-event or status semantics.
