# QA Dossier Redesign Report

**Epic:** bu-gt2o0 — Redesign QA dossier (OpenSpec: redesign-qa-dossier)
**Reconciliation bead:** bu-f3ygx (gen-1 top-level)
**Date:** 2026-05-16
**Auditor:** beads-worker (gen-1 top-level reconciliation)

---

## What Shipped

All core capabilities from the OpenSpec change landed across 40+ beads and PRs from 2026-04-25
through 2026-05-16. Every numbered item below maps to the delivering bead and primary files.

### Database: Journal Table (D1)

- **bu-v1n04** — `alembic/versions/core/core_091_qa_investigation_events.py`
  Alembic migration adds `public.qa_investigation_events` with UUIDv7 PK, composite index on
  `(attempt_id, ts)`, step CHECK constraint, and `idx_qa_inv_events_step` secondary index.

### Backend Core — Investigation Notes (D2, D3)

- **bu-zrsv3** — `src/butlers/core/qa/notes.py`
  `InvestigationNotes` Pydantic model with strict validation; `parse_investigation_notes()` with
  three-way `ok/partial/failed` status; field-level `TypeAdapter` fallback extraction.

- **bu-epk0r** — `src/butlers/core/qa/dispatch.py`
  Dispatcher reads `./.qa/investigation_notes.json` before worktree teardown, parses via
  `parse_investigation_notes()`, and persists the result into
  `qa_findings.structured_evidence.investigation_notes`.

- **bu-yaytq** — Investigation agent prompt extended to instruct emission of
  `./.qa/investigation_notes.json`; `considered` and `concluded` journal events emitted from the
  agent step.

### Backend Core — Diff Snapshot (D4)

- **bu-yodrf** — `src/butlers/core/qa/dispatch.py`
  `_capture_commit_diff_snapshot()` runs `git diff --no-color HEAD~1..HEAD --unified=3`; truncates
  at 10 000 lines with a `meta` marker; writes into `investigation_notes.diff_snapshot` via
  `_persist_diff_snapshot()`.

### Backend Core — Anonymization Invariant (D5)

- **bu-u4arr** — `tests/core/qa/test_anonymization_boundary.py`
  Unit test asserts every `gh pr create` / `git commit -m` code path calls `anonymize()` before
  constructing arguments; no path passes `evidence_lines` to PR-bound content.

- **bu-sopj0** — `src/butlers/core/qa/dispatch.py`
  `validate_anonymized()` failure path implemented: transitions attempt to `anonymization_failed`,
  deletes the remote branch, and increments `qa_anonymization_failed_total` counter.

### Backend Core — Retention Cleanup (D6)

- **bu-mfb39** — `src/butlers/modules/qa/__init__.py`
  Daily cleanup job at `retention_cleanup_hour` (default 04:00 UTC); strips
  `investigation_notes.evidence_lines[]` from findings where linked attempt is terminal +14d, or
  finding has no linked attempt and `created_at < now() - 30d`; skips malformed JSONB rows with
  WARNING log.

### Backend API — KPI Extension (D7)

- **bu-c3p06** — `src/butlers/api/routers/qa.py`
  `/api/qa/summary` extended with `kpis: QaKpiBlock` (`prs_landed_24h`, `mttr_24h_seconds`,
  `self_resolved_7d_pct`, `active_cases_now`) and `active_breakdown: QaActiveBreakdown`
  (`awaiting_ci`, `escalated_open_cases`).

- **bu-omeqv** — `src/butlers/core/qa/severity.py`
  `failed_with_human_action()` canonical helper; single ILIKE-pattern detector for escalated cases.

- **bu-0qm9n** — PR #1671: `escalated` → `escalated_open_cases` rename; `active_breakdown`
  rewired to use the canonical helper.

- **bu-wpa6x** — PR #1672: `state_of_case()` rewritten to call `failed_with_human_action()` and
  drop the `drafted` pseudo-state.

### Backend API — Cases Resource (D8)

