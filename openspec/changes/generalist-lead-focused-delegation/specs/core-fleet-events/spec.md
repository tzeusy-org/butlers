## MODIFIED Requirements

### Requirement: Switchboard ingestion freshness
A newly accepted Switchboard ingest SHALL publish one bridge-only `ingestion` signal after the `public.ingestion_events` transaction commits. Its metadata SHALL contain only `request_id` and `source_channel`, never the raw ingest payload or a triage field, because ingest triage is removed and accepted envelopes feed goal admission; duplicate submissions SHALL emit no new-ingest signal and publication failure MUST NOT undo acceptance.

ID: REQ-core-fleet-events-009
Source: RFC 0022 #Decision; [Observed] roster/switchboard/tools/ingestion/ingest.py; RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: Switchboard accepts a new ingest
- **WHEN** the ingestion transaction commits a new accepted request
- **THEN** Switchboard publishes `ingestion` with the request identifier and channel only
- **AND** the metadata carries no `triage_decision` or `triage_target`

#### Scenario: Switchboard finds a duplicate
- **WHEN** a submission resolves to an already accepted request
- **THEN** it publishes no new-ingest freshness event
