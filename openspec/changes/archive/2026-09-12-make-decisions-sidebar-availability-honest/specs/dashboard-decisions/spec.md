## MODIFIED Requirements

### Requirement: Decisions Page Route and Navigation

The dashboard SHALL expose a routed `/decisions` page registered in the
sidebar Main section with a dedicated icon and a navigation indicator driven by
the read-only Decisions digest. A readable digest with one or more open
decision records SHALL render its positive numeric count. A readable digest
with zero open decision records SHALL render no badge. When
`meta.decisions_available` is `false`, or the Decisions query errors directly,
the sidebar SHALL render an accessible unavailable marker instead of a numeric
zero. The marker SHALL be labelled `Decisions digest unavailable` and SHALL
appear consistently in rail, expanded desktop, and mobile sidebar variants.

#### Scenario: Decisions page is reachable from the sidebar

- **WHEN** the owner opens the sidebar and the readable digest contains open
  decision records
- **THEN** a Decisions entry links to `/decisions`
- **AND** its indicator renders the positive open-decision count

#### Scenario: Available empty digest remains quiet

- **WHEN** the Decisions digest is readable and contains zero open decision
  records
- **THEN** the Decisions entry renders no numeric badge or unavailable marker

#### Scenario: Degraded digest renders an accessible unavailable marker

- **WHEN** `meta.decisions_available` is `false`
- **THEN** the Decisions entry renders an unavailable marker labelled
  `Decisions digest unavailable`
- **AND** it does not render a numeric zero

#### Scenario: Direct query failure renders an accessible unavailable marker

- **WHEN** the Decisions query errors before a usable digest is available
- **THEN** the Decisions entry renders an unavailable marker labelled
  `Decisions digest unavailable`
- **AND** it does not render a numeric zero

#### Scenario: Availability state is consistent across sidebar variants

- **WHEN** the Decisions digest is unavailable
- **THEN** the rail, expanded desktop sidebar, and mobile sidebar each render
  the labelled unavailable marker
- **AND** a positive available count remains numeric in each variant
