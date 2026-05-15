## 1. Doctrine update (RFC 0015)

- [ ] 1.1 Edit `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md` Non-Goals: replace the "QA does NOT store raw log lines" bullet with the egress-bounded version (per design.md §D11)
- [ ] 1.2 Add the Retention bullet under §D7 (raw lines purged after 30 days; non-terminal cases exempt until terminal+14d; narrative preserved indefinitely)
- [ ] 1.3 Land 1.1 + 1.2 in the same PR as this OpenSpec change

## 2. Database — qa_investigation_events table

- [ ] 2.1 Write Alembic migration creating `public.qa_investigation_events` per design.md §D1 (columns, check constraint on `step`, composite index `(attempt_id, ts)`, single-column index on `step`)
- [ ] 2.2 Add migration regression test verifying check constraint rejects unknown step values
- [ ] 2.3 Add migration regression test verifying ON DELETE CASCADE from `healing_attempts` cleans up events

## 3. Journal helper + emission sites

- [ ] 3.1 Create `src/butlers/core/qa/journal.py` with `record_event(session, attempt_id, step, text, detail=None, data=None, finding_id=None, ts=None)` helper
- [ ] 3.2 Wire `flagged` emission into `src/butlers/core/qa/triage.py` (post novelty-claim success path)
- [ ] 3.3 Wire `drafted` emission into `src/butlers/core/qa/dispatch.py` (PR creation success path)
- [ ] 3.4 Wire `wait` emission into the PR status poller (per-cycle deduplication: at most one wait event per attempt per patrol cycle)
- [ ] 3.5 Wire `merged` emission into the PR status poller (on `pr_open → pr_merged` transition)
- [ ] 3.6 Wire `escalated` emission into `dispatch.py` (on `unfixable` or `failed` with human-action `error_detail`)
- [ ] 3.7 Wire `tick` emission into the patrol loop in `src/butlers/modules/qa/__init__.py` (one event per attempt in `investigating` or `pr_open` per patrol cycle when no other events fired this cycle; `dispatch_pending` is intentionally excluded per design.md §D10)
- [ ] 3.8 Unit tests for each emission path (`tests/core/qa/test_journal.py`): asserts row appears with expected `step`, `text` shape, and `attempt_id` linkage

## 4. Investigation Notes Artifact contract

- [ ] 4.1 Define `InvestigationNotes` Pydantic model in `src/butlers/core/qa/notes.py` per design.md §D2 (schema_version=1, headline, hypothesis, blurb_segments, claims, evidence_lines, counter_evidence, why_this_fix, diff_snapshot)
- [ ] 4.2 Add `BlurbSegment`, `Claim`, `EvidenceLine`, `CounterEvidenceItem`, `DiffLine` sub-models with strict validation
- [ ] 4.3 Implement `parse_investigation_notes(raw: str) -> tuple[InvestigationNotes | None, Literal["ok","partial","failed"]]` — strict parse first, fall back to per-field best-effort extraction on failure
- [ ] 4.4 Update the investigation agent prompt in `src/butlers/core/qa/dispatch.py` (or wherever the prompt is composed) to instruct emission of `./.qa/investigation_notes.json` with the documented schema, plus a one-paragraph rationale and example
- [ ] 4.5 Use the portable file contract for every runtime: prompt the agent to write plain JSON to `./.qa/investigation_notes.json`, then rely on dispatcher validation/tolerant parsing; do not require Claude final-response structured-output mode unless the runtime adapter gains an artifact-file schema channel
- [ ] 4.6 In the dispatcher's terminal handler, read `./.qa/investigation_notes.json` before worktree teardown, parse via 4.3, and persist into `qa_findings.structured_evidence.investigation_notes` (for every finding linked to the attempt)
- [ ] 4.7 Add Prometheus counter `qa_investigation_notes_parse_total{status}` (status ∈ {ok, partial, failed}); increment per parse attempt
- [ ] 4.8 Emit `considered` and `concluded` journal events from the agent path: for each `counter_evidence[]` entry insert a `considered` event; insert exactly one `concluded` event with the agent's hypothesis when notes are successfully parsed
- [ ] 4.9 Unit tests in `tests/core/qa/test_investigation_notes.py`: full parse, partial parse with one missing optional field, total parse failure with garbage input, schema_version mismatch handling

