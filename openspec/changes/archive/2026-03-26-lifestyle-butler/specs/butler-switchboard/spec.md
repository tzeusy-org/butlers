# Switchboard — Delta for lifestyle-butler change

## MODIFIED Requirements

### Requirement: Domain Butler Registry

The Switchboard SHALL include the Lifestyle butler as a routing target in its domain classification.

#### Scenario: Lifestyle domain classification

- **WHEN** the Switchboard classifies an incoming message
- **AND** the message content relates to music, listening, playlists, entertainment (movies, TV, books, games, podcasts), food preferences, favorite restaurants, cuisines, recipes, hobbies, personal interests, leisure activities, or daily routines
- **THEN** the Switchboard SHALL route the message to the `lifestyle` butler at `http://localhost:41109`

#### Scenario: Multi-butler fanout with lifestyle overlap

- **WHEN** a message contains both lifestyle and health signals (e.g., "I've been stress-eating Thai food all week")
- **THEN** the Switchboard SHALL route to both `lifestyle` (food preference: Thai) and `health` (stress eating pattern)
- **AND** each butler SHALL extract domain-relevant facts independently

#### Scenario: Lifestyle vs General disambiguation

- **WHEN** a message could be classified as either lifestyle or general
- **AND** the message relates to taste, preferences, entertainment, or routines
- **THEN** the Switchboard SHALL prefer routing to `lifestyle` over `general`
- **AND** `general` SHALL only receive messages that do not fit any domain butler's scope
