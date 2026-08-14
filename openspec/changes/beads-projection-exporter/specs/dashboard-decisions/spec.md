## MODIFIED Requirements

### Requirement: Export As-Of Plaque

The `/decisions` page SHALL render a source-as-of plaque whenever
`meta.snapshot_as_of` or `meta.export_as_of` is known. It SHALL prefer the
projection snapshot timestamp, identify `meta.beads_source`, and use
`meta.beads_freshness` to distinguish muted fresh data, a visibly named warning
snapshot, and an unavailable source. The plaque SHALL remain visible beside a
degraded note when a known timestamp exists and SHALL be omitted only when no
source timestamp exists. A warning source remains readable but MUST NOT be
styled or labelled as current. Any `meta.unavailable_reason`, including
`source_completeness_unverified`, SHALL retain the named degraded note rather
than render the empty state as a calm all-clear.

ID: REQ-dashboard-decisions-001
Source: RFC 0025 §§3, 5-8; RFC 0007
Scope: v1-mandatory

#### Scenario: Projection warning is visible with current rows

- **WHEN** `decisions_available` is `true`, `beads_source` is `projection`,
  `beads_freshness` is `warning`, and `snapshot_as_of` is known
- **THEN** the page renders the decision rows and a warning-tinted plaque that
  names the projection source and its as-of time

#### Scenario: Plaque persists alongside an unavailable source note

- **WHEN** `decisions_available` is `false` for a stale or unreadable selected
  source and either `snapshot_as_of` or `export_as_of` is known
- **THEN** the page renders both the named degraded note and the source-as-of
  plaque

#### Scenario: Source-completeness failure remains visibly unavailable

- **WHEN** `meta.decisions_available` is `false` and
  `meta.unavailable_reason` is `source_completeness_unverified`
- **THEN** the page renders the named degraded note rather than a no-decisions
  all-clear

#### Scenario: Explicit JSONL mode remains identifiable

- **WHEN** `beads_source` is `jsonl` and `export_as_of` is known
- **THEN** the plaque identifies JSONL rather than projection and renders the
  export-as-of time
