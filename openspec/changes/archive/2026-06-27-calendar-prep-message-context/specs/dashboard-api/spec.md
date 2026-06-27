## ADDED Requirements

### Requirement: Prep Rail Surfaces Merged Message Context

The meeting-prep rail read `GET /api/calendar/workspace/prep/{event_id}` SHALL
surface a populated `message_context` for an attendee when an email/message-owning
butler has contributed one. The endpoint MUST union the relationship-sourced
envelope (attendee + notes + last-met) with the email-sourced envelope (message
context) by attendee `entity_id`, reading both exclusively from the precomputed
`calendar.v_prep_contributions` cached view. It MUST NOT issue a direct cross-schema
query and MUST NOT spawn an LLM session at request time, and MUST continue to fail
open to a structured empty payload when no contribution exists.

#### Scenario: Message context merges into the relationship attendee
- **WHEN** both the relationship butler and an email-owning butler (messenger/travel) have written a prep envelope for the same event with the same attendee `entity_id`
- **THEN** the response carries a single merged attendee whose `notes`/`last_met` come from the relationship envelope and whose `message_context` carries the email envelope's recent threads, and `source_butlers` lists both contributing schemas

#### Scenario: Message context surfaced without request-time cross-butler read
- **WHEN** the prep rail read serves an event with email message context
- **THEN** the `message_context` is read only from `calendar.v_prep_contributions` (the precomputed cached view), with no on-demand `SELECT` against any sibling schema and no LLM/Gmail session opened while serving the request
