# Dashboard Data Layer and API — Delta

## ADDED Requirements

### Requirement: Decisions Digest Endpoint

The dashboard API SHALL expose `GET /api/decisions`, a read-only endpoint
returning the open decision-bead digest as an `ApiResponse<DecisionBeadSummary[]>`.
The endpoint SHALL be a thin wrapper around
`butlers.jobs.decision_review.compute_decision_digest()` (bu-ckkpz.4) -- it
MUST NOT re-implement the title-marker decision-detection heuristic, the
escalation (P1-bug / deploy `blocks`-edge, >48h) computation, or the
beads-export JSONL read path; those stay owned by `decision_review.py`.

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
