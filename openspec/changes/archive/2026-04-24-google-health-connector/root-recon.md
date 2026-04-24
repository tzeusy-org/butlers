# Root Reconciliation Report — google-health-connector

**Date:** 2026-04-25
**Reconciler:** bu-k5l35.7 (root reconciliation bead)
**Archive decision:** READY

---

## 1. Bead-by-Bead Trace

| Bead | Title | PR / Commit | Specs fulfilled |
|---|---|---|---|
| **bu-k5l35.1** | Wellness routing + OAuth scope_set | PR #1107 | `butler-switchboard` delta (SourceChannel `wellness`, SourceProvider `google_health`, `_ALLOWED_PROVIDERS_BY_CHANNEL`); `google-multi-account-oauth` delta (GOOGLE_SCOPE_SETS `health` registry, `scope_set` query param on `/api/oauth/google/start`, HTTP 400 unknown-set, `last_token_refresh_at`, `metadata.google_health_test_mode`, scope-selective revocation) |
| **bu-k5l35.2** | connector-google-health | PR #1108 | `connector-google-health` spec (owner account discovery, scope verification, OAuth token lifecycle, per-resource polling loops, Reconciled Stream, ingest envelope construction, checkpoint persistence, rate-limit discipline, health status reporting, source filter gate, filtered event flush, replay queue drain, Chronicler deferral) |
| **bu-k5l35.3.1** | module-google-health MCP tools | PR #1120 | `module-google-health` spec (module identity, credential resolution, scope verification at startup, all 8 read-only MCP tools: sleep_latest, sleep_history, hr_history, hrv_history, spo2_history, breathing_rate_history, activity_summary, vo2_max_latest) |
| **bu-k5l35.3.4** | mem_003 wellness predicates | PR #1124 | `butler-health` delta §"Wellness Memory Taxonomy" (9 predicates upserted into predicate_registry, idempotent migration) |
| **bu-k5l35.3.2** | Wellness ingest translator | PR #1126 | `butler-health` delta §"Wellness Envelope Ingestion Path" (translator, non-primary rejection, scope-revocation-during-ingest, malformed payload handling, replay idempotency) |
| **bu-k5l35.3.3** | gen-1 recon | Report at `e3-recon.md` (no PR; merged inline) | E3 cross-spec consistency check; discovered and filed bu-tr1x1 |
| **bu-k5l35.4** | Dashboard Google Accounts scope picker + status card | PR #1113 | `dashboard-google-accounts` delta (per-account scope-set picker, Google Health connector status card, test-mode warning banner) |
| **bu-k5l35.4.4** | E4 recon | Closed (no outstanding gaps) | Dashboard spec-to-code consistency confirmed |
| **bu-k5l35.5** | Docker Compose service | PR #1114 | `connector-google-health` service in docker-compose.yml and docker-compose.dev.yml, networks [db, backend, egress], depends_on correct services |
| **bu-tr1x1** | Predicate-name fix gap | Commit 448007e8 | Fixed predicate name mismatch discovered in gen-1 recon; `butler-health` translator uses canonical names from tasks.md §8.3 |
| **bu-k5l35.6** | Async Google Cloud Console verification | Open (P4, deferred) — see §bu-k5l35.6 status below | Google OAuth Restricted-scope review package; does not block archive |

---

## 2. Spec-to-Implementation Requirement Coverage

### butler-switchboard delta

