## MODIFIED Requirements

### Requirement: Decisions Digest Endpoint

The dashboard API SHALL expose `GET /api/decisions` as a read-only
`ApiResponse<DecisionBeadSummary[]>` wrapper around the shared deterministic
decision calculation fed by `BeadReadProvider`. The endpoint MUST NOT
re-implement the label-only classifier, escalation calculation, provider
reader, JSONL parser, or convention linter; it MUST NOT call `bd`, Dolt,
GitHub, or any decision mutation/default-application path. An open decision is
a non-epic bead with the `decision` label, and title text alone MUST NOT include
a record.
Each summary SHALL retain the existing `id`, `title`, `priority`, `created_at`,
`age_hours`, and escalation fields. It SHALL additionally project
`description` as the allowlisted source description of an eligible decision or
`null` when it is absent or not a string; `options` as the ordered normalized
decision options or `null` when they cannot be trusted; `default` as the
normalized decision default or `null` when it cannot be trusted; `due_at` as
the native Beads deadline timestamp or `null` when it is absent or invalid;
`structured_details_available` as whether the governed decision fields and
native deadline are valid; and `structured_details_unavailable_reason` as
`null` when details are available or a named missing or malformed source reason
otherwise. The calculation SHALL preserve option order. It SHALL mark structured details
available only when decision options are a non-empty ordered list of distinct
non-blank strings, the default is a non-blank string that exactly matches an
option, and `due_at` is a valid native timestamp. It MUST NOT sort, infer,
apply, silently replace, or expose raw source metadata, notes, history, or
arbitrary issue descriptions. Missing decision metadata and malformed decision
metadata SHALL remain distinguishable through the unavailable reason.
The response `meta` SHALL retain `decisions_available: boolean`,
`unavailable_reason`, and `export_as_of` for explicit JSONL mode or a known
source export time. It SHALL add `beads_source` (`jsonl` or `projection`),
`snapshot_as_of`, `beads_freshness` (`fresh`, `warning`, or `unavailable`),
and `beads_target_met` (`true`, `false`, or `null` for an unavailable source).
Missing, unreadable, schema-mismatched, or hard-stale provider data SHALL
return `data: []` with `decisions_available: false` and a named reason; a
readable source with zero decisions SHALL return
`decisions_available: true` and an empty list only after the selected source has
passed the source-completeness policy. A current
`source_completeness_unverified` outcome SHALL return `data: []` with
`decisions_available: false` and that named reason, even when a retained
snapshot exists. A warning snapshot remains readable but SHALL name `warning`
freshness. Open decisions SHALL remain oldest-first.

ID: REQ-dashboard-api-001
Source: RFC 0025 §§3, 5-8; RFC 0007
Scope: v1-mandatory

#### Scenario: Valid structured context preserves order and the native deadline

- **WHEN** the selected source contains an open decision with an allowlisted
  string description, ordered valid options, a matching default, and a valid
  `due_at`
- **THEN** its API summary contains those values without reordering or
  inference
- **AND** `structured_details_available` is `true`
- **AND** `structured_details_unavailable_reason` is `null`

#### Scenario: Malformed metadata is visible without degrading the whole snapshot

- **WHEN** a readable selected source contains an otherwise eligible decision
  whose source decision options or default violates the decision convention
- **THEN** the response still contains that decision and
  `meta.decisions_available` remains `true`
- **AND** that decision reports `structured_details_available: false` with a
  named malformed-metadata reason
- **AND** the endpoint MUST NOT turn the invalid source into a calm empty
  options/default result

#### Scenario: Missing structured metadata is distinct from malformed metadata

- **WHEN** a readable selected source contains an eligible decision with no
  decision metadata mapping
- **THEN** that decision reports `structured_details_available: false` with a
  named missing-metadata reason
- **AND** it is not reported as a malformed whole snapshot

#### Scenario: Hard-unavailable source never reads as an all-clear

- **WHEN** the selected JSONL source is missing, stale, or unreadable, or the
  selected projection is missing, unreadable, schema-mismatched, or older than
  fifteen minutes
- **THEN** `GET /api/decisions` returns HTTP 200 with `data: []`
- **AND** `meta.decisions_available` is `false`
- **AND** `meta.unavailable_reason` names the source failure
- **AND** `meta.beads_freshness` is `unavailable`

#### Scenario: Unverified source completeness cannot render an empty all-clear

- **WHEN** the selected projection retains its prior pointer after an empty or
  count-regressed candidate fails source-completeness validation
- **THEN** `GET /api/decisions` returns HTTP 200 with `data: []`
- **AND** `meta.decisions_available` is `false`
- **AND** `meta.unavailable_reason` is `source_completeness_unverified`
- **AND** `meta.beads_freshness` is `unavailable`

#### Scenario: Warning projection keeps data and provenance visible

- **WHEN** the selected projection is more than ten and no more than fifteen
  minutes old
- **THEN** the endpoint returns its current decision rows with
  `meta.decisions_available: true`
- **AND** `meta.beads_source` is `projection`
- **AND** `meta.snapshot_as_of` is populated
- **AND** `meta.beads_freshness` is `warning`
- **AND** `meta.beads_target_met` is `false`

#### Scenario: Escalated decision carries blocking detail

- **WHEN** an open decision has blocked an open P1 bug or deploy-marked bead
  for more than 48 hours
- **THEN** its unchanged escalation fields describe the longest-blocked hit

#### Scenario: Explicit JSONL source retains known export time

- **WHEN** JSONL is the explicitly selected source and its export mtime is
  known, including on a stale result
- **THEN** `meta.export_as_of` remains populated with that mtime
- **AND** `meta.beads_source` is `jsonl`