## 5. Commit-time diff snapshot

- [ ] 5.1 In `dispatch.py`'s commit-success path, run `git -C <worktree> diff --no-color HEAD~1..HEAD --unified=3` and capture stdout
- [ ] 5.2 Implement `parse_unified_diff(text: str, max_lines: int = 10_000) -> list[DiffLine]` in `src/butlers/core/qa/diff.py` returning `{kind, text}` rows; classify `diff --git`, `index ...`, `---`, `+++`, `@@ ... @@` as `kind = "meta"`; `+`/`-`/` ` as their literal kinds
- [ ] 5.3 On overflow, truncate at 10 000 lines and append `{kind: "meta", text: "... (truncated, N more lines)"}`
- [ ] 5.4 Write the parsed snapshot into `investigation_notes.diff_snapshot` for the corresponding finding(s)
- [ ] 5.5 Handle the unfixable-no-commit case: write `diff_snapshot: []`
- [ ] 5.6 Unit tests in `tests/core/qa/test_diff.py`: parses a representative diff, truncates correctly, handles empty input

## 6. Anonymization-on-egress guarantees

- [ ] 6.1 Audit every code path in `dispatch.py` that composes PR title, PR body, or `git commit -m` content; confirm each passes through `anonymize()` immediately prior and is gated by `validate_anonymized()` for PR-bound content
- [ ] 6.2 Confirm no PR-construction call accepts `evidence_lines` (or any alias) as a parameter; remove any code path that would let one leak through
- [ ] 6.3 New test `tests/core/qa/test_anonymization_boundary.py`: import the dispatch module, walk every function that calls `gh pr create` or `git commit -m`, and assert each is preceded by an `anonymize()` call (use AST inspection or call-graph traversal)
- [ ] 6.4 Add an `evidence_lines` blocklist: a small static check (e.g. an explicit `assert "evidence_lines" not in pr_body` in the PR-creation function) that fails loudly if the field name appears anywhere in PR-bound payloads
- [ ] 6.5 Implement the `validate_anonymized()` failure path per qa-investigation-dispatch spec: when validation rejects PR-bound content, delete the remote branch (`git push origin --delete <branch>` or equivalent `gh` call) and transition the attempt to a new `anonymization_failed` terminal status (extend the `healing_attempts.status` enum if absent; otherwise reuse `failed` with an explicit `error_detail.anonymization_failed = true` marker). Add a Prometheus counter `qa_anonymization_failed_total` incremented per occurrence
- [ ] 6.6 Integration test simulating a `validate_anonymized()` failure: asserts (a) remote branch deletion was attempted, (b) attempt status reflects the failure, (c) counter incremented, (d) no PR was opened

## 7. Retention cleanup job

- [ ] 7.1 Implement `daily_evidence_cleanup()` in `src/butlers/modules/qa/__init__.py` performing the SQL from design.md §D6
- [ ] 7.2 Schedule the job via the QA module scheduler at `[modules.qa].retention_cleanup_hour` (default 04:00 UTC); add the config key to `roster/qa/butler.toml` with a comment
- [ ] 7.3 Increment `qa_findings_retention_purged_total` counter by row-count per run
- [ ] 7.4 Emit `WARNING` log lines for rows with malformed `structured_evidence.investigation_notes` shape; skip those rows but continue the run
- [ ] 7.5 Tests in `tests/core/qa/test_retention.py`: (a) deletes evidence_lines for findings older than 30d, (b) exempts findings whose linked attempt is still non-terminal, (c) preserves narrative fields for cleaned rows, (d) handles malformed JSONB without crashing

