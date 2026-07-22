# Dashboard Data Layer and API — Delta

## ADDED Requirements

### Requirement: Decisions Digest Endpoint

The dashboard API SHALL expose `GET /api/decisions`, a read-only endpoint
returning the open decision-bead digest as an `ApiResponse<DecisionBeadSummary[]>`.
The endpoint SHALL be a thin wrapper around
`butlers.jobs.decision_review.compute_decision_digest()` (bu-ckkpz.4) -- it
MUST NOT re-implement label-only decision classification, the escalation
(P1-bug / deploy `blocks`-edge, >48h) computation, or the beads-export JSONL
read path; those stay owned by `decision_review.py`. An open decision is a
non-epic bead carrying the `decision` label; title text alone MUST NOT cause
the endpoint to include a bead. The separate strict lint path identifies
legacy-shaped unlabeled beads for migration.

Each `DecisionBeadSummary` object SHALL contain:
- `id` -- string bead id
- `title` -- string bead title
- `priority` -- integer | null
- `created_at` -- ISO 8601 timestamp
- `age_hours` -- float, `checked_at - created_at` in hours
- `escalated` -- boolean, true when this decision has blocked a P1 bug or a
  deploy-marked bead for more than 48 hours
- `escalated_blocked_id` / `escalated_blocked_title` / `escalated_blocked_kind`
  (`"p1_bug"` | `"deploy"`) / `escalated_block_hours` -- populated from the
  single longest-blocked escalation hit against this decision when
  `escalated` is true, otherwise all `null`

The response's `meta` bag SHALL carry `decisions_available: boolean` (the
fleet-wide degraded-envelope convention -- see "API Conventions" in the
project root guidance). `decisions_available: false` means the beads-export
digest could not be read (missing, stale, or unreadable); in that case `data`
SHALL be an empty list and MUST NOT be interpreted as "zero decisions
waiting." A genuine zero (export readable, zero decision-marked beads
currently open) SHALL report `decisions_available: true` with an empty list.
`meta.unavailable_reason` SHALL echo `DecisionDigest.unavailable_reason`
(e.g. `"export_missing"`, `"export_stale"`) when `decisions_available` is
false.

`meta.export_as_of` SHALL carry the beads export file's own mtime (ISO 8601
timestamp) whenever it was successfully stat'd -- including on the
`export_stale` branch, where the export was readable but old enough to be
distrusted. It SHALL be `null`/absent only when the export was never reached
(e.g. `export_missing`). This lets a consumer render the true data age
instead of trusting hour-precision `age_hours` values computed against a
single-file bind-mount that may silently freeze at container-start inode.

Open decisions SHALL be ordered oldest-first (ascending `created_at`),
matching `compute_decision_digest()`'s own ordering.

#### Scenario: Genuine all-clear

- **WHEN** the beads export is readable and contains zero decision-marked beads
- **THEN** `GET /api/decisions` returns `{"data": [], "meta": {"decisions_available": true}}`

#### Scenario: Degraded digest never reads as an all-clear

- **WHEN** the beads-export JSONL is missing, stale, or unreadable
- **THEN** `GET /api/decisions` still returns HTTP 200 with `data: []`
- **AND** `meta.decisions_available` is `false`
- **AND** `meta.unavailable_reason` names the failure mode

#### Scenario: Escalated decision carries blocking detail

- **WHEN** an open decision bead has a `blocks` dependency edge from an open
  P1 bug (or a deploy-titled bead) older than 48 hours
- **THEN** that decision's `DecisionBeadSummary.escalated` is `true`
- **AND** `escalated_blocked_id`/`escalated_blocked_title`/`escalated_blocked_kind`/`escalated_block_hours`
  describe the longest-blocked such edge

#### Scenario: A stale-but-not-yet-unavailable export still reports its age

- **WHEN** the beads export is readable but old enough to be flagged
  `export_stale` (`decisions_available: false`)
- **THEN** `meta.export_as_of` is still populated with the export's mtime
  (not `null`), so a caller can report exactly how stale the data is

### Requirement: Scheduled Decision-Convention Lint Uses Live Candidates

The scheduled full-export marker-lint selection SHALL select only `open`,
`in_progress`, and `blocked` issues before linting. When
`scripts/lint_decision_beads.py` receives a full beads export through
`--check-unlabeled-markers` without explicit issue IDs, that live-status filter
applies equally to decision-labeled issues and to
unlabeled title-marker matches, so historical closed records cannot create a
weekly owner-facing migration alert. The scheduled
`butlers.jobs.decision_review` subprocess invocation SHALL retain its existing
unavailable/error handling: a missing or unreadable lint script/input, an
unexpected nonzero exit, or malformed/non-JSON lint output MUST NOT be
reported as a calm successful audit. A JSON lint payload is valid only when
every result has string `id` and `title` fields, boolean `ok`, and
`violations: list[str]`; `ok` SHALL be true exactly when `violations` is empty;
and exit `0` SHALL occur only when every result is okay while exit `1` SHALL
occur only when at least one result is not okay. Any mismatch is malformed
output. The scheduled digest SHALL return an unavailable scheduled result,
record the existing failed attention-ledger outcome with a `data_unavailable:`
reason, and continue scheduler processing without raising. A successful
zero-candidate lint result remains the normal calm `no_decisions` outcome.

Explicit issue IDs SHALL remain forensic input and MUST be linted as supplied,
even when `--check-unlabeled-markers` is present. An ordinary non-strict
`--status all` audit SHALL likewise retain historical records, because the
live-status selection is specific to the scheduled full-export marker mode.

#### Scenario: Closed malformed records do not enter the weekly lint lane

- **WHEN** the scheduled marker-mode subprocess reads a full export containing
  an open malformed decision candidate and a closed malformed decision-labeled
  record (or a closed unlabeled marker match)
- **THEN** only the open candidate is returned as a lint violation
- **AND** the closed historical records produce no owner-facing migration alert

#### Scenario: Explicit forensic input retains historical visibility

- **WHEN** an operator supplies a closed issue ID explicitly with
  `--check-unlabeled-markers`
- **THEN** the linter checks that supplied issue regardless of its status

#### Scenario: Non-strict all-status audits retain historical visibility

- **WHEN** an operator runs an ordinary `--status all` audit without
  `--check-unlabeled-markers`
- **THEN** the linter retains historical records from that input

#### Scenario: Scheduled lint failure is unavailable, not a calm audit

- **WHEN** the scheduled marker-mode lint script or its input is missing or
  unreadable, the process exits unexpectedly, or its stdout is malformed/non-JSON
- **THEN** the scheduled digest returns unavailable and records its existing
  failed attention-ledger path with a `data_unavailable:` reason
- **AND** the scheduler continues without raising
- **AND** the digest MUST NOT return `available: true` with
  `outcome: "no_decisions"`

#### Scenario: Semantically invalid JSON lint output is unavailable

- **WHEN** the scheduled marker-mode lint subprocess returns syntactically JSON
  that violates the payload contract, such as exit `1` with `[]` or a result
  with `ok: true` and `violations: [123]`
- **THEN** the digest returns unavailable with
  `reason: "lint_output_invalid_shape"` and records the existing failed
  attention-ledger path with a `data_unavailable:lint_output_invalid_shape`
  reason
- **AND** the scheduler continues without raising and MUST NOT return
  `available: true` with `outcome: "no_decisions"`

#### Scenario: Successful zero-candidate lint remains calm

- **WHEN** the scheduled marker-mode lint subprocess exits successfully with a
  valid JSON zero-candidate result and the export has zero open decisions
- **THEN** the digest returns `available: true` with `outcome: "no_decisions"`
