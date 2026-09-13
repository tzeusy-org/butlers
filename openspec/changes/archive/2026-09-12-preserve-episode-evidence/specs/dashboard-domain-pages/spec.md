## ADDED Requirements

### Requirement: Expired Episode Provenance Has No Dangling Door

Memory detail and register surfaces SHALL render a source episode as a
navigation link only when the typed source state is `available`. An `expired`
source MUST remain visible as truthful content-free provenance but MUST be
non-clickable; an `unresolved` source MUST be visibly uncertain and
non-clickable. The surfaces MUST NOT replace either state with a false
no-provenance presentation.

#### Scenario: Fact detail renders a deleted source without navigation

- **WHEN** FactDetailPage receives a fact with an `expired` source episode
- **THEN** it MUST display a visible `Source expired` provenance state
- **AND** it MUST NOT render a link to `/memory/episodes/:episodeId`

#### Scenario: Rule and register surfaces preserve truthfulness

- **WHEN** RuleDetailPage or a memory register receives an `expired` or
  `unresolved` source episode state
- **THEN** it MUST display the matching source state without a live-episode
  navigation affordance
- **AND** it MUST retain the durable fact, rule, or link evidence in view
