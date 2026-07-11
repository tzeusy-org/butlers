# Tasks — owner-decision-desk-decisions-lane

## 1. Backend

- [x] 1.1 `DecisionBeadSummary` model (`src/butlers/api/models/decision.py`)
- [x] 1.2 `GET /api/decisions` router reusing `compute_decision_digest()`, degraded-envelope `meta.decisions_available`
- [x] 1.3 Register router in `src/butlers/api/app.py`
- [x] 1.4 Tests: degraded envelope, genuine-empty all-clear, oldest-first ordering + age_hours, escalation fields at/under the 48h threshold

## 2. Frontend

- [x] 2.1 API types (`DecisionBeadSummary`, `DecisionsListMeta`, `DecisionsListResponse`) + client (`getDecisions`) + `useDecisions` hook
- [x] 2.2 `DecisionsVerdictOpener` ("N decisions waiting, oldest Xd" + escalation clause, degraded-envelope honored)
- [x] 2.3 `DecisionsPage` (`/decisions`): verdict opener, row list, j/k roving selection via `useListTriage`, selected-row-expands-inline detail
- [x] 2.4 Nav registration: sidebar entry, icon, `decisions-open` badge count
- [x] 2.5 Tests: verdict opener, page (list/degraded/empty/j-k selection), a11y sweep coverage entry

## 3. Spec

- [x] 3.1 This proposal + `dashboard-decisions` capability spec + `dashboard-api` delta
