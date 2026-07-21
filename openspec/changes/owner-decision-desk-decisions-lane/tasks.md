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

## 4. Activation (bu-hmdqz.6 addendum)

Move 6 of the 2026-07-12 JARVIS pursuit found this capability shipped but
structurally inert in the containerized topology -- see
`docs/redesigns/2026-07-12-jarvis-pursuit.md` §6. This addendum closes that
gap; it does not change the scope above.

- [x] 4.1 `docker-compose.yml`: mount `.beads/issues.export.jsonl:ro` into
  `dashboard-api`/`dashboard-api-hotreload` (previously only `butlers-up`/
  `butlers-up-hotreload` had it, so `GET /api/decisions` was permanently
  `decisions_available=false` once deployed)
- [x] 4.2 `butlers.core.deploy.materialize_beads_export`: refresh the export
  on the deploy host before `recreate_services`, so a snapshot-worktree
  deploy never binds a missing/stale file
- [x] 4.3 `compute_decision_digest()` / `GET /api/decisions`: `export_as_of`
  meta field (export mtime) + `/decisions` page as-of plaque
- [x] 4.4 `scripts/lint_decision_beads.py --check-unlabeled-markers` +
  weekly `decision_review` job wiring, so the convention lint is non-vacuous
  against the live queue (documented in AGENTS.md, not a dashboard-facing
  capability -- no spec delta)

## 5. bu-kqnum.9.2 — Scheduled lint live-candidate scope

- [x] 5.1 Add the Dashboard API delta requirement covering live-status
  selection for scheduled full-export marker linting and the forensic-mode
  exceptions.
- [x] 5.2 Add RED regression coverage for labeled and unlabeled closed records,
  explicit-ID/all-status forensic behavior, and the real `decision_review`
  subprocess path.
- [x] 5.3 Apply the minimal strict-selector/help change, verify the focused
  suites and OpenSpec validation, and preserve unavailable/error behavior.
