## Depends On

- **`crud-to-spo-migration`** (in-flight) — this change reuses its predicate taxonomy (`measurement_{type}` naming conventions, SPO fact store, `predicate_registry`) for landing wellness facts. Do not land the `butler-health` delta until `crud-to-spo-migration` is archived, or restrict this change's butler-health delta to claims that survive either outcome. Tasks §8 assume the SPO predicate model is available.

## Why

The Health butler today only tracks what the owner *manually* logs (measurements, medications, symptoms, meals). The richest, lowest-effort signal in the owner's life — their wearable — is absent. Without it, Health cannot reason about sleep patterns, recovery, activity trends, or cross-link "felt bad today" symptom entries against the underlying physiology (short sleep, low HRV, elevated resting HR).

Prior research (`docs/archive/health-wearable-draft.md`, archived 2026-03-21) pointed at the Fitbit Web API as the free, server-reachable path. **That research is now outdated.** In September 2026 Google is turning down the legacy Fitbit Web API and replacing it with the **Google Health API** — a REST API at `https://health.googleapis.com` using standard Google OAuth 2.0. Google recommends launching new integrations on the new API by the end of May 2026. Every legacy integration must migrate before the turndown to preserve data continuity.

This forces a change and simultaneously makes the change *cheaper* for Butlers: we already operate a mature Google OAuth infrastructure for Calendar and Drive (`public.google_accounts`, `google-multi-account-oauth`). Adding Google Health is an additive scope change on the existing plumbing — not a new credential silo.

## What Changes

- **New Google Health connector** (`src/butlers/connectors/google_health.py`): Standalone polling process that reads the owner's daily and intraday wellness data from `https://health.googleapis.com/v4/users/me/...`, normalizes events into `ingest.v1` envelopes, and submits them to the Switchboard. Pure-Python over `httpx`. Polling-only; no webhooks.
- **New Google Health module** (`src/butlers/modules/google_health.py`): Read-only MCP tools for the Health butler to query sleep sessions, HR/HRV history, activity summaries, and SpO2 trends against the ingested facts. No write-back — the device is authoritative.
- **Reuses existing Google OAuth infrastructure**: No new account registry, no new credential store, no new User Secrets templates. The owner's Google account in `public.google_accounts` gains additional Google Health scopes via the existing re-consent flow (`force_consent=true`). `granted_scopes` already tracks per-account scope state.
- **New OAuth scope set `health`**: Introduces a `scope_set` query parameter on `/api/oauth/google/start` with three Google Health scopes (`https://www.googleapis.com/auth/googlehealth.sleep`, `.activity_and_fitness`, `.health_metrics_and_measurements`, read variants). This is a **new capability** on the OAuth pipeline — the current endpoint hard-codes a default scope string and does not accept a scope selector. The new pattern supports Google Health today and other scope sets (e.g. Photos) later.
- **New per-account scope-set picker UI**: Net-new component on the Google Accounts dashboard page. The current card displays `granted_scopes` as a read-only CSV; this change introduces a toggle per scope set (Calendar / Drive / Google Health) plus a status card for the Google Health connector.
- **Google Cloud Console submission for Restricted scope verification**: The Google Health API scopes are classified Restricted, requiring a Google privacy and security review of the OAuth client. This is a one-time review per OAuth app registration, not per user. The verification package (privacy policy, demo video, security questionnaire) is a tracked deliverable of this change.
- **Switchboard routing registration**: Add `wellness` to `SourceChannel`, `google_health` to `SourceProvider`, validate the pair in `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Health butler integration**: Enable `[modules.google_health]` in `roster/health/butler.toml`. Wellness-derived facts land in the health butler's SPO memory store under dedicated predicates (`sleep_session`, `sleep_stage_summary`, `resting_hr_daily`, `hrv_daily`, `spo2_daily`, `breathing_rate_daily`, `steps_daily`, `active_minutes_daily`, `vo2_max`). No new per-metric tables — reuses the direction set by `crud-to-spo-migration`.
- **Docker compose**: New `connector-google-health` service, modeled after `connector-spotify` / `connector-steam`.
- **RFC 0003 amendment**: Register `wellness/google_health` channel/provider pair alongside the existing `gaming/steam`, `whatsapp_user_client/whatsapp`, etc.

## Capabilities

### New Capabilities
- `connector-google-health`: Polling-only ingestion of the owner's Google Health data with per-resource poll intervals, OAuth2 token refresh via the existing Google credential pipeline, rate-limit-aware batching, checkpoint persistence, and ingest.v1 normalization. Structurally mirrors `connector-spotify` — single-owner, non-messaging, state-diff-based event emission.
- `module-google-health`: Read-only MCP tools on the Health butler for querying ingested wellness facts (`sleep_history`, `sleep_latest`, `hr_history`, `hrv_history`, `spo2_history`, `activity_summary`, `vo2_max_latest`). Standard Module pattern (config schema, `register_tools()`, credential resolution via the shared Google account refresh pipeline).

### Modified Capabilities
- `butler-switchboard`: Register `wellness` source channel, `google_health` source provider, channel-provider pair in `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- `butler-health`: Enable `[modules.google_health]` in the health butler's module profile, add Google Health query tools to the tool inventory, add wellness-derived memory predicates to the health memory taxonomy.
- `google-multi-account-oauth`: Register the three Google Health scopes in the OAuth scope catalog so they can be selected at authorization / re-consent. Document that Google Health scopes require Restricted-scope verification of the OAuth client before end users can grant them in production mode.
- `dashboard-google-accounts`: Add Google Health scope toggles to the per-account scope selector. Show connection status for the wellness integration (last ingest, token age, rate-limit headroom). Surface a pre-verification warning when the OAuth client is still in test mode.