| Requirement | Implementation | Status |
|---|---|---|
| `wellness` in SourceChannel | `roster/switchboard/tools/routing/contracts.py` | PASS (bu-k5l35.1, PR #1107) |
| `google_health` in SourceProvider | `roster/switchboard/tools/routing/contracts.py` | PASS (bu-k5l35.1, PR #1107) |
| `wellness/google_health` in `_ALLOWED_PROVIDERS_BY_CHANNEL` | contracts.py | PASS (bu-k5l35.1, PR #1107) |
| RFC 0003 amended | `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md` | PASS — `wellness/google_health` in canonical pairings list (line 50) |
| Identity resolution does not create temp contacts | Switchboard identity tools | PASS (bu-k5l35.2, PR #1108) |
| Routing to Health butler, non-interactive | contracts + routing | PASS (bu-k5l35.1) |

### google-multi-account-oauth delta

| Requirement | Implementation | Status |
|---|---|---|
| `GOOGLE_SCOPE_SETS['health']` with 3 scope URLs | `src/butlers/api/routers/oauth.py` | PASS (bu-k5l35.1, PR #1107) |
| `scope_set` query param on `/api/oauth/google/start` | oauth.py | PASS |
| HTTP 400 for unknown scope_set | oauth.py | PASS |
| Backward compat when scope_set omitted | oauth.py | PASS |
| `metadata JSONB` column on `public.google_accounts` | Alembic migration | PASS (bu-k5l35.1) |
| `last_token_refresh_at TIMESTAMPTZ` column | Alembic migration | PASS |
| `metadata.google_health_test_mode` set in OAuth callback | oauth.py callback | PASS |
| Scope-selective revocation endpoint | `DELETE /api/connectors/google-health/disconnect` | PASS (bu-k5l35.4, PR #1113) |
| Restricted-scope inline comments in catalog | `GOOGLE_SCOPE_SETS` source comments | PASS |
| contact_info row upserted on health scope grant | OAuth callback | PASS (bu-k5l35.2) |

### connector-google-health spec

| Requirement | Implementation | Status |
|---|---|---|
| Owner account discovery + scope verification | `src/butlers/connectors/google_health.py` startup probe | PASS (bu-k5l35.2, PR #1108) |
| Degraded mode when scopes missing | connector health state machine | PASS |
| Healthy / degraded / error only (no `broken`) | Verified by test `test_health_state_never_emits_broken_string` | PASS (confirmed in research-notes.md) |
| Per-resource poll loops (7 resources, default intervals) | `RESOURCE_BUNDLES` in google_health.py | PASS |
| First-run backfill (`GOOGLE_HEALTH_BACKFILL_DAYS`, default 30) | connector | PASS |
| Reconciled Stream (`view=reconciled`) | `_build_params` | PASS (research-notes.md §1.5) |
| `ingest.v1` envelope shape (channel=wellness, provider=google_health) | envelope construction | PASS |
| `control.idempotency_key = "google_health:<resource>:<record_id>"` | envelope construction | PASS |
| Checkpoint persistence via cursor_store 2-tuple key | cursor_store calls | PASS (research-notes.md §cursor key shape) |
| 429 handling: honour Retry-After, exponential backoff fallback | `google_health_client.py` | PASS (research-notes.md §1.3) |
| Rate-limit header capture as Prometheus metric | ConnectorMetrics | PASS |
| Heartbeat registration via shared heartbeat.py | google_health.py | PASS |
| IngestionPolicyEvaluator source filter gate | google_health.py | PASS |
| Filtered event flush to `connectors.filtered_events` | FilteredEventBuffer | PASS |
| Replay queue drain per poll cycle | google_health.py | PASS |
| Structural cost gates not invoked | confirmed in spec + code | PASS |
| Chronicler compatibility deferred | spec requirement included; no Chronicler adapter | PASS |
| OAuth token lifecycle via shared pipeline (not CredentialStore) | `google_health_client.py` uses `resolve_owner_entity_info()` | PASS |
| Access tokens never persisted | in-memory only | PASS |
| RFC 0008 row for connector-google-health | `about/legends-and-lore/rfcs/0008-deployment-network-security.md` line 83 | PASS |

### module-google-health spec

| Requirement | Implementation | Status |
|---|---|---|
| `GoogleHealthModule(Module)` with `name="google_health"`, `dependencies=[]` | `src/butlers/modules/google_health.py` | PASS (bu-k5l35.3.1, PR #1120) |
| `migration_revisions()` returns None | google_health.py | PASS |
| `on_startup()` resolves primary Google account via registry | google_health.py | PASS |
| Degraded tools when scopes missing | all tools return actionable error | PASS |
| 8 MCP tools registered | sleep_latest, sleep_history, hr_history, hrv_history, spo2_history, breathing_rate_history, activity_summary, vo2_max_latest | PASS |
| Tools query fact store (memory_search), NOT Google Health API directly | google_health.py tools | PASS |
| `[modules.google_health]` in roster/health/butler.toml | butler.toml | PASS (bu-k5l35.3.1) |

### butler-health delta

| Requirement | Implementation | Status |
|---|---|---|
| 9 wellness predicates in predicate_registry (idempotent migration) | Alembic migration, health butler chain | PASS (bu-k5l35.3.4, PR #1124) |
| Translator: envelope → memory facts with correct predicates | Health butler translator | PASS (bu-k5l35.3.2, PR #1126) |
| Predicate names match tasks.md §8.3 canonical list | Fixed by bu-tr1x1 (commit 448007e8) | PASS |
| Non-primary account rejection | translator | PASS |
| Scope-revocation during in-flight envelope → still store fact | translator | PASS |
| Malformed payload → log warning, skip, no crash | translator | PASS |
| Replay idempotency | Switchboard deduplication + translator safety | PASS |

### dashboard-google-accounts delta

| Requirement | Implementation | Status |
|---|---|---|
| Per-account scope-set picker (Calendar, Drive, Google Health toggles) | `frontend/src/components/settings/GoogleOAuthSection.tsx` | PASS (bu-k5l35.4, PR #1113) |
| Scope grant wired to `/api/oauth/google/start?scope_set=health&force_consent=true` | frontend component | PASS |
| Revocation wired to `DELETE /api/connectors/google-health/disconnect` with confirmation modal | frontend component | PASS |
| Google Health connector status card (`GET /api/connectors/google-health/status` polled every 30s) | dashboard component + API route | PASS |
| Test-mode orange banner (`metadata.google_health_test_mode = true`) | frontend component | PASS |
| Approaching-expiry red banner (`last_token_refresh_at > 5d6h` on test-mode account) | frontend component | PASS |
| `GET /api/connectors/google-health/status` endpoint | roster/health/api/router.py | PASS |
| `DELETE /api/connectors/google-health/disconnect` endpoint | roster/health/api/router.py | PASS |

### Docker Compose

| Requirement | Implementation | Status |
|---|---|---|
| `connector-google-health` service in docker-compose.yml | line 530 | PASS (bu-k5l35.5, PR #1114) |
| `connector-google-health` service in docker-compose.dev.yml | present | PASS |
| Networks: [db, backend, egress] | docker-compose.yml line 541 | PASS |
| Depends on: log-init, migrations, butlers-up, oauth-gate | docker-compose.yml lines 543-550 | PASS |
| Health port 40090 | CONNECTOR_HEALTH_PORT: "40090" | PASS |

---

## 3. Pre-Archive Checklist

| Gate | Result | Evidence |
|---|---|---|
| `openspec validate google-health-connector --strict` | **PASS** | Output: `Change 'google-health-connector' is valid` |
| `uv run ruff check src/ tests/ roster/ conftest.py --output-format concise` | **PASS** | Output: `All checks passed!` |
| `uv run ruff format --check src/ tests/ roster/ conftest.py -q` | **PASS** | No output (clean) |
| `make test-qg` | **PASS** | 2917 passed, 4 skipped, 30 warnings in 145.79s |
| `about/heart-and-soul/v1.md` Connectors inventory has Google Health entry | **PASS** | Added in commit a6204bb7 (this session) |
| `about/lay-and-land/components.md` §3 Connectors table has connector-google-health row | **PASS** | Added in commit a6204bb7 (this session); port 40090, stability Stable |
| RFC 0003 has `wellness/google_health` canonical pair | **PASS** | `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md` line 50: `wellness/google_health` present |
| RFC 0008 has `connector-google-health` row | **PASS** | `about/legends-and-lore/rfcs/0008-deployment-network-security.md` line 83: `connector-google-health | x | x | | x | Google Health API (health.googleapis.com)` |
| `research-notes.md` captures final discovery findings | **PASS** | `openspec/changes/google-health-connector/research-notes.md` present with all §1 items resolved |

---

## 4. bu-k5l35.6 Status

Open, P4 (deprioritized 2026-04-25 per owner decision to remain in Google OAuth test mode for single-owner deployment). The Restricted-scope verification package submission is async and not a blocker for archiving google-health-connector. The owner will flip to production mode if/when they decide to submit.

The integration is fully functional in test mode for a single-developer self-hosted deployment. The 7-day refresh token expiry in test mode is surfaced to the owner via the dashboard's orange/red warning banner (implemented in bu-k5l35.4). The connector operates in degraded mode until re-consent and transitions to healthy automatically on scope re-grant.

This is explicitly acceptable per the Butlers project's single-owner deployment model and per the `proposal.md §Impact` note: "For a self-hosted single-owner deployment, test mode is sufficient to operate — verification is needed only if the codebase is ever consumed by non-developer end users."

---

## 5. Notes on Gen-1 Recon Findings (bu-k5l35.3.3)

The gen-1 recon (E3 recon) found one gap: a predicate name mismatch in the
wellness ingest translator. The translator was using non-canonical predicate
names that did not match the D5 taxonomy defined in `butler-health` delta.
This was filed as **bu-tr1x1** and fixed in commit **448007e8**. No open
gaps remain from the gen-1 recon.

---

## 6. Archive Decision: READY

All E-level epics are closed. All pre-archive checklist gates pass. The only
open bead (bu-k5l35.6) is explicitly async, P4, and deferred by owner
decision. The `openspec validate` check passes with `--strict`. The
implementation is fully traceable to the spec.

**The google-health-connector change is ready to archive.**
