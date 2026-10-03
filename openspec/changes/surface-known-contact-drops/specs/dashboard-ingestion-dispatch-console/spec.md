## ADDED Requirements

### Requirement: Filters Opener Surfaces Drops From Known Contacts

The Filters verdict opener SHALL report outstanding drops of messages from known contacts using the `GET /api/ingestion/events/dropped-known` aggregate. When the aggregate reports a positive `dropped` count the opener SHALL render "N dropped from people you know" as a link to the filtered events. When the aggregate request fails or reports `available=false` the opener SHALL render "gate harm unknown". In both cases the opener SHALL NOT render the all-clear line. The aggregate SHALL count only unanswered marked drops (`filtered` or `replay_failed`) and SHALL respond with `available=false` rather than zero when the filtered-event store cannot be read.

#### Scenario: Known-contact drops are named with a door

- **WHEN** the aggregate reports 3 outstanding drops
- **THEN** the opener renders "3 dropped from people you know" linking to the filtered events
- **AND** no all-clear line is rendered

#### Scenario: Unavailable aggregate is unknown, not clear

- **WHEN** the aggregate request fails or returns `available=false`
- **THEN** the opener renders "gate harm unknown"
- **AND** no all-clear line is rendered

#### Scenario: Aggregate read failure degrades honestly

- **WHEN** the filtered-event read raises on the server
- **THEN** the endpoint returns HTTP 200 with `available=false` and zero counts
