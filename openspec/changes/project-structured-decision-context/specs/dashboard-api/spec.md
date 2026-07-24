## ADDED Requirements

### Requirement: Decisions Digest Endpoint

The dashboard API SHALL expose `GET /api/decisions` as a read-only
`ApiResponse<DecisionBeadSummary[]>` wrapper around
`butlers.jobs.decision_review.compute_decision_digest()`. The endpoint MUST
NOT re-implement the label-only classifier, escalation calculation, or
exported-JSONL reader; it MUST NOT call `bd`, Dolt, the convention linter, or
any decision mutation/default-application path. An open decision is a
non-epic bead with the `decision` label, and title text alone MUST NOT include
a record.

Each summary SHALL retain the existing `id`, `title`, `priority`, `created_at`,
`age_hours`, and escalation fields, and SHALL additionally project:

- `description`: the exported string description or `null` when it is absent
  or not a string;
- `options`: the ordered exported `metadata.decision.options` strings or
  `null` when they cannot be trusted;
- `default`: the exported `metadata.decision.default` string or `null` when it
  cannot be trusted;
- `due_at`: the exported native Beads `due_at` timestamp or `null` when it is
  absent or invalid;
- `structured_details_available`: whether the governed decision metadata and
  native deadline are valid; and
- `structured_details_unavailable_reason`: `null` when details are available,
  otherwise a named missing or malformed source reason.

The projection SHALL preserve option order. It SHALL mark structured details
available only when `metadata.decision.options` is a non-empty ordered list of
distinct non-blank strings, `metadata.decision.default` is a non-blank string
that exactly matches an option, and `due_at` is a valid native timestamp. It
MUST NOT sort, normalize, infer, apply, or silently replace an option/default
or deadline. Missing decision metadata and malformed decision metadata SHALL
remain distinguishable through the unavailable reason.

The response `meta` SHALL retain `decisions_available: boolean`. Missing,
stale, or unreadable exports SHALL return `data: []` with
`decisions_available: false` and `unavailable_reason`; a readable export with
zero decisions SHALL return `decisions_available: true` and an empty list.
`meta.export_as_of` SHALL retain the export mtime whenever it can be stat'd,
including a stale export. Open decisions SHALL remain oldest-first.

#### Scenario: Valid structured context preserves order and the native deadline

- **WHEN** a readable export contains an open decision with a string description,
  ordered valid options, a matching default, and a valid `due_at`
- **THEN** its API summary contains those values without reordering or inference
- **AND** `structured_details_available` is `true`
- **AND** `structured_details_unavailable_reason` is `null`

#### Scenario: Malformed metadata is visible without degrading the whole export

- **WHEN** a readable export contains an otherwise eligible decision whose
  `metadata.decision.options` or default violates the decision convention
- **THEN** the response still contains that decision and
  `meta.decisions_available` remains `true`
- **AND** that decision reports `structured_details_available: false` with a
  named malformed-metadata reason
- **AND** the endpoint MUST NOT turn the invalid source into a calm empty
  options/default result

#### Scenario: Missing structured metadata is distinct from malformed metadata

- **WHEN** a readable export contains an eligible decision with no
  `metadata.decision` mapping
- **THEN** that decision reports `structured_details_available: false` with a
  named missing-metadata reason
- **AND** it is not reported as a malformed whole export

#### Scenario: Degraded export never reads as an all-clear

- **WHEN** the beads export is missing, stale, or unreadable
- **THEN** `GET /api/decisions` returns HTTP 200 with `data: []`
- **AND** `meta.decisions_available` is `false`
- **AND** `meta.unavailable_reason` names the export failure

#### Scenario: Escalated decision carries blocking detail

- **WHEN** an open decision has blocked an open P1 bug or deploy-marked bead
  for more than 48 hours
- **THEN** its unchanged escalation fields describe the longest-blocked hit

#### Scenario: A stale export still reports its known age

- **WHEN** the export is readable but stale enough to set
  `decisions_available: false`
- **THEN** `meta.export_as_of` remains populated with the export mtime

### Requirement: Scheduled Decision-Convention Lint Uses Live Candidates

The scheduled full-export marker-lint selection SHALL select only `open`,
`in_progress`, and `blocked` issues before linting. When
`scripts/lint_decision_beads.py` receives a full export through
`--check-unlabeled-markers` without explicit issue IDs, that filter SHALL
apply to both decision-labeled records and unlabeled title-marker matches so
closed history does not create a weekly owner-facing migration alert.

The scheduled decision-review subprocess SHALL retain fail-closed handling:
missing/unreadable inputs, unexpected exits, malformed output, or output whose
result records are not `{id: str, title: str, ok: bool, violations: list[str]}`
MUST return an unavailable scheduled result and record the existing failed
attention-ledger path without raising. A successful zero-candidate result
SHALL remain the normal `no_decisions` result. Explicit issue ids and ordinary
non-strict `--status all` audits SHALL retain forensic historical visibility.

#### Scenario: Closed malformed records do not enter the weekly lint lane

- **WHEN** scheduled marker-mode lint reads a full export containing both an
  open malformed candidate and a closed malformed candidate
- **THEN** only the open candidate is returned as a lint violation
- **AND** the closed record creates no owner-facing migration alert

#### Scenario: Scheduled lint failure is unavailable rather than calm

- **WHEN** the scheduled marker-mode lint script or input is unavailable, its
  process exits unexpectedly, or its output is malformed
- **THEN** the scheduled digest returns unavailable with a `data_unavailable:`
  reason and continues scheduler processing without raising
- **AND** it MUST NOT return `available: true` with `outcome: "no_decisions"`
