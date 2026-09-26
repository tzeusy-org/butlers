## ADDED Requirements

### Requirement: Dispatch Surface Primitives

Dashboard surfaces SHALL use the semantic `Section` primitive as the default container. Section
uses semantic section markup, titles the region with the canonical Eyebrow treatment, and keeps
hierarchy in type and rules rather than card chrome. A quiet section with no active content SHALL
remove its chrome or render one calm serif-italic sentence, with no illustration or mock filler.

`Tile` SHALL be used only for a module in a dense status grid that can load or degrade
independently from its neighboring modules. A Tile exposes that module boundary without changing
the consumer's existing loading, error, empty, or retry semantics. Ordinary page composition and
single-surface content use Section.

The retired `ui/card` module SHALL have no production imports, re-export, compatibility alias, or
path shim. New code SHALL use Section or the narrow Tile role directly.

#### Scenario: Section is the default semantic surface

- **WHEN** a dashboard page composes an ordinary titled region
- **THEN** it uses Section with semantic section markup and an Eyebrow title
- **AND** the region has no elevated Card shell or nested Card chrome

#### Scenario: Quiet Section removes decoration

- **WHEN** a Section has no active content
- **THEN** it renders without chrome or as one calm serif-italic sentence
- **AND** it adds no illustration, mock content, or decorative filler

#### Scenario: Tile isolates dense-grid module state

- **WHEN** a dense status-grid module can load or degrade independently
- **THEN** it uses Tile and marks its own loading or degraded boundary
- **AND** a sibling module remains renderable when that Tile fails

#### Scenario: Card primitive is retired without an alias

- **WHEN** production frontend code imports or composes a dashboard container
- **THEN** it has no `ui/card` import or Card compatibility alias
- **AND** the canonical Section or narrow Tile primitive is used directly

#### Scenario: Operational state uses one semantic resolver

- **WHEN** a connector, Google Health account, or topology node renders operational state
- **THEN** its state mark uses StateDot, Sev, or `stateColorVar`
- **AND** the consumer does not define a duplicate state-to-token map or a second visible status affordance