## 8. KPI extension on /api/qa/summary

- [ ] 8.1 Add `QaKpiBlock` Pydantic model in `src/butlers/api/routers/qa.py` with fields `prs_landed_24h`, `mttr_24h_seconds: int | None`, `self_resolved_7d_pct: float`, `active_cases_now`
- [ ] 8.2 Add `QaActiveBreakdown` model with `awaiting_ci: int`, `escalated_open_cases: int`
- [ ] 8.3 Implement the four KPI SQL queries from design.md §D7 inside the summary handler PLUS the `awaiting_ci` and `escalated_open_cases` partition queries (escalated counts terminal cases requiring human action over a 7-day window; NOT a subset of `active_cases_now`)
- [ ] 8.4 Extend the existing `QaSummary` response model to include `kpis: QaKpiBlock` and `active_breakdown: QaActiveBreakdown`
- [ ] 8.5 Tests in `tests/api/test_api_qa.py`: (a) empty database returns sensible zero/null values, (b) seeded data returns expected counts, (c) MTTR null when sample empty, (d) escalated_open_cases reports nonzero only when a `failed`/`unfixable` attempt has `error_detail` text matching one of the documented human-action substrings (`human action`, `operator`, `escalat`, case-insensitive) per the canonical helper from §8.6
- [ ] 8.6 Implement `failed_with_human_action(attempt) -> bool` in `src/butlers/core/qa/severity.py` (or sibling helper); single canonical detector of "escalated" status used by both the KPI sub-label and `state_of_case()` (§9.7). Unit-tested in `tests/core/qa/test_severity.py`

## 9. Cases API endpoints

- [ ] 9.1 Add `QaCaseSummary`, `QaCaseDossier`, `QaPrSummary`, `QaJournalEvent` Pydantic models to `qa.py` per design.md §D8
- [ ] 9.2 Implement `GET /api/qa/cases` with `limit`, `sev`, `since` (`24h`/`7d`/`30d`/`all`) query params; returns `PaginatedResponse[QaCaseSummary]` ordered by most recent first
- [ ] 9.3 Implement `GET /api/qa/cases/:id` returning `ApiResponse[QaCaseDossier]`; joins `healing_attempts`, the latest linked `qa_findings`, and the most recent 50 `qa_investigation_events`
- [ ] 9.4 Implement `GET /api/qa/cases/:id/journal` with `cursor`, `limit` (default 50, max 500); returns `PaginatedResponse[QaJournalEvent]`
- [ ] 9.5 Implement the severity-int → high/medium/low mapping in `src/butlers/core/qa/severity.py` (`map_severity(int) -> Literal["high","medium","low"]`); used by both API and frontend
- [ ] 9.6 Implement `short_id_from_uuid(uuid) -> str` deriving the `#NNN` short id (stable; from the lowest-three-digit suffix of the UUIDv7 timestamp portion, or a sequence column if introduced)
- [ ] 9.7 Implement `state_of_case(attempt) -> Literal["detect","diagnose","pr","landed","escalated"]` mapping the existing healing_attempts.status into the five-state vocabulary. Mapping: `pr_merged` → `landed`; `pr_open` → `pr`; `investigating` → `diagnose`; `dispatch_pending` → `detect`; any status where `failed_with_human_action(attempt)` returns true → `escalated`; `unfixable` → `escalated` (terminal-unresolved is escalated regardless of whether the human-action marker is present); everything else → `detect`. Must call into the canonical helper from §8.6 (no inline duplication of the escalation check)
- [ ] 9.8 Implement `headline_for_case(attempt, finding) -> str` falling back to `finding.event_summary` when `investigation_notes.headline` is null
- [ ] 9.9 Tests in `tests/api/test_api_qa_cases.py` covering: empty list, filtered list (sev/since), case detail with full notes, case detail with missing notes (headline fallback), journal pagination, 404 on unknown id

