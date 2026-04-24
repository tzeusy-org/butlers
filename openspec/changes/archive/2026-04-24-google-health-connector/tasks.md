> **Blocking predecessor.** This change depends on `openspec/changes/crud-to-spo-migration/` being archived (its predicate taxonomy and `predicate_registry` are prerequisites for §7–§8). If that change slips, either wait or restrict this change's `butler-health` delta to claims that survive either outcome.

## 1. Google Health API Discovery (confirm at implementation, not a blocking phase)

These are items the Google Health API reference does not fully pin down today. Rather than block the whole change on a research sprint, the implementer confirms each one while doing the section of work that needs it, and records the finding in `research-notes.md`. If a discovered fact invalidates a spec scenario, update the spec in the same PR.

- [ ] 1.1 (do while §4/§5) Confirm exact endpoint paths for each data type bundle (sleep, heart-rate, hrv, spo2, breathing-rate, steps, active-minutes, vo2-max) against `https://health.googleapis.com/v4/`
- [ ] 1.2 (do while §3) Confirm the exact **full scope URLs** for `granted_scopes` (currently assumed: `https://www.googleapis.com/auth/googlehealth.sleep`, `.activity_and_fitness`, `.health_metrics_and_measurements` read variants) and whether read-vs-write is a separate URL or an OAuth2 access parameter
- [ ] 1.3 (do while §5.5) Confirm the 429 retry header and any rate-limit headers exposed on responses
- [ ] 1.4 (do while §3) Confirm that the existing OAuth client can be augmented with Google Health scopes, or whether a separate OAuth client is required for Restricted-scope apps. If a separate client is required, extend §3 to provision it.
- [ ] 1.5 (do while §5.4) Confirm the Reconciled Stream endpoint and whether it is the default read path or requires an explicit parameter
- [ ] 1.6 Keep `research-notes.md` current as discoveries land; any spec-scenario revisions go in the same PR as the discovery

## 2. Switchboard Routing Registration

- [ ] 2.1 Add `"wellness"` to `SourceChannel` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 2.2 Add `"google_health"` to `SourceProvider` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 2.3 Add `"wellness": frozenset({"google_health"})` to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- [ ] 2.4 Write tests covering valid `wellness/google_health` envelope acceptance and invalid `wellness/fitbit` rejection
- [ ] 2.5 Amend `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md` to register the `wellness/google_health` pair. Draft the exact markdown edit (addition to the canonical pairings list at RFC 0003 around line 50) inside this change folder — do not leave the amendment as a future intention. The runtime validator in `contracts.py` references the RFC's canonical list, so the RFC edit is load-bearing, not cosmetic.

## 3. OAuth Scope Catalog Extensions — Net-New `scope_set` Selector

> This is **not** reuse of existing OAuth plumbing. `/api/oauth/google/start` today hard-codes `_DEFAULT_SCOPES` in `src/butlers/api/routers/oauth.py` (around lines 168–183, 278). This change introduces a named-scope-set registry as a net-new OAuth capability. Discovery items §1.2 and §1.4 are resolved in-line during this section.

- [ ] 3.1 Register the three Google Health **full scope URLs** in a new named scope set `"health"` inside the Google OAuth scope catalog. Add sibling entries for `"base"`, `"calendar"`, `"drive"`, `"gmail"` using the scopes already baked into `_DEFAULT_SCOPES` so existing flows become scope-set-based without behaviour change.
- [ ] 3.2 Extend `/api/oauth/google/start` to accept a `scope_set: str | list[str]` query parameter (comma-separated). Compose the authorization URL by unioning the requested sets' scopes plus the implicit `base` set. Preserve existing behaviour when the parameter is omitted (backward-compat).
- [ ] 3.3 Ensure `GET /api/oauth/google/start?scope_set=health&force_consent=true` produces an authorization URL with all three Google Health full scope URLs plus `base`.
- [ ] 3.4 Ensure the OAuth callback populates `public.google_accounts.granted_scopes` with the new full-URL entries after successful re-consent (no short-form conversion).
- [ ] 3.5 Return HTTP 400 `{"error": "unknown_scope_set", ...}` when an unrecognized set is requested.
- [ ] 3.6 Additive Alembic migration for `public.google_accounts`: add `metadata JSONB NOT NULL DEFAULT '{}'::jsonb` and `last_token_refresh_at TIMESTAMPTZ` columns if not already present (no-op for columns that already exist). Update `last_token_refresh_at` on every successful token refresh through the existing pipeline.
- [ ] 3.7 Set `metadata.google_health_test_mode = true` on the Google account row when the OAuth client is detected to be in test mode during a Google Health scope grant.
- [ ] 3.8 Write tests covering: scope-only request, multi-set request, scope-upgrade on existing account via `force_consent=true`, partial grant, revocation of Health scopes, backward-compat when `scope_set` is omitted, unknown-set rejection, `last_token_refresh_at` update on refresh.
- [ ] 3.9 Document the Restricted-scope verification requirement in the OAuth scope catalog comments, including the test-mode vs production-mode distinction.

