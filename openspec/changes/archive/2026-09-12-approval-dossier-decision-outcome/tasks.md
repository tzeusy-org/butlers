## 1. Detail response contract

- [x] 1.1 Extend the approval-detail model and mapper with nullable
  `denial_reason` and redacted `execution_result` fields.
- [x] 1.2 Read only the latest immutable rejection event, returning null when
  the optional event lookup is absent or unavailable.

## 2. Dossier outcome rendering

- [x] 2.1 Extend frontend approval-detail types and render retained decision
  provenance, denial reason, and a safely redacted execution outcome.
- [x] 2.2 Render dossier Retry only for an approved detail with a null
  execution result.

## 3. Regression protection and verification

- [x] 3.1 Add focused API regressions for event-derived denial reasons,
  redaction, and safe degraded-pool behavior.
- [x] 3.2 Add focused dossier regressions for outcome rendering and exact Retry
  eligibility.
- [x] 3.3 Run strict OpenSpec validation and the risk-scaled backend/frontend
  quality gates.