- **bu-cdp24** — `src/butlers/api/routers/qa.py` — `GET /api/qa/cases` list endpoint.
- **bu-z34mk** — `GET /api/qa/cases/:id` dossier endpoint and `GET /api/qa/cases/:id/journal`
  paginated journal endpoint.
- **bu-do4op** — `tests/api/test_api_qa_cases.py` — integration test coverage.
- **bu-8asz1** — Server-side `state` and `butler` filters on the cases list.
- **bu-61aor** — PR #1670: dismissal surface added to Cases API (`QaActiveDismissal`); `GET
  /api/qa/cases/:id` includes active dismissal; `DELETE /api/qa/dismissals/:fingerprint` wired.

### Backend API — Severity and State Helpers (D8)

- **bu-a96av** — `src/butlers/core/qa/severity.py` / `src/butlers/core/qa/models.py`
  `severity_label()` (0–1→high, 2→medium, 3–4→low) and `short_id_from_uuid()` helpers.

### Journal Emission (D10)

- **bu-ofcu3** — `src/butlers/core/qa/journal.py` + `src/butlers/core/qa/triage.py`
  `record_event()` helper; `flagged` event emitted on novel finding dispatch.

- **bu-v9qc3** — PR #1658: `drafted`, `wait`, `merged`, `escalated` events from dispatch/PR
  poller.

- **bu-72opw** — `src/butlers/modules/qa/__init__.py`
  `tick` events emitted per patrol cycle for `investigating` and `pr_open` attempts only
  (excluding `dispatch_pending` per D10 scope).

- **bu-h2nyt.1** — `drafted` event timestamp backdate fix.
- **bu-h2nyt.2** — `tick` emission scope narrowed to `investigating` and `pr_open` per D10.

### RFC 0015 Doctrine Update (D11)

- **bu-g9jxv** — `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md`
  Non-Goals updated to the anonymization-on-egress doctrine. Retention bullet added under D7.

### Frontend Components (D9)

- **bu-f9hmg** — PR #1663: `QaKpiStrip.tsx`, `CaseList.tsx`, `CaseDossierHeader.tsx`,
  `StateTrack.tsx` atoms.
- **bu-4iwle** — `ClaimAnchoredBlurb.tsx`, `EvidenceLog.tsx` (bidirectional hover linkage).
- **bu-fxf19** — PR #1674: `CounterEvidence.tsx`, `PRPanel.tsx`, `DiffPreview.tsx`.
- **bu-9lo5u** — PR #1677: `PatrolJournal.tsx`, `CaseDossier.tsx` composition.
- **bu-cavyk** — `frontend/src/api/types.ts` Cases API types + TanStack Query hooks in
  `frontend/src/hooks/use-qa.ts`.

### Frontend Pages (D9)

- **bu-21uf7** — PR #1680: `QaOverviewPage.tsx` rewritten as dossier shell.
- **bu-q397p** — `QaInvestigationsPage.tsx` rewritten as Dispatch case index.
- **bu-e5zne** — PR #1681: `QaInvestigationDetailPage.tsx` + `QaPatrolDetailPage.tsx` rewritten.
- **bu-r5i9o** — PR #1684: recharts imports swept; dead code deleted.
- **bu-yblmf** — Home Page QA widget in Dispatch language.

### Post-G11/G12 Gap Fixes

All gaps identified in the G11 (bu-otub9) and G12 (bu-aopqt) reconciliation passes were
materialized as child beads under bu-1gi20 and closed:

| Gap | Bead | PR |
|---|---|---|
| Live clock missing from QaOverviewPage header | bu-5tuzd | merged to main |
| Port + model missing from header caption | bu-1gi20.1 | PR #1688 |
| KPI delta sub-labels with prior-period comparison | bu-1gi20.2 | PR #1686 |
| Retry + Dismiss pill buttons in CaseDossierHeader | bu-1gi20.3 | PR #1687 |
| Double-hash `##NNN` short_id prefix | bu-1gi20.4 | merged to main |
| PRPanel `why_this_fix` serif italic 13px | bu-1gi20.5 | merged to main |
| PRPanel serif/italic dossier.test.tsx snapshot | bu-ct6qa | merged to main |

