## ADDED Requirements

### Requirement: Bounded snapshot-only Bead detail reader

The dashboard SHALL obtain a Bead detail only from the existing read-only
Beads JSONL export. A shared reader SHALL fully assess the regular-file
snapshot, mtime freshness, bounded file/line/record limits, and JSONL
readability before it resolves an ID. It SHALL cap direct dependency summaries
at 20 records and SHALL NOT perform a live `bd`, Dolt, GitHub, database,
credential, network, or tracker mutation operation.

The reader SHALL expose no raw record mapping, arbitrary source key, or
caller-selected projection. It SHALL construct a detail solely from this
allowlist: ID, title, status, priority, type, description, design, acceptance
criteria, labels, safe timestamps, bounded dependency summaries, and
`external_ref`. Each dependency summary SHALL contain only ID, title, status,
priority, and type. It MUST NOT expose notes, metadata, comments, identities,
credentials, raw dependency edges, raw records, or arbitrary href values.

#### Scenario: A record projects only allowed fields

- **WHEN** a fresh readable export record contains allowed fields alongside
  notes, metadata, comments, identities, credentials, and arbitrary URLs
- **THEN** the reader returns only the allowlisted detail fields and bounded
  dependency-summary fields
- **AND** none of the non-allowlisted source values are materialized in the
  returned representation

#### Scenario: Oversized or malformed source is unavailable

- **WHEN** the export exceeds a reader bound or any JSONL line prevents a full
  bounded parse
- **THEN** the reader reports the snapshot unavailable
- **AND** it does not return an earlier matching record or a partial all-clear

### Requirement: Same-origin Bead detail page

The dashboard SHALL expose `/beads/:id` as a read-only Bead detail route. It
SHALL use the detail-page shell with a record-identity title, semantic body
sections, visible export-as-of information, native focusable links and retry
control, and rule-separated content without Card-style body chrome. It SHALL
render only API-provided allowlisted fields and no mutation action.

`external_ref`, when present, SHALL be rendered as inert text and MUST NOT be
an anchor, navigation target, or fetched URL. Each dependency drill-down SHALL
be a same-origin `/beads/:id` route derived from the safe dependency ID.

#### Scenario: Detail renders allowed evidence without external navigation

- **WHEN** an available Bead detail includes an `external_ref` and dependency
  summaries
- **THEN** the page displays `external_ref` as text without an `href`
- **AND** each dependency navigation target is a same-origin `/beads/:id`
  route
- **AND** no tracker mutation control is rendered

#### Scenario: Detail state remains honest

- **WHEN** the detail request is loading, not found, or source-unavailable
- **THEN** the page renders distinct accessible loading, not-found, or
  unavailable states
- **AND** the unavailable state exposes the known export-as-of time and a
  retry control when one is available
- **AND** it never describes an unavailable snapshot as an absent Bead