## 4. Google Health Connector — Scaffolding (depends on §3)

- [ ] 4.1 Create `src/butlers/connectors/google_health.py` with `if __name__ == "__main__": asyncio.run(...)` entrypoint following the spotify/steam pattern
- [ ] 4.2 Create `src/butlers/connectors/google_health_client.py` — thin async httpx wrapper around `https://health.googleapis.com/v4/*` with OAuth token refresh delegated to the shared Google credential pipeline via `resolve_owner_entity_info()` (never `CredentialStore.resolve()` or `os.environ.get`)
- [ ] 4.3 Implement startup probe: confirm the primary Google account has all three Google Health full scope URLs in `granted_scopes`; if not, start in degraded mode
- [ ] 4.4 Implement heartbeat and metrics registration via the shared `connectors/heartbeat.py` and `connectors/metrics.py` — report `healthy | degraded | error` states (do NOT introduce a `broken` state)
- [ ] 4.5 Implement `HealthSocket` health endpoint
- [ ] 4.6 Implement mandatory base-spec obligations: source filter gate via `IngestionPolicyEvaluator` scoped `connector:google_health:<endpoint_identity>`, filtered-events batch flush to `connectors.filtered_events`, replay-queue drain each poll cycle

## 5. Google Health Connector — Polling Loop

- [ ] 5.1 Implement per-resource poll cadence (default: daily summaries every 30 minutes; HRV/SpO2/breathing every 60 minutes; VO2 max once daily)
- [ ] 5.2 Implement the state-diff logic: for each resource, compare the latest response against the last-seen checkpoint; emit envelopes only for new or changed records
- [ ] 5.3 Implement checkpoint persistence per resource via `cursor_store.save_cursor(pool, connector_type="google_health", endpoint_identity="google_health:user:<google_user_id>:<resource>", cursor_value=...)` / `load_cursor(...)`. **Note:** `cursor_store` uses a 2-tuple key `(connector_type, endpoint_identity)` (confirmed in `src/butlers/connectors/cursor_store.py`), so per-resource dimension is encoded into the `endpoint_identity` suffix, not a third key field.
- [ ] 5.4 Implement Reconciled Stream consumption for each relevant resource
- [ ] 5.5 Implement 429 handling (honour `Retry-After` header; exponential backoff with jitter as fallback)
- [ ] 5.6 Implement first-run backfill governed by `GOOGLE_HEALTH_BACKFILL_DAYS` env var (default 30)

## 6. Google Health Connector — Ingest Envelope Construction

- [ ] 6.1 For each resource, build an `ingest.v1` envelope with `source.channel = "wellness"`, `source.provider = "google_health"`, `source.endpoint_identity = "google_health:user:<google_user_id>"` (3-segment `<provider>:<type>:<value>` per Google-family convention; per-resource cursor variants append a `:<resource>` suffix for `cursor_store` keying but the envelope identity remains the 3-segment canonical form)
- [ ] 6.2 Ensure `event.external_event_id` uses a stable Google Health record identifier where available (e.g. `session_id` for sleep, `<date>:<resource>` for daily summaries)
- [ ] 6.3 Ensure `control.idempotency_key = "google_health:<resource>:<record_id>"`
- [ ] 6.4 Ensure `payload.raw` carries the full Google Health response dict and `payload.normalized_text` is a human-readable summary
- [ ] 6.5 Ensure `sender.identity = <google_user_id>` is resolvable by the Switchboard via the pre-registered `public.contact_info` row (`type="google_health"`, `value=<google_user_id>`, `entity_id=owner_entity_id`) — register that row during OAuth callback for `scope_set=health`
- [ ] 6.6 Write unit tests for envelope construction covering each resource type

