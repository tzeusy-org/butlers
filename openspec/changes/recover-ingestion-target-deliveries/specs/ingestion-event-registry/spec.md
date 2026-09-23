## ADDED Requirements

### Requirement: Ingestion target delivery recovery evidence

The ingestion event API SHALL project target-delivery state from durable intents and receipts for non-dashboard domain `route.execute`, while retaining the existing connector filtered-event replay policy and lineage vocabulary. `public.ingestion_events.status = 'ingested'` SHALL remain a coarse source-acceptance or processing status, not universal proof of target acceptance. A failed event SHALL not become `ingested` solely because its recovery endpoint changed a status field.

ID: REQ-ingestion-event-registry-002
Source: [Observed] src/butlers/core/ingestion_events.py:1107; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Waiting and partial delivery remain visible

- **WHEN** one event has an accepted target and another target waiting, ambiguous, or terminally failed
- **THEN** its list and detail APIs SHALL expose each target's safe delivery state and a non-complete aggregate
- **AND** accepted target evidence SHALL remain linked to its existing session lineage without claiming that the session completed

#### Scenario: Recovery pending requires durable work

- **WHEN** a failed event's recovery request reports `replay_pending` or an equivalent waiting state
- **THEN** at least one eligible target intent SHALL already be durably queued
- **AND** the API SHALL NOT clear `failed` or return success merely because the request reached the endpoint

#### Scenario: Ingested source has unresolved target

- **WHEN** a source event has status `ingested` while one classified target has no valid acceptance receipt
- **THEN** its API delivery summary SHALL remain non-complete and SHALL show that target's current state
- **AND** clients SHALL NOT treat `ingested` as a claim that every target accepted the event

#### Scenario: Every public-event recovery branch preserves its plan

- **WHEN** recovery is requested for a `failed`, `ingested`, or `replay_failed` `public.ingestion_events` row
- **THEN** the API SHALL return the original eligible queued target identities or a safe non-success result
- **AND** it SHALL NOT reset `message_inbox`, repeat classification, or create a replacement target plan

#### Scenario: Pending source status is checked against work

- **WHEN** recovery is requested for a `replay_pending` public event
- **THEN** the API SHALL return the existing queued target identity if it exists, or report a conflict/unavailable state when the marker has no durable queued work
- **AND** neither case SHALL queue duplicate work or claim a new delivery succeeded

#### Scenario: Legacy row cannot prove a target delivery

- **WHEN** an older failed row lacks a retained target plan or trustworthy acceptance evidence
- **THEN** the API SHALL keep its failed state and return a content-blind ineligibility reason
- **AND** connector-side `filtered_events` replay eligibility SHALL continue to follow its separate connector policy

#### Scenario: Delivery evidence store is unavailable

- **WHEN** delivery intent state cannot be read for an in-scope event
- **THEN** the API SHALL report delivery evidence unavailable rather than an empty target set or a successful aggregate

#### Scenario: Connector replay remains separately governed

- **WHEN** the requested event belongs to `connectors.filtered_events`
- **THEN** the connector's existing replay-safe policy and drain status transitions SHALL govern it
- **AND** the target-delivery intent state SHALL NOT replace connector replay eligibility