## Impact

- **Routing contracts** (`roster/switchboard/tools/routing/contracts.py`): Extend `SourceChannel` and `SourceProvider` literals; add to `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Pipeline config** (`src/butlers/modules/pipeline.py`): No change — wellness is not an interactive channel.
- **Daemon interactive channels** (`src/butlers/daemon.py`): No change — wellness events do not receive replies.
- **Dashboard frontend**: Extend the existing Google Accounts settings page to expose Google Health scopes. No new User Secrets templates needed — all credentials flow through the existing Google account linkage in `public.google_accounts` + `entity_info`.
- **Dashboard API**: Extend `/api/oauth/google/start` scope handling to include Google Health scopes when requested. New read-only endpoints for the connector's health card (`GET /api/connectors/google-health/status`).
- **Database**: No new tables. Minor additive columns on `public.google_accounts`: a `metadata JSONB` column (for `google_health_test_mode` flag) and a `last_token_refresh_at TIMESTAMPTZ` column (for the dashboard's 7-day expiry heuristic). If either column is already present, the migration is a no-op for that column. Ingested facts flow through the existing health butler SPO memory fact store with nine new predicates registered in `predicate_registry` (see `butler-health` delta). `public.google_accounts.granted_scopes` already accommodates the new scope URL entries.
- **Docker compose**: New `connector-google-health` service.
- **External dependency**: Google Health API at `https://health.googleapis.com` (v4). Google OAuth 2.0 via existing client. Data parity with the legacy Fitbit API covers sleep, heart rate, HRV, SpO2, steps, activity, weight, exercise, and 15+ other data types.
- **Review dependency**: Google's Restricted scope verification must clear before the integration is usable in production mode. Until verified, the OAuth client operates in test mode (limited to developer-added users, ~100-user cap). For a self-hosted single-owner deployment, test mode is sufficient to operate — verification is needed only if the codebase is ever consumed by non-developer end users. The verification submission is still tracked as a deliverable because the Butlers project is open-source and downstream users will benefit.
- **Timeline**: Recommended launch end of May 2026. Legacy Fitbit Web API turndown September 2026 — not a hard dependency since we are building against the new API directly, not migrating.
- **Security surface**: Re-consent OAuth flow on the owner's existing Google account; new refresh token issued covering the existing + new scopes. Health data is sensitive — inherits the same trust model as manually-logged health facts already in the schema. Covered by `about/craft-and-care/security-and-secrets.md` review triggers.
- **Doctrine/RFC**: RFC 0003 must be amended to register the `wellness/google_health` pair. Task captured in `tasks.md`. No new RFC needed — this follows the established connector pattern.
