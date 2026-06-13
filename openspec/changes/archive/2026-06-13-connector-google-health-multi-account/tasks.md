# Tasks

> All open decisions are recorded as ADRs in `design.md` §Decisions. Tasks below are implementation-only.

## 1. Account Discovery

- [ ] 1.1 Add `list_health_scoped_accounts(pool)` helper to `src/butlers/google_account_registry.py` returning every `status='active'` account whose `granted_scopes` is a superset of `GOOGLE_HEALTH_SCOPES`. Return tuples of `(account_uuid, email, entity_id, refresh_token_present_bool)`.
- [ ] 1.2 Update `GoogleHealthConnector._resolve_owner_and_scopes` to populate a `dict[account_uuid, OwnerContext]` from the helper and to diff against the previous cycle's account set to detect adds/removals.
- [ ] 1.3 Re-check on every `scope_recheck_s` cycle (default 300 s). On add: spawn per-resource poll state and per-account heartbeat. On remove: tear down per-resource poll state and close the heartbeat row (set `state='unknown'` with `error_message='account_removed'`).

## 2. Per-Account Poll Sets

- [ ] 2.1 Promote `_resources: dict[resource, ResourceState]` to `dict[(account_uuid, resource), ResourceState]`. Initialise per (account, resource) on add; tear down on remove.
- [ ] 2.2 Extend `_mint_access_token` to be per-account: cache keyed by `account_uuid`; each call uses that account's own refresh token resolved via `google_credentials._resolve_entity_refresh_token(pool, companion_entity_id)`. Scope-restricted mint (already landed) stays.
- [ ] 2.3 Heartbeat emission: spawn one `ConnectorHeartbeat` task per account with `endpoint_identity = google_health:user:<email>`. The connector-level `/health` endpoint computes worst-of state across per-account heartbeats and returns it as the aggregate.
- [ ] 2.4 Per-account Prometheus metric labels are already keyed by `endpoint_identity` — no metric schema change required, but confirm via a unit test that two accounts produce two distinct label sets.

## 3. Cursor Persistence Migration

- [ ] 3.1 Update `_cursor_endpoint_identity` to encode `google_health:user:<email>:<account_uuid>:<resource>`. Single-account installs see no behavioural change beyond the key shape.
- [ ] 3.2 Write a one-shot SQL migration that, for every existing `cursor_store` row matching `connector_type='google_health'` and the old `google_health:user:<email>:<resource>` shape, rewrites the `endpoint_identity` to include the matching `account_uuid` from `public.google_accounts.email`. Idempotent (re-running is a no-op). Reversible by stripping the `:<account_uuid>:` segment.
- [ ] 3.3 Add a regression test that the connector loads pre-migration cursor values correctly after the migration runs.

## 4. Ingestion-Event Identity Migration

- [ ] 4.1 Update envelope construction in `build_sleep_session_envelope` and `build_daily_summary_envelope` (and `control.idempotency_key`) to use the prefixed format `google_health:<email>:<resource>:<id>`. The format decision is locked — see design.md §Decisions/ADR-3.
- [ ] 4.2 Write a one-shot Alembic migration that rewrites the 3 existing `public.ingestion_events.external_event_id` rows from `google_health:activity:<date>` to `google_health:uniquosity@gmail.com:activity:<date>`. Verify no downstream chronicler-adapter regression — chronicler still has `google_health.*` adapter as deferred (`butler-chronicler/spec.md:113`), so the only consumer of `external_event_id` for these rows is the dashboard counter query in `src/butlers/api/routers/google_health.py::_fetch_ingest_counts`, which uses `LIKE 'google_health:%:%'` and stays compatible.
- [ ] 4.3 Update `_fetch_ingest_counts` and `_fetch_last_ingest_at` predicate patterns to match the new key shape (`google_health:%:sleep_session:%` for sleep, `google_health:%:%:%` minus sleep for daily summaries) without regressing single-account installs.

## 5. Dashboard

- [ ] 5.1 Update `GoogleHealthStatusResponse` in `src/butlers/api/models/google_health.py` to add `accounts: list[AccountStatus]` with per-account `email, state, scopes_granted, last_ingest_at, last_token_refresh_at, rate_limit_remaining, sleep_sessions_7d, daily_summaries_7d`. Keep top-level summary fields (`connected`, aggregate `state`, etc.) for back-compat by computing them as the worst-of across accounts.
- [ ] 5.2 Update `get_google_health_status` to enumerate health-scoped accounts via the registry helper from task 1.1 and populate per-account entries from individual heartbeat rows.
- [ ] 5.3 Frontend status-card update (out-of-band — track in a separate frontend bead but link from the report bead).

## 6. Health Butler Acceptance Path

- [ ] 6.1 Update the wellness-envelope acceptance check in the health butler (per the paired `butler-health` delta in `specs/butler-health/spec.md`) to accept any active health-scoped owner account, not just the primary `google_user_id`. The check should query `public.google_accounts` once per butler-session and cache the recognised identity set.
- [ ] 6.2 Regression test: an envelope with `sender.identity = <tzeuse@gmail.com email>` and a primary account of `uniquosity@gmail.com` is accepted when both rows are health-scoped + active; rejected when tzeuse@ row is missing scopes or status='revoked'.

## 7. Tests

- [ ] 7.1 Unit: `list_health_scoped_accounts` returns only `status='active'` AND scope-superset accounts; ignores rows with missing scopes or `status='revoked'`.
- [ ] 7.2 Unit: per-account teardown on mid-run scope revocation leaves other accounts' polls and cursors untouched.
- [ ] 7.3 Integration (`_StubTransport`-style): a connector configured with 2 accounts produces 2 distinct heartbeat rows, 2 distinct token mints (scope-restricted), and emits envelopes for both. Assert no cross-account token reuse and no cursor collisions.
- [ ] 7.4 Status endpoint returns per-account entries when 2 accounts are health-scoped; falls back to a single-account shape when only 1 exists (back-compat check).
- [ ] 7.5 Migration smoke test: run the cursor + ingestion-event migrations against a fixture DB containing the 3 historical activity rows; verify post-state cursor rows resolve and counters in `_fetch_ingest_counts` return the same totals.

## 8. Report

- [ ] 8.1 Terminal reconciliation/report bead: confirm beads 1.1–7.5 are closed, dashboards render both accounts, fresh ingest is observed for sleep/HR on tzeuse@, and the paired `butler-health` delta acceptance scenarios pass. Publish a short report under `docs/reports/connector-google-health-multi-account.md` capturing actual vs. projected ingest counts, any deferred work, and follow-up beads if any.
