# Butler Switchboard

## MODIFIED Requirements

### Requirement: Explicit Chronicler Routing Boundary

Switchboard SHALL route explicit retrospective time-review requests to Chronicler
and SHALL NOT route passive source events to Chronicler solely because they are
timestamped.

#### Scenario: Retrospective time-review request routes to Chronicler

- **WHEN** the user asks "what did I do yesterday afternoon?"
- **THEN** Switchboard SHALL classify the request as a Chronicler-owned retrospective time-review intent
- **AND** route the request to Chronicler

#### Scenario: Music recommendation remains Lifestyle

- **WHEN** the user asks "recommend music based on what I listened to last week"
- **THEN** Switchboard SHALL route the request to Lifestyle, not Chronicler
- **AND** Lifestyle MAY use its own domain evidence for taste and recommendation work

#### Scenario: Time-accounting music question routes to Chronicler

- **WHEN** the user asks "how much time did I spend listening to music last week?"
- **THEN** Switchboard SHALL route the request to Chronicler
- **AND** Chronicler SHALL answer from projected temporal records

#### Scenario: Scheduling request does not route to Chronicler

- **WHEN** the user asks "schedule a meeting tomorrow"
- **THEN** Switchboard SHALL NOT route the request to Chronicler
- **AND** the request SHALL route to the appropriate calendar/general scheduling owner

#### Scenario: Passive timestamped event not routed to Chronicler

- **WHEN** a passive source event such as Spotify playback, Steam activity, OwnTracks location, email, or chat metadata enters the system
- **THEN** Switchboard SHALL NOT route it to Chronicler solely because it contains time evidence
- **AND** Chronicler SHALL consume compatible evidence later through projection jobs

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (transport is connector responsibility)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0014 (Chronicler Time Butler, Draft)
