# Dashboard Decisions

## Purpose

The Decisions lane makes open owner decision beads visible as a read-only,
source-honest dashboard surface backed solely by the exported Beads snapshot.

## Requirements

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

### Requirement: Decisions Verdict Opener

The `/decisions` page SHALL render a verdict opener from the decision digest:
`N decision(s) waiting, oldest Xd`, plus an escalation clause when one or more
records block a P1 bug or deploy. When the digest is unavailable or the query
errors, the opener MUST name that condition and MUST NOT render the calm
all-clear line.

#### Scenario: Genuine all-clear

- **WHEN** the digest is available and contains zero open decisions
- **THEN** the opener renders `No decisions waiting.`

#### Scenario: Degraded digest suppresses the all-clear

- **WHEN** `meta.decisions_available` is `false`
- **THEN** the opener names the digest as unavailable
- **AND** it does not render `No decisions waiting.`

### Requirement: Read-Only Decision Detail and Selection

The `/decisions` page SHALL render eligible decisions as a rule-separated,
keyboard-operable row list with id, title, priority when present, age, and
escalation state. The list SHALL retain j/k roving selection through
`useListTriage`, publish its existing footer hints, and expand only the
selected row inline. Click and keyboard selection SHALL remain usable without
introducing approve, deny, close, default-application, or Telegram controls.

When a selected decision has `structured_details_available: true`, its detail
SHALL render the exported description when present, options in API order, the
matching default distinctly, and the native due timestamp. When structured
details are unavailable, its detail SHALL name the unavailable/malformed reason
and MUST NOT imply that the decision has no options or invent a default. The
existing created-at and escalation details SHALL remain visible.

The `bead` query parameter SHALL deep-link selection: `/decisions?bead=<id>`
selects and expands a record only when that id occurs in the current digest. An
unknown id SHALL leave the normal list visible and unselected; it MUST NOT
create a fabricated record or hide usable rows.

#### Scenario: Valid detail remains read-only

- **WHEN** the owner selects a record with valid structured details
- **THEN** its inline panel shows the description, ordered options, default,
  native deadline, created-at, and any escalation detail
- **AND** no decision mutation control is rendered

#### Scenario: Malformed metadata is named instead of calming the owner

- **WHEN** the owner selects a record with
  `structured_details_available: false` because metadata is malformed
- **THEN** its inline panel names structured details as unavailable/malformed
- **AND** it does not render a calm no-options message or a fabricated default

#### Scenario: Present deep link selects the matching record

- **WHEN** the owner opens `/decisions?bead=bu-present` and `bu-present` is in
  the current digest
- **THEN** only that record expands inline

#### Scenario: Unknown deep link leaves the list usable

- **WHEN** the owner opens `/decisions?bead=bu-unknown` and that id is absent
- **THEN** no record is fabricated or auto-expanded
- **AND** the owner can still select any listed record by click or j/k

#### Scenario: Degraded digest names itself instead of a calm empty state

- **WHEN** `meta.decisions_available` is `false`
- **THEN** the list area renders a named Decisions degraded note instead of
  `No decisions waiting.`

### Requirement: Export As-Of Plaque

The `/decisions` page SHALL render an export-as-of plaque whenever
`meta.export_as_of` is known. A recently refreshed export SHALL use muted
styling, while an aging but still available export SHALL use a warning tint.
The plaque SHALL remain visible beside a degraded note when the mtime is known
and SHALL be omitted only when no export mtime exists.

#### Scenario: Plaque persists alongside a stale-export note

- **WHEN** `decisions_available` is `false` for a stale export and
  `export_as_of` is known
- **THEN** the page renders both the named degraded note and the export-as-of
  plaque
