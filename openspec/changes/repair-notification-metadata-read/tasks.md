## 1. Notification API read compatibility

- [ ] 1.1 Implement the shared notification metadata normalizer in `src/butlers/api/routers/notifications.py` and route the global list, butler-scoped list, and mark-read response through its one-layer object-or-null contract (REQ-core-notify-001).
- [ ] 1.2 Add direct and endpoint regressions in `tests/api/test_notifications.py` for mappings, `null`, encoded objects, malformed strings, every inner non-object string result, actual non-string non-objects, and no-recursive-decode behavior across all three response paths (REQ-core-notify-001).
- [ ] 1.3 Assert the API compatibility work preserves existing effective-status, degraded-source, pagination, and mark-read behavior rather than changing delivery or status semantics.
- [ ] 1.4 Update `docs/frontend/backend-api-contract.md` with the exact object-or-null normalization matrix, `_raw` fallback, and all three affected response paths.

## 2. Serving-writer deployment evidence gate

- [ ] 2.1 Preserve or extend the real-Postgres writer regression in `roster/switchboard/tests/test_deliver_search_path_integration.py` so a normal `log_notification()` write is proven to persist a JSONB object through the registered codec (REQ-core-notify-002).
- [ ] 2.2 Prepare the read-only operational evidence procedure that enumerates every active notification-writer process, container/image digest or runtime revision, command, and bind-mounted source; prove each serves #3458 (`7d2bea3bc`) or a descendant rather than relying on checkout or merge state (REQ-core-notify-002).
- [ ] 2.3 Record the active Switchboard migration frontier, aggregate-only pre-repair candidate-band bounds/counts, and a bounded post-deploy observation window with no new JSONB string metadata rows; do not print raw metadata, messages, recipients, or row identifiers (REQ-core-notify-002).
- [ ] 2.4 Treat missing/stale process evidence, a frontier mismatch, a new string row, or candidate-band growth as an external deployment blocker; stop before any historical mutation and do not substitute manual SQL or a runtime workaround (REQ-core-notify-002).

## 3. Guarded Switchboard historical repair

- [ ] 3.1 Only after Section 2 has successful recorded evidence, recheck the current Switchboard migration head and add the next Switchboard-chain migration with one captured cutoff, a target-relation absent-table no-op guard, and one transactional set-based repair of pre-cutoff JSONB string metadata (REQ-core-notify-003).
- [ ] 3.2 Implement migration conversion with a session-local exception-safe one-layer parser: valid encoded objects become objects, malformed/inner-non-object strings become `{"_raw": <original string>}`, and non-string values plus all unrelated columns remain unchanged; emit aggregate-only counts/bounds and make downgrade an intentional data no-op (REQ-core-notify-003).
- [ ] 3.3 Add migration regressions in `tests/config/test_switchboard_notifications_migration.py` (or the established Switchboard migration suite) for valid conversion, `_raw` fallback, cutoff exclusion, ordinary-value preservation, absent-table success, rollback atomicity, replay/idempotence, aggregate-only evidence, and no-op downgrade. Include a concrete malformed-inner-JSON candidate (for example, `to_jsonb('{"broken":'::text)`) alongside a valid candidate, and prove the one atomic set-based update completes while preserving the malformed candidate's exact outer text under `_raw` (REQ-core-notify-003).
- [ ] 3.4 Run the migration only through the normal deployment runner after the gate; collect aggregate post-migration evidence of zero pre-cutoff string candidates and no new strings in the observation window, without manual SQL or raw-payload output.

## 4. Verification and handoff

- [ ] 4.1 Run focused API, writer, and Switchboard migration tests; run the applicable Ruff checks/format check and the migration-chain guard after the final Switchboard revision is known.
- [ ] 4.2 Re-run `openspec validate repair-notification-metadata-read --strict`, review the API documentation against the implemented response matrix, and hand off the actual commands, aggregate evidence, deployment decision, and any external blocker without claiming a repair ran when the gate did not pass.