## 10. Frontend — types and hooks

- [ ] 10.1 Add types to `frontend/src/api/types.ts`: `QaCaseSummary`, `QaCaseDossier`, `QaInvestigationNotes`, `QaJournalEvent`, `QaPrSummary`, `QaKpiBlock`, `QaActiveBreakdown`
- [ ] 10.2 Add `useQaCases({ limit, sev, since })` hook to `frontend/src/hooks/use-qa.ts` (TanStack Query; staleTime matches existing QA hooks)
- [ ] 10.3 Add `useQaCase(id)` and `useQaCaseJournal(id, { cursor, limit })` hooks
- [ ] 10.4 Extend `useQaSummary` selector to surface `kpis` and `active_breakdown` cleanly
- [ ] 10.5 Drop in-page consumption of `useQaTrends`, `useQaKnownIssues`, and `useQaInvestigations` from the new `QaOverviewPage.tsx`; the hooks remain exported for any future consumer

## 11. Frontend — QA dossier components

- [ ] 11.1 Create `frontend/src/components/qa/QaKpiStrip.tsx` — 4-cell hairline-divided grid; cells consume `kpis` + `active_breakdown`
- [ ] 11.2 Create `frontend/src/components/qa/CaseList.tsx` — 320 px rail with rule-separated rows per `QaCaseSummary`
- [ ] 11.3 Create `frontend/src/components/qa/CaseDossierHeader.tsx` — sev/id/butler/detected row + StateTrack + H2 headline
- [ ] 11.4 Create `frontend/src/components/qa/StateTrack.tsx` — mono caps state pipe with `escalated` variant
- [ ] 11.5 Create `frontend/src/components/qa/ClaimAnchoredBlurb.tsx` — serif paragraph rendering `blurb_segments`; emits `onClaimHover(claim_id | null)` to a parent
- [ ] 11.6 Create `frontend/src/components/qa/EvidenceLog.tsx` — mono rows for `evidence_lines[]`; consumes `hoveredClaim` from parent state to highlight matching rows; emits its own `onRowHover(claim_id | null)` to drive the bidirectional linkage
- [ ] 11.7 Create `frontend/src/components/qa/CounterEvidence.tsx` — mono table for `counter_evidence[]`
- [ ] 11.8 Create `frontend/src/components/qa/PRPanel.tsx` — state chip + title + branch/CI/diff stats + "Why this fix" serif italic + embedded `DiffPreview`
- [ ] 11.9 Create `frontend/src/components/qa/DiffPreview.tsx` — line-kind aware diff renderer
- [ ] 11.10 Create `frontend/src/components/qa/PatrolJournal.tsx` — full-width chronological rows with per-step color
- [ ] 11.11 Create `frontend/src/components/qa/CaseDossier.tsx` — composes Header + two-column body + PatrolJournal; lifted bidirectional hover state lives here
- [ ] 11.12 Component tests (`*.test.tsx`) for: claim-anchor hover linkage in ClaimAnchoredBlurb ↔ EvidenceLog, StateTrack escalated variant, PRPanel null/non-null PR, DiffPreview kind classification
- [ ] 11.13 Extend `CaseDossierHeader.tsx` to render the active-dismissal surface per qa-dashboard spec: when `case.dismissal` is non-null, render a mono "dismissed until <expires_at>" caption beneath the sev/id/butler row and a "remove dismissal" pill alongside Retry/Dismiss. Pill click calls `DELETE /api/qa/dismissals/:fingerprint` then invalidates the case query. Cases API must include the active `dismissal` block (or null) on `QaCaseDossier` — extend §9.1 / §9.3 to surface it

## 12. Frontend — page rewrites (hard cut)

