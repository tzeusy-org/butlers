## Why

The read-only Decisions lane already truthfully reports whether its exported
Beads snapshot is missing, stale, or unreadable, but it deliberately omits the
structured context the owner needs to understand a decision. The Beads
convention already supplies that context in the export; projecting it through
the existing digest lets the dashboard explain a decision without gaining live
Dolt access or authority to apply it.

## What Changes

- Extend the existing read-only decision-digest projection with a validated
  description, ordered options, matching default, and native `due_at` value.
- Surface an explicit per-record structured-details availability signal and a
  named reason when the exported decision metadata is missing or malformed.
  This is distinct from the existing whole-export degraded envelope.
- Render the validated context in the existing inline Decisions detail and
  support selecting a present decision with `/decisions?bead=<id>`; an unknown
  id leaves the ordinary list unselected and usable.
- Establish and sync the canonical Decisions and Dashboard API OpenSpec
  contracts, preserving the existing verdict opener, export-as-of plaque,
  escalation detail, and j/k triage behavior.

## Capabilities

### New Capabilities

- `dashboard-decisions`: The owner-facing, read-only Decisions lane and its
  source-honest selection/detail behavior.

### Modified Capabilities

- `dashboard-api`: `GET /api/decisions` projects validated structured decision
  context from the existing exported-JSONL digest while retaining its current
  degraded envelope.

## Impact

- `src/butlers/jobs/decision_review.py`, `src/butlers/api/models/decision.py`,
  and `src/butlers/api/routers/decisions.py` gain read-only projection and
  validation only; they do not call `bd`, Dolt, lint, or any mutation path.
- `frontend/src/api/types.ts`, `frontend/src/pages/DecisionsPage.tsx`, and
  their focused tests gain typed detail rendering and query-backed selection.
- `openspec/specs/dashboard-api/spec.md` and the new
  `openspec/specs/dashboard-decisions/spec.md` become the canonical contracts.
