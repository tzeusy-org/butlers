## Why

The current `/qa` page is a SaaS-style operator dashboard (status banner, raw-count KPI cards, Kanban pipeline, recharts trend + source-breakdown, recent-patrols table) and predates the Dispatch design language adopted by `/overview` and the Butler-detail pages. It surfaces *that* QA is running but not *what the staff caught and fixed* — the dossier-grade narrative (claim-anchored diagnosis, evidence anchored to claims, the patrol's decision journal, the proposed fix with inline diff, the human-readable "why this fix") that operators actually read on a quiet afternoon. The QA staffer already has most of the raw material (findings, fingerprints, healing attempts, PR URLs, dispatch events) but produces none of the structured per-case prose that a dossier needs. This change reshapes the surface to match the rest of the system and extends the capture layer just enough to feed it.

## What Changes

- **BREAKING (UI)**: hard cut of `/qa`, `/qa/investigations`, `/qa/investigations/:attemptId`, and `/qa/patrols/:patrolId`. The Kanban + recharts dashboard is replaced by a dossier shell: page header, 4-cell KPI strip (`prs landed · 24h`, `mttr · 24h`, `self-resolved · 7d`, `active cases · now`), 320 px case-list rail, and a dossier body (sev/id/butler/detected header + state track, H2 headline, two columns of diagnosis + PR panel, full-width patrol journal). Same Dispatch language applies to the sibling pages.
- **Doctrine update (RFC 0015 §Non-Goals)**: remove "QA does NOT store raw log lines in `qa_findings`". Replace with: raw log lines may be stored in `qa_findings.structured_evidence.evidence_lines[]` for use on the private dashboard only; all GitHub-egress paths (PR title, PR body, branch commit messages, quoted lines) must still pass through `anonymize()` + `validate_anonymized()`.
- **Retention policy**: raw log lines in `qa_findings` are purged after 30 days. Cases still in a non-terminal state are exempt until 14 days after their terminal transition. A daily cleanup job enforces this.
- **Agent contract (qa-investigation-dispatch)**: the investigation agent must emit a structured `investigation_notes` JSON artifact at terminal state, containing `headline`, `hypothesis`, `blurb_segments` (anchored prose), `claims` (claim_id → evidence_ids, note), `evidence_lines[]`, `counter_evidence[]`, `why_this_fix`, and `diff_snapshot[]`. Diff snapshot is captured at commit time inside the worktree (one `git diff --no-color HEAD~1`) so it survives PR force-pushes.
- **Patrol journal**: new table `public.qa_investigation_events` (attempt_id FK, finding_id FK nullable, ts, step ∈ {`flagged`, `sampled`, `cross-checked`, `considered`, `concluded`, `drafted`, `wait`, `merged`, `tick`, `escalated`}, text, detail, data JSONB). `flagged` is emitted by triage on novel finding; `sampled` / `cross-checked` / `considered` / `concluded` are emitted by the investigation agent; `drafted` / `wait` / `merged` / `escalated` are emitted by the dispatch layer; `tick` is emitted by the patrol loop when an open case is re-checked but nothing changed.
- **API additions** (qa-dashboard):
  - `GET /api/qa/cases` — case-list resource for the rail (id, sev, butler, headline, detected, age, state, pr_state).
  - `GET /api/qa/cases/:id` — full dossier payload (case meta + `investigation_notes` + PR summary + recent journal events).
  - `GET /api/qa/cases/:id/journal` — paginated journal stream (optional SSE for live `tick` updates).
  - `GET /api/qa/summary` extended with `kpis: { prs_landed_24h, mttr_24h_seconds, self_resolved_7d_pct, active_cases_now }`.
- **API removals/replacements** (qa-dashboard): the existing summary fields stay (back-compat consumers), but the page no longer renders Kanban columns, the success-rate area chart, the source-breakdown bar chart, or the recent-patrols table. `useQaTrends` is no longer consumed from `/qa` (still served, in case it's reused elsewhere).
- **Frontend tokens**: no new tokens. Inter Tight / JetBrains Mono / Source Serif 4 and the OKLCH palette in `frontend/src/index.css` are already shipped; the redesign only consumes them.

## Capabilities

### New Capabilities

_None._ The redesign sits inside the existing QA capability surface.

### Modified Capabilities

- `qa-dashboard`: replace the Overview Page requirement with a dossier-layout scenario; add Cases API (cases-list, case-detail, case-journal) and KPI extensions to `/api/qa/summary`; redesign the Patrol Detail, Investigation Detail, and Investigations List pages to Dispatch language; sidebar entry unchanged.
- `qa-investigation-dispatch`: add Investigation Notes Artifact requirement (agent emission schema, persistence, anonymization-on-egress guarantee); add commit-time diff-snapshot capture; add raw-log retention and cleanup-job requirements; add journal-event emission contract for the dispatch-owned step kinds (`drafted`, `wait`, `merged`, `escalated`).
- `qa-triage`: add journal-event emission contract for the triage-owned step kinds (`flagged`; `sampled` and `cross-checked` permitted when triage performs cross-source corroboration).

## Impact

**Doctrine**
- `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md` — Non-Goals list edited; retention + anonymization-on-egress clauses added under §D4 and §D7.

**Database (Alembic migrations)**
- New migration: `public.qa_investigation_events` table with composite index on `(attempt_id, ts)`.
- No schema migration for `qa_findings`; `structured_evidence` JSONB is extended in shape only (documented in design.md). A separate, much shorter migration adds a `terminal_at` timestamp column or relies on `healing_attempts.closed_at` for the retention join — final choice in design.md.

**Backend (Python)**
- `src/butlers/core/qa/dispatch.py` — agent prompt update; structured-output parsing of `investigation_notes`; diff capture inside worktree before teardown; emission of dispatch-owned journal events.
- `src/butlers/core/qa/triage.py` — emit `flagged` events; optional `sampled` / `cross-checked` when corroborating across sources.
- `src/butlers/modules/qa/__init__.py` — patrol loop emits `tick` events for re-checked-but-unchanged open cases; new daily cleanup job for log retention; PR-state poller writes `merged` / `wait` events.
- `src/butlers/api/routers/qa.py` — three new endpoints (`/cases`, `/cases/:id`, `/cases/:id/journal`); KPI block added to `/summary`. Existing endpoints unchanged.
- `src/butlers/core/qa/anonymizer.py` (or wherever the anonymize pipeline lives) — confirm GitHub-egress paths route through `anonymize()`; add a unit-level guard that PR-bound payloads cannot embed `evidence_lines[].msg` raw.

**Frontend (Vite + React 18 + React Router v7)**
- `frontend/src/pages/QaOverviewPage.tsx` — rewritten as the dossier shell.
- `frontend/src/pages/QaInvestigationsPage.tsx` — rewritten as a Dispatch-language case index (rule-separated rows, no Kanban).
- `frontend/src/pages/QaInvestigationDetailPage.tsx` — rewritten as the same dossier component that `/qa?case=` mounts, accessed by attempt id.
- `frontend/src/pages/QaPatrolDetailPage.tsx` — rewritten with the same hairline-row + eyebrow vocabulary; findings table becomes a rule-separated list.
- `frontend/src/components/qa/` — new component directory: `CaseList`, `CaseDossierHeader`, `StateTrack`, `ClaimAnchoredBlurb`, `EvidenceLog`, `CounterEvidence`, `PRPanel`, `DiffPreview`, `PatrolJournal`, `QaKpiStrip`.
- `frontend/src/hooks/use-qa.ts` — new hooks: `useQaCases`, `useQaCase(id)`, `useQaCaseJournal(id)`; KPIs surfaced from extended `useQaSummary`.
- `frontend/src/api/types.ts` — new types: `QaCaseSummary`, `QaCaseDossier`, `QaInvestigationNotes`, `QaJournalEvent`, `QaKpiBlock`.
- `frontend/src/api/index.ts` (or wherever the client lives) — three new endpoint methods.

**Tests**
- New tests under `tests/api/test_api_qa_cases.py` for the new endpoints.
- New tests under `tests/core/qa/test_investigation_notes.py` for agent-emission parsing.
- New tests under `tests/core/qa/test_journal.py` for journal-event invariants (append-only, valid step values).
- New tests under `tests/core/qa/test_retention.py` for the cleanup job (deletes after 30 days; exempts open cases).
- Component tests for the claim-anchor hover linkage in `frontend/src/components/qa/`.

**Observability**
- New OTel attributes on `qa.investigation` span: `qa.notes_emitted` (bool), `qa.notes_parse_status` (`ok` / `partial` / `failed`).
- New Prometheus metric: `qa_investigation_notes_parse_total{status}`.
- `qa_findings_retention_purged_total` counter on the cleanup job.

**Out of scope (deferred)**
- Live SSE for the case journal (polling at the patrol cadence is acceptable for v1; SSE is an optional follow-up).
- Backfilling `investigation_notes` for historical attempts (older cases simply render in a degraded state with the fields they have).
- Notification system integration for `escalated` events (Proactive Butler's domain per RFC 0015).