### Observability

- **bu-w116y** — `src/butlers/modules/qa/__init__.py` + `src/butlers/core/qa/dispatch.py`
  OTel attributes + Prometheus counters wired.

### Docs Polish

- **bu-s32cb** — `roster/qa/MANIFESTO.md`, `CLAUDE.md`, `about/` lay-and-land docs updated to
  reflect the dossier surface.

---

## What Slipped

### bu-w14jj — End-to-End Validation (deferred to human)

The `bu-w14jj` bead (Run end-to-end validation against `/butlers-dev/qa`) is intentionally
deferred. It requires a live QA staffer instance with real patrol data, a forced investigation
that reaches `merged`, and visual inspection of the dossier on
`tzeusy.parrot-hen.ts.net/butlers-dev/qa`. This cannot be executed by an automated agent without
a wired dev environment. The bead is open and queued for human execution when the dev instance is
available.

All automated quality gates (lint, format, pytest) are green. The functional code is complete.

---

## Deviations from the OpenSpec Change

### SQL for `escalated_open_cases` (D7)

Design.md §D7 quoted the ILIKE query inline. Shipped implementation in
`src/butlers/core/qa/severity.py` extracts this to `failed_with_human_action()`, then both the
KPI sub-label and `state_of_case()` call the helper. This is a refinement over the spec's inline
pattern and is strictly better (single canonical detector, documented substrings in docstring).

### `state_of_case()` removes `drafted` pseudo-state (D8)

The original `state_of_case()` in the shipped `qa.py` included `drafted` as a pseudo-state that
was never produced by the status machine (no `healing_attempts.status = 'drafted'` exists). PR
#1672 (bu-wpa6x) cleaned this up. The spec's `QaCaseSummary.state` literal never listed
`drafted`, so this is alignment to spec.

### PR State Chip `rejected` variant removed (G11-GAP-8, resolved)

The G11 reconciliation (bu-otub9) identified that spec.md listed a `rejected` state chip variant
not produced by the backend. After investigation, `rejected` is not a real GitHub PR merge state.
The spec was corrected in `openspec/changes/redesign-qa-dossier/specs/qa-dashboard/spec.md`
(removed from the chip variant list). Tests for all 4 valid states (`drafted|open|merged|closed`)
added.

### KPI prior-period comparison sub-labels