- [ ] 12.1 Rewrite `frontend/src/pages/QaOverviewPage.tsx` as the dossier shell: sticky top bar, page header with clock + patrol cadence caption, `QaKpiStrip`, `CaseList` + `CaseDossier` two-pane body
- [ ] 12.2 Implement URL-driven case selection: `useSearchParams` reads `case=<short_id or uuid>`; clicking a rail row updates the query string; the selected case drives `useQaCase(id)`
- [ ] 12.3 Rewrite `frontend/src/pages/QaInvestigationsPage.tsx` as a Dispatch case index: sticky filter bar (state/severity/butler/time-range) + rule-separated `CaseList` rows; clicking a row navigates to `/qa/investigations/:attemptId`
- [ ] 12.4 Rewrite `frontend/src/pages/QaInvestigationDetailPage.tsx` to mount `CaseDossier` with the attempt id from the route param; include a back-link to `/qa`
- [ ] 12.5 Rewrite `frontend/src/pages/QaPatrolDetailPage.tsx` with the Dispatch primitives: header, findings as rule-separated list, dispatch summary as rule-separated list
- [ ] 12.6 Delete unused old-page component imports and helpers; verify no recharts component imports remain in the QA page tree
- [ ] 12.7 Smoke test in dev: load `/qa`, click a case, verify URL updates, navigate to `/qa/investigations/:id`, confirm the same dossier renders
- [ ] 12.8 Rebuild the Home Page QA widget per qa-dashboard "Home Page QA Widget" requirement: in the dashboard's home page (`frontend/src/pages/IndexPage.tsx` or equivalent), render a Dispatch-language compact surface with QA staffer status (`running`/`tripped`/`stopped` mono caption), last patrol timestamp + outcome, `active cases · now` count, click-through to `/qa`, and the "QA staffer not active" serif-italic empty state. No Kanban columns, no charts. Reuse `useQaSummary` for the status + count; verify no card chrome
- [ ] 12.9 Audit `frontend/src/components/layout/nav-config.ts` for the QA sidebar badge contract: confirm the badge sources from `kpis.active_cases_now` (or the equivalent count) rather than the now-removed `useQaKnownIssues` consumer. Update if the source path changed

## 13. Observability

- [ ] 13.1 Add OTel attributes to the `qa.investigation` span: `qa.notes_emitted` (bool), `qa.notes_parse_status` (one of `ok`/`partial`/`failed`)
- [ ] 13.2 Register Prometheus counters: `qa_investigation_notes_parse_total{status}`, `qa_findings_retention_purged_total`
- [ ] 13.3 Verify low-cardinality label discipline (no UUIDs, fingerprints, or butler names as label values)

## 14. End-to-end validation

- [ ] 14.1 Force a patrol via `POST /api/qa/force-patrol` against a dev butler producing synthetic errors; verify `flagged` and `drafted` events appear in `qa_investigation_events`
- [ ] 14.2 Run an end-to-end investigation with the configured QA runtime; verify `./.qa/investigation_notes.json` is produced by the portable file contract, parses cleanly, persists into `structured_evidence`, and the dossier renders all fields
- [ ] 14.3 Wait two patrol cycles after the case lands; verify `tick` events accumulate while the case is `pr_open` and stop after `merged`
- [ ] 14.4 Verify the daily cleanup job runs on schedule in dev (or run it manually) and increments the retention counter; confirm narrative fields are preserved post-cleanup
- [ ] 14.5 Verify the page renders correctly at `https://tzeusy.parrot-hen.ts.net/butlers-dev/qa` with real data
- [ ] 14.6 Run `make check` (ruff + full test suite) and confirm zero failures

## 15. Documentation

- [ ] 15.1 Update `roster/qa/MANIFESTO.md` to reflect the dossier surface and the journal capture, if anything in it conflicts
- [ ] 15.2 Update `roster/qa/CLAUDE.md` system prompt to include the `investigation_notes.json` emission instructions (or extract them into a skill at `roster/qa/.agents/skills/`)
- [ ] 15.3 Add a one-paragraph note to `about/lay-and-land/frontend.md` describing the new `frontend/src/components/qa/` directory and its role
