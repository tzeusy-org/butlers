## ADDED Requirements

### Requirement: Ingestion admission and recovery evidence

The ingestion event API SHALL project each in-scope event's admission state from the durable admission receipt, and each pre-switch event's legacy target evidence read-only, while retaining the existing connector filtered-event replay policy and lineage vocabulary. `public.ingestion_events.status = 'ingested'` SHALL remain a coarse source-acceptance or processing status, not proof of admission, legacy target acceptance or goal completion. A failed event SHALL NOT become `ingested` solely because its recovery endpoint changed a status field.

ID: REQ-ingestion-event-registry-002
Source: [Observed] src/butlers/core/ingestion_events.py:1107; RFC 0038 D5; design.md Decisions 4 and 5
Scope: v1-mandatory

#### Scenario: Admission state is visible

- **WHEN** an event's envelope is buffered, admitted, joined to an existing goal, recorded without a goal, refused, or failed admission
- **THEN** its list and detail APIs SHALL expose that state, the decision identity and any admitted or joined goal identity
- **AND** an admitted goal SHALL link to its goal lineage without claiming the goal completed

#### Scenario: Recovery pending requires durable work

- **WHEN** a recovery request reports `replay_pending` or an equivalent waiting state
- **THEN** durable recovery work SHALL already be queued for that event
- **AND** the API SHALL NOT clear `failed` or return success merely because the request reached the endpoint

#### Scenario: Ingested source is not admission evidence

- **WHEN** a source event has status `ingested` and no admission receipt or legacy target receipt
- **THEN** its API summary SHALL remain non-complete
- **AND** clients SHALL NOT treat `ingested` as a claim that the event was admitted or delivered

#### Scenario: Every public-event recovery branch preserves its decision

- **WHEN** recovery is requested for a `failed`, `ingested`, or `replay_failed` `public.ingestion_events` row
- **THEN** the API SHALL return the original admission receipt, a single new admission when retained evidence proves no decision was made, or a safe non-success result
- **AND** it SHALL NOT reset `message_inbox`, repeat classification, or create a replacement route or goal

#### Scenario: Pending source status is checked against work

- **WHEN** recovery is requested for a `replay_pending` public event
- **THEN** the API SHALL return the existing pending identity if durable work is queued, or report a conflict or unavailable state when the marker has none
- **AND** neither case SHALL queue duplicate work or claim success

#### Scenario: Legacy row stays read-only

- **WHEN** an event was routed before the switch
- **THEN** the API SHALL project its retained legacy target receipts and dispatch outcomes read-only, keeping a partial legacy fan-out non-complete
- **AND** a legacy row lacking a retained envelope or trustworthy evidence SHALL keep its state and return a content-blind ineligibility reason

#### Scenario: Evidence store is unavailable

- **WHEN** admission or legacy evidence cannot be read for an in-scope event
- **THEN** the API SHALL report evidence unavailable rather than an empty or successful summary

#### Scenario: Connector replay remains separately governed

- **WHEN** the requested event belongs to `connectors.filtered_events`
- **THEN** the connector's existing replay-safe policy and drain status transitions SHALL govern it
- **AND** admission state SHALL NOT replace connector replay eligibility
