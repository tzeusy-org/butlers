## 1. Contract

- [x] 1.1 Add optional `pinned_target: NonEmptyStr | None` to `IngestControlV1` in `roster/switchboard/tools/routing/contracts.py`.

## 2. Ingest boundary

- [x] 2.1 In `ingest_v1()` (`roster/switchboard/tools/ingestion/ingest.py`), validate `envelope.control.pinned_target` against `_load_available_butlers(pool)` (routable, butler-typed) before dedup-adjacent triage evaluation; raise `ValueError` on an unknown/non-routable target.
- [x] 2.2 When `pinned_target` is valid, construct a `route_to` `PolicyDecision` (`matched_rule_type="pinned_target"`) taking precedence over thread-affinity lookup and ingestion-policy rule evaluation.
- [x] 2.3 Add `"pinned_target"` to the telemetry `_ALLOWED_RULE_TYPES` allowlist so it is recorded correctly rather than falling back to `"unknown"`.

## 3. Tests

- [x] 3.1 Unit test: pinned routing produces a deterministic `route_to` decision and bypasses rule evaluation.
- [x] 3.2 Unit test: pinned routing takes precedence over a matching ingestion-policy rule and over thread-affinity.
- [x] 3.3 Unit/integration test: unknown/non-routable `pinned_target` is rejected (`ValueError`), no `message_inbox`/`ingestion_events` row is created.
- [x] 3.4 Regression test: unpinned envelopes behave exactly as before (thread-affinity / rules / classification fallback unchanged).
- [x] 3.5 Regression test: duplicate-looking submissions validate `pinned_target` before returning `duplicate=true`.

## 4. Spec

- [x] 4.1 OpenSpec delta against `connector-base-spec` (ingest.v1 envelope + Triage Integration).
- [x] 4.2 OpenSpec delta against `dashboard-conversations` (documents that per-butler conversations set `pinned_target`, for bu-mj2k2 to implement).

## 5. Quality gates

- [x] 5.1 `ruff check` / `ruff format --check` on touched files.
- [x] 5.2 Targeted `pytest` for `roster/switchboard/tests/test_triage_ingest_integration.py` and any new pinned-target tests.