The initial spec examples (`+2 vs prior 24h`, `−12m vs 7d`, `+4pp vs prior week`) implied
prior-period delta. The first implementation (bu-f9hmg / bu-c3p06) shipped static strings
(`"24h window"`, etc.) without prior-period data. Gap materialized as bu-1gi20.2 (PR #1686),
which added `prs_landed_prior_24h`, `mttr_prior_7d_seconds`, `self_resolved_prior_7d_pct` to
`QaKpiBlock` and wired the delta sub-labels. Spec intent is now fully met.

### Frontend headline fallback shows "Untitled QA case" (minor, below gap threshold)

`CaseDossierHeader.tsx` and `CaseList.tsx` fall back to `"Untitled QA case"` when `headline` is
null. The spec says fall back to `event_summary`. The backend `headline_for_case()` always returns
a non-null string, so the null path is unreachable in production. Documented as a minor issue in
RECONCILE-G11.md; no separate bead created because the code path is dead in practice.

### EvidenceLog column order (minor cosmetic)

Spec lists evidence grid column order as `ts, level, butler, msg, [N]` (claims last). Shipped
implementation puts claim numbers in the first 20px column. Functionally equivalent; documented in
RECONCILE-G11.md as a minor cosmetic deviation; no gap bead created.

### `formatQaDetectedTime` vs `<Time>` primitive (minor)

Two inline `toLocaleTimeString()` calls in `utils.ts` and `PatrolJournal.tsx`. Design doctrine
says all timestamps use `<Time>`. Output is correct; this is a minor convention deviation.
Documented in RECONCILE-G11.md.

---

## Observability Checklist

| Metric | File | Status |
|---|---|---|
| `qa_investigation_notes_parse_total{status=ok\|partial\|failed}` | `src/butlers/core/qa/dispatch.py` | Implemented (bu-w116y + bu-epk0r) |
| `qa_findings_retention_purged_total` | `src/butlers/modules/qa/__init__.py` | Implemented (bu-mfb39) |
| `qa_anonymization_failed_total` | `src/butlers/core/qa/dispatch.py` | Implemented (bu-sopj0) |

First-week alert threshold: `qa_investigation_notes_parse_total{status="failed"}` >10% rate over
24h triggers review (documented in design.md Risks section). The counters are declared with
`prometheus_client.Counter` and guarded against import failure so non-observability deployments
still function. The retention counter increments per-row-batch on each cleanup run.

---

## End-to-End Validation Result

`bu-w14jj` is deferred to human operator. Automated validation is not possible without a live
`/butlers-dev/qa` instance with real data.

Per the design's Open Questions:

- `sampled` and `cross-checked` journal events remain optional in v1; no operator has requested
  them yet.
- `/api/qa/cases/:id/journal` ships as polling (patrol cadence); SSE deferred per design
  decision.
- `headline` fallback to `event_summary` is implemented in the backend
  (`headline_for_case()`); the frontend shows `"Untitled QA case"` as a secondary safety net
  (dead code path in practice).

When `bu-w14jj` is run, the validation target is:
1. Forced patrol produces `flagged` + `tick` events visible in PatrolJournal.
2. A real investigation reaches `merged` with populated `investigation_notes` (headline,
   evidence_lines, diff_snapshot visible in dossier).
3. KPI strip shows non-zero values; prior-period deltas render correctly.
4. Case rail, case selection, URL-driven navigation (`?case=`) all function.
5. Retry and Dismiss pills function from the dossier header.

---

## Next Steps

### Deferred (no gap bead required)

- **bu-w14jj** — End-to-end validation against `/butlers-dev/qa` (human operator task).
- SSE for `/api/qa/cases/:id/journal` — deferred per design; polling at patrol cadence is
  acceptable for v1.
- `sampled` / `cross-checked` journal events from triage — optional per D10; deferred unless
  operators request.
- Backfill `investigation_notes` for pre-redesign historical attempts — explicitly listed as a
  Non-Goal.

### Newly identified minor items (below bead threshold, fix alongside related work)

These were documented in RECONCILE-G11.md and do not require standalone beads:

1. `EvidenceLog.tsx:80` — claim `[N]` column appears first; spec lists it last. Cosmetic only.
2. `PatrolJournal.tsx:18` — dead `opened` entry in `stepClassName` (not a valid journal step).
3. `QaInvestigationsPage.tsx:198` — `font-semibold` (600) should be `font-medium` (500).
4. `EvidenceLog.tsx:81`, `ClaimAnchoredBlurb.tsx:61` — hardcoded OKLCH values outside token
   system; consider a named token or `color-mix`.
5. `PRPanel.tsx:29` — em-dash in "No PR — escalated to user." (voice doctrine bans em-dashes in
   prose); spec itself contains the em-dash so this requires a coordinated spec + code fix.
6. `utils.ts:9-13`, `PatrolJournal.tsx:24-28` — inline `toLocaleTimeString()` instead of
   `<Time>` primitive.

### OpenSpec sync status

Part 3 (opsx:sync and archive) is deferred. Gaps G11-minor-1 through G11-minor-6 above are
below-threshold cosmetic items, not blocking functional gaps. However, the coordinator should
decide whether to archive the OpenSpec change now with those minors documented, or land a polish
pass first. This report recommends archiving: the minors have been fully documented in
RECONCILE-G11.md and do not affect operator function.

---

## Spec-to-Code Coverage Summary

### qa-dashboard/spec.md

| Requirement group | Scenarios | Covered | Notes |
|---|---|---|---|
| QA Overview Page | 4 scenarios | 4/4 | Clock gap and port gap resolved by bu-5tuzd, bu-1gi20.1 |
| Patrol Detail Page | 2 scenarios | 2/2 | |
| Investigation Detail Page | 2 scenarios | 2/2 | |
| Home Page QA Widget | 2 scenarios | 2/2 | bu-yblmf |
| Navigation Integration | 2 scenarios | 2/2 | |
| Case Dossier Layout | 4 scenarios | 4/4 | Retry/Dismiss gap resolved by bu-1gi20.3 |
| QA Cases API | 4 scenarios | 4/4 | bu-cdp24, bu-z34mk, bu-do4op, RFC 0007 envelope drift fixed bu-9otu9.1 |
| QA Summary KPI Extension | 1 scenario | 1/1 | bu-c3p06, bu-omeqv, bu-0qm9n |
| Investigations List Page | 2 scenarios | 2/2 | bu-q397p |
| Known Issues Tracker (REMOVED) | — | n/a | Folded into Case Dossier per spec |
| State Persistence (REMOVED) | — | n/a | Replaced by per-case dismissal action |

### qa-investigation-dispatch/spec.md

| Requirement group | Scenarios | Covered | Notes |
|---|---|---|---|
| Investigation Notes Artifact | 3 scenarios | 3/3 | bu-zrsv3, bu-epk0r, bu-yaytq, bu-cd8cb recon |
| Commit-Time Diff Snapshot | 3 scenarios | 3/3 | bu-yodrf |
| Journal Event Emission — Dispatch | 4 scenarios | 4/4 | bu-v9qc3, bu-h2nyt.1 |
| Raw Log Retention and Cleanup | 3 scenarios | 3/3 | bu-mfb39 |
| Anonymization-on-Egress Guarantee | 2 scenarios | 2/2 | bu-u4arr, bu-sopj0 |

### qa-triage/spec.md

| Requirement group | Scenarios | Covered | Notes |
|---|---|---|---|
| Journal Event Emission — Triage | 4 scenarios | 3/4 | `flagged` covered (bu-ofcu3); `sampled` and `cross-checked` are optional per spec and intentionally not implemented in v1 |

### Design Decisions D1–D11

| Decision | Status | Bead |
|---|---|---|
| D1 — Journal storage: new table | PASS | bu-v1n04 |
| D2 — Investigation Notes Artifact: structured_evidence JSONB | PASS | bu-zrsv3, bu-epk0r |
| D3 — Agent emission via portable file contract | PASS | bu-zrsv3, bu-yaytq, bu-epk0r |
| D4 — Diff snapshot at commit time | PASS | bu-yodrf |
| D5 — Anonymization invariant confirmed and instrumented | PASS | bu-u4arr, bu-sopj0 |
| D6 — Retention: 30d/14d terminal, daily cleanup | PASS | bu-mfb39 |
| D7 — KPI definitions | PASS | bu-c3p06, bu-omeqv, bu-0qm9n, bu-1gi20.2 |
| D8 — Cases resource shape | PASS | bu-cdp24, bu-z34mk, bu-a96av, bu-8asz1, bu-61aor |
| D9 — Frontend component structure | PASS | bu-f9hmg, bu-4iwle, bu-fxf19, bu-9lo5u, bu-21uf7, bu-q397p, bu-e5zne |
| D10 — Journal event emitters | PASS | bu-ofcu3, bu-v9qc3, bu-72opw, bu-yaytq (sampled/cross-checked optional, intentionally omitted in v1) |
| D11 — Doctrine update to RFC 0015 | PASS | bu-g9jxv |

**Overall coverage: 100% of required scenarios; 0 functional gaps remaining.**

The three optional v1 scenarios (`sampled`, `cross-checked` from triage, SSE for journal) are
intentionally deferred per the spec's own language ("v1 implementations are explicitly permitted
to omit these").