## 7. Google Health Module — `src/butlers/modules/google_health.py`

- [ ] 7.1 Create `GoogleHealthConfig` Pydantic model (empty v1 — module has no runtime knobs beyond module-base defaults)
- [ ] 7.2 Implement `GoogleHealthModule(Module)` with `name="google_health"`, `dependencies=[]`, `migration_revisions()=None`. Verify the `Module` base class's `dependencies` attribute shape in `src/butlers/modules/base.py` matches `module-spotify` precedent before committing.
- [ ] 7.3 Implement `on_startup()`: resolve the primary Google account's `entity_id` via `google_account_registry.get_primary()` (confirm exact function name against `src/butlers/google_account_registry.py`); verify Google Health scopes are granted; no-op if not (tools respond with a clear "reconnect Google Health" error)
- [ ] 7.4 Implement `register_tools()` for read-only MCP tools: `sleep_history`, `sleep_latest`, `hr_history`, `hrv_history`, `spo2_history`, `activity_summary`, `vo2_max_latest`, `breathing_rate_history`
- [ ] 7.5 Each tool queries the Health butler's SPO memory fact store via `memory_search` (scope='health', appropriate `measurement_*` or `sleep_*` predicate) — NOT the Google Health API directly
- [ ] 7.6 Add `google_health` module to `roster/health/butler.toml` under `[modules.google_health]` (no config keys required)
- [ ] 7.7 Write unit tests for each tool's predicate query shape

## 8. Health Butler — Fact Ingestion Path (depends on `crud-to-spo-migration` archived)

- [ ] 8.1 Confirm the butler-side entry point for non-interactive ingest envelopes (same mechanism Spotify and Steam butlers use today — verify against `roster/health/` and sibling rosters; do NOT assume a new per-butler ingest-handler registry exists)
- [ ] 8.2 Implement the translator in the Health butler: unpack `payload.raw`, derive the correct predicate + `valid_at`, call the memory module's fact-store write tool (confirm exact name against `src/butlers/modules/memory/tools/__init__.py`; likely `memory_store_fact`) with `scope='health'` and `entity_id=owner_entity_id`
- [ ] 8.3 Use the predicate names from the D5 taxonomy exactly: `sleep_session`, `sleep_stage_summary`, `measurement_resting_hr`, `measurement_hrv`, `measurement_spo2`, `measurement_breathing_rate`, `measurement_steps`, `measurement_active_minutes`, `measurement_vo2_max`
- [ ] 8.4 Write an idempotent Alembic migration (owned by the health butler chain) that upserts the nine new predicates into the memory module's `predicate_registry` with appropriate entity-type and cardinality metadata
- [ ] 8.5 Ensure duplicate envelopes are idempotent (the Switchboard dedupes on `control.idempotency_key`, but the Health butler translator must also be safe under replay)
- [ ] 8.6 Implement the non-primary-account rejection: the translator SHALL reject envelopes whose `sender.identity` does not match the primary Google account's `google_user_id` (single-owner v1 safety invariant)
- [ ] 8.7 Write integration tests covering: sleep ingest, daily summary ingest, replay idempotency, non-primary rejection, scope-downgrade (translator gracefully no-ops)

## 9. Dashboard — Per-Account Scope-Set Picker (NET-NEW component)

> The current Google Accounts card at `frontend/src/components/settings/GoogleOAuthSection.tsx` renders `granted_scopes` as a read-only CSV. This section builds the scope-set picker from scratch — it is not a modification of an existing toggle.

- [ ] 9.1 Build a new scope-set picker component in `GoogleOAuthSection.tsx` that renders one row per registered scope set (`Calendar`, `Drive`, `Google Health`) with a toggle reflecting whether `granted_scopes` contains all of that set's URLs
- [ ] 9.2 Wire the "Google Health" toggle to `GET /api/oauth/google/start?scope_set=health&force_consent=true&account_hint=<account_email>` for grant and `DELETE /api/connectors/google-health/disconnect` for revocation (with a confirmation modal stating Calendar/Drive remain connected)
- [ ] 9.3 Build a Google Health connector status card component showing connection state, last ingest timestamp, 7-day ingest counts, token age, and rate-limit headroom (hidden if the metrics surface has no rate-limit header to display)
- [ ] 9.4 Build the test-mode warning banner variants (orange / red) driven off `metadata.google_health_test_mode` and the `last_token_refresh_at` age heuristic; wire the red banner to a re-consent CTA
- [ ] 9.5 `frontend/src/lib/user-secret-templates.ts` — no changes (intentional: Google Health credentials flow through the Google account linkage, not User Secrets)

