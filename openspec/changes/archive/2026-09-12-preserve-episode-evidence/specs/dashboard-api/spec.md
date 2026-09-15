## ADDED Requirements

### Requirement: Memory APIs Expose Truthful Source Episode State

Memory API responses for facts, rules, and generic memory links SHALL expose a
typed episode-reference state whenever an episode identifier is present. The
state MUST be `available`, `expired`, or `unresolved`: `available` only when
the live episode can be read; `expired` when a content-free tombstone proves
deletion; and `unresolved` when neither relation establishes the source state.
The API MUST NOT omit a retained identifier or describe it as no provenance
solely because its episode is deleted.

#### Scenario: Fact and rule retain an expired source reference

- **WHEN** a fact or rule has a `source_episode_id` whose tombstone exists
- **THEN** its API response MUST retain that identifier and return source state
  `expired`
- **AND** it MUST NOT return raw episode content or internal deletion details

#### Scenario: Generic link reports expired episode endpoint

- **WHEN** a memory-link endpoint is an episode whose tombstone exists
- **THEN** the generic link API or tool response MUST mark that endpoint as
  `expired`
- **AND** the relation MUST remain visible as durable provenance evidence

#### Scenario: Unknown source stays explicit

- **WHEN** a fact, rule, or generic link names an episode identifier that is
  neither live nor tombstoned
- **THEN** the response MUST report source state `unresolved`
- **AND** it MUST NOT claim that the source is available or absent by design
