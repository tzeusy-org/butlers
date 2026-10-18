## 0. Ordering and preconditions

- [ ] 0.1 Land this change only after owner adoption of the two decisions (A1, B1) and the open owner questions in proposal.md.
- [ ] 0.2 Serialize implementation behind PR #4187 / bu-h40h2b.5.2.2 (same calendar.py, workspace API/UI tests and the same two OpenSpec domains); do not combine its find-time error work with this feature.
- [ ] 0.3 After #4488 merges, merge origin/main into this change's branch and re-run `make check-spec-overwrites`; this change MODIFIES no canonical block, so only archive ordering of module-calendar/spec.md is at stake.
- [ ] 0.4 Resolve overlap with #4394 (`calendar-context-truth`): decide which change owns projected provider `eventType` / declared-context fields; REQ-module-calendar-034 reads them and must cite the one that lands.
- [ ] 0.5 Before archive, re-check the IDs REQ-module-calendar-031..034, REQ-calendar-conflict-overcommitment-radar-010..012, REQ-dashboard-domain-pages-050..052 and REQ-butler-chronicler-008..009 against canonical specs and pending changes (generalist-lead-focused-delegation holds REQ-module-calendar-030).

## 1. S1 provider occurrence projection (module-calendar)

- [ ] 1.1 Failing tests first (tests/modules/test_module_calendar.py): parse `recurringEventId`/`originalStartTime`; one visible occurrence per key; moved instance keeps `origin_instance_ref`; minimally populated cancelled exception suppresses only itself.
- [ ] 1.2 Switch the sync list to `singleEvents=true` with `timeMin`/`timeMax` on full sync; derive `origin_instance_ref` from the occurrence key; keep non-recurring derivation and the time-drifted prune.
- [ ] 1.3 Add a cursor version; a stale-version or collapsed-mode token triggers a bounded full resync; stage, commit retirement of previous-version rows and cursor advance in one transaction; failure leaves projection and cursor untouched.
- [ ] 1.4 Record `coverage_through`; enforce the window on incremental writes; schedule a horizon-advance resync; expose `coverage_through` and `coverage` on the source freshness surface.
- [ ] 1.5 Idempotent retry and rollback tests (bump the version again, no token reuse).
- [ ] 1.6 Core-chain migration for any new column or metadata shape, derived from the actual merged graph; real-PostgreSQL upgrade, bounded downgrade/reapply and runtime-role privilege tests (CI-only locally: Docker is unavailable on this host).
- [ ] 1.7 Chronicler calendar adapter: keep `calendar:{origin_instance_ref}` stable (tests/chronicler/test_calendar_adapter.py).

## 2. S2 honest day load (radar, module-calendar category, dashboard)

- [ ] 2.1 Failing tests first (tests/core/temporal/test_conflicts.py): four-day hold clipped per local day; DST 23/25-hour days; excluded rows stay excluded; sum of clipped parts equals duration.
- [ ] 2.2 Category derivation as a pure function of stored event (REQ-module-calendar-034); add the category field to the conflict candidate; no title heuristic (tests/core/test_calendar_provenance.py).
- [ ] 2.3 Meeting-only density with separate uncategorized bucket; warning vs info overload (REQ-radar-011).
- [ ] 2.4 Additive `day_load` on `ConflictScanResponse` with `coverage` states; fail-open contract unchanged (tests/api/test_calendar_conflict_radar.py, tests/api/test_read_models_calendar_workspace_v1.py).
- [ ] 2.5 Frontend: occurrence marker, day-load line with unavailable/stale states (frontend/src/components/calendar tests, CalendarWorkspacePage.test.tsx).

## 3. S3 Chronicler reconciliation run (only under adopted A1)

- [ ] 3.1 Sequence after S1 (needs stable occurrence provenance) and after S2 where both change the Calendar header.
- [ ] 3.2 Chronicler-owned run table and scheduled job; deterministic contradiction/unknown only; no attended label; owner corrections win; idempotent per `(local_day, algorithm_version)`.
- [ ] 3.3 Read endpoint (REQ-butler-chronicler-009); not-found for current, future and never-evaluated days.
- [ ] 3.4 Dashboard header line (REQ-dashboard-domain-pages-052), absent for current/future days; coverage and freshness shown.
- [ ] 3.5 Prove no Calendar write path: grants unchanged, tests/migrations/test_chronicler_calendar_read_surface_migration.py still passes.

## 4. Docs and gates

- [ ] 4.1 Update docs/modules/calendar.md and the Chronicler docs in the same delivery.
- [ ] 4.2 `openspec validate calendar-occurrence-and-day-honesty --strict`, `openspec validate --all --strict`, `make check-spec-overwrites`, `make check-guards`; for implementation also `make test-plan BASE=origin/main`, `make check-ci-test-shards`, and from frontend/ `npm run knip`, lint, build and the focused Vitest files.
- [ ] 4.3 No live provider mutation, credential access or production migration is part of any pre-merge test.