## 10. Dashboard API — Connector Status and Scope-Selective Disconnect

- [ ] 10.1 Implement `GET /api/connectors/google-health/status` returning `{connected: bool, scopes_granted: [...], last_ingest_at: ISO8601, last_token_refresh_at: ISO8601, rate_limit_remaining: int|null, test_mode: bool, state: "healthy"|"degraded"|"error"}`
- [ ] 10.2 Implement `DELETE /api/connectors/google-health/disconnect` — revokes only the three Google Health scopes (calls Google's revoke endpoint scoped to those URLs) and updates `granted_scopes` without touching `calendar` / `drive` entries or the `google_accounts` row itself
- [ ] 10.3 Write tests for both endpoints, including: full-account disconnect still revokes Health scopes as part of union revocation; scope-selective disconnect preserves Calendar/Drive

## 11. Docker Compose and Deployment

- [ ] 11.1 Add `connector-google-health` service entry to `docker-compose.dev.yml` modeled on the actual `connector-spotify` block at `docker-compose.yml:270-291`. Use `image: butlers-app:latest`, `<<: *connector-env`, volumes `./logs:/app/logs`, networks `[db, backend, egress]` (egress required for `health.googleapis.com`, per `connector-gmail` precedent), depends on `log-init`, `migrations`, `butlers-up`, `oauth-gate`.
- [ ] 11.2 Add the same service to `docker-compose.yml` (production)
- [ ] 11.3 Update `about/legends-and-lore/rfcs/0008-deployment-network-security.md` §"Per-Service Network Assignment" table to register the new row: `connector-google-health` → `db, backend, egress`
- [ ] 11.4 Verify clean startup: `docker compose up connector-google-health` should succeed in degraded mode when scopes are absent, and transition to healthy after pairing

## 12. Google Cloud Console — Restricted Scope Verification Package (non-blocking, async)

- [ ] 12.1 Draft a privacy policy that covers the Butlers project's handling of Google Health data (data minimization, retention, no third-party sharing, no sale, local storage only)
- [ ] 12.2 Record a demo video walking through the owner's consent flow, data use inside the Health butler, and the disconnect path
- [ ] 12.3 Complete the Google security questionnaire for the OAuth client
- [ ] 12.4 Submit the verification package via Google Cloud Console; track the ticket ID in this change's followup notes
- [ ] 12.5 On approval, flip the OAuth client from test mode to production; update the dashboard banner accordingly
- [ ] 12.6 Document the test-mode vs production-mode tradeoff for downstream self-hosters in the deployment guide

## 13. Topology and Doctrine Updates (post-implementation)

- [ ] 13.1 Add `Google Health` row to `about/lay-and-land/components.md` §3 Connectors inventory table
- [ ] 13.2 Add `Google Health` to `about/heart-and-soul/v1.md` Connectors inventory. **Binding v1 scope inclusion** (per v1.md rule "If a capability is not listed under 'What v1 Ships,' it is not in v1"), not merely descriptive — this edit must land with the implementation.
- [ ] 13.3 Revisit Chronicler compatibility after RFC 0014 is accepted; keep Google Health deferred until time fields, boundary semantics, privacy/precision/retention behavior, idempotency, and projection path are specified.

## 14. Validation

- [ ] 14.1 `uv run ruff check src/ tests/ roster/ conftest.py --output-format concise` passes
- [ ] 14.2 `uv run ruff format --check src/ tests/ roster/ conftest.py -q` passes
- [ ] 14.3 Targeted pytest coverage for the connector, module, and health butler ingestion translator passes
- [ ] 14.4 Integration test: end-to-end flow from mocked Google Health response through connector → Switchboard → Health butler → fact store → MCP tool query returns the expected fact
- [ ] 14.5 `openspec validate google-health-connector --strict` passes
- [ ] 14.6 Manual verification: pair Google Health scopes in dashboard, observe connector transition from degraded to healthy, observe first backfill complete, query sleep history via MCP tool and confirm the response
