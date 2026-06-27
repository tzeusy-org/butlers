# v1 Status Matrix

**Last updated:** 2026-06-28
**Branch:** current `main` (as of commit `10c8a1bd6`)

Maps every success criterion from [`v1.md`](v1.md) to its current status and
concrete evidence. Honest by design: partial and unproven are not failure states
— they are the signal that tells us what work remains.

---

## Key

| Status | Meaning |
|--------|---------|
| **implemented** | Code complete, unit/contract tested, observable in CI |
| **partial** | Core components present; named gaps or missing coverage |
| **unproven** | Code exists but operational/field evidence not yet accumulated |

---

## Success Criteria (v1.md §"Success Criteria")

### SC-1 — All staffers and domain butlers run concurrently

> All three staffers and nine domain butlers (including Chronicler) run
> concurrently in a single deployment and handle their declared
> responsibilities.

**Status: partial**

Code complete for all 12 butler roles. A concurrent production run for ≥7
days has not been attested (see SC-6).

| Component | Evidence |
|-----------|----------|
| Switchboard staffer | `roster/switchboard/butler.toml` — `type = "staffer"` |
| Messenger staffer | `roster/messenger/butler.toml` — `type = "staffer"` |
| QA staffer | `roster/qa/butler.toml` — `type = "staffer"`, patrol cron `*/10 * * * *` |
| General, Health, Relationship, Finance, Education, Travel, Home, Lifestyle, Chronicler | All have `roster/<name>/butler.toml`, `MANIFESTO.md`, migrations |
| Chronicler | `roster/chronicler/butler.toml` — 13 scheduled projection jobs; `roster/chronicler/` specialised module |
| Daemon multi-process launch | `docker-compose.yml` + `tests/smoke/test_daemon_lifecycle.py` |

**Gap:** "run concurrently and handle declared responsibilities" is an
operational claim requiring a live system; CI validates individual daemon
lifecycle (smoke tests) but not 12-butler simultaneous uptime.

---

### SC-2 — Switchboard routes from all connectors with >90% accuracy

> The Switchboard staffer correctly routes messages from all active connectors
> (Telegram, Gmail, Discord, Google Calendar, Google Drive, Home Assistant,
> OwnTracks, Spotify, WhatsApp) to the appropriate domain butler with >90%
> classification accuracy.

**Status: partial**

Routing infrastructure is implemented and all listed connector types have
working ingestion adapters — except Discord, which is explicitly
`STATUS: TARGET-STATE (Not Production-Ready)`. Routing accuracy has been
benchmarked on local/alternative models only; the primary model (Claude via
claude-code) has no captured benchmark run, and the best recorded result
(80.6%) is below the 90% threshold.

| Evidence item | Detail |
|---------------|--------|
| Telegram bot connector | `src/butlers/connectors/telegram_bot.py` |
| Telegram user client | `src/butlers/connectors/telegram_user_client.py` |
| Gmail connector | `src/butlers/connectors/gmail.py` |
| Google Calendar connector | `src/butlers/connectors/google_calendar.py` |
| Google Drive connector | `src/butlers/connectors/google_drive.py` |
| Home Assistant connector | `src/butlers/connectors/home_assistant.py` — `STATUS: COMPLETE` |
| OwnTracks connector | `src/butlers/connectors/owntracks.py` — webhook server, production-ready |
| Spotify connector | `src/butlers/connectors/spotify.py` — adaptive polling, production-ready |
| WhatsApp user client | `src/butlers/connectors/whatsapp_user_client.py` — Go sidecar bridge |
| **Discord connector** | `src/butlers/connectors/discord_user.py` — "**DRAFT — v2-only WIP, not production-ready**"; auth flow, scope validation, consent UI, error recovery all incomplete |
| Routing benchmark harness | `tests/benchmarks/switchboard/` — not run in CI |
| Accuracy results | `tests/benchmarks/switchboard/results.md` — best: **80.6%** (opencode-go/glm-5, 100 scenarios); Claude not benchmarked |
| Switchboard routing tests | `tests/contracts/test_mcp_only_inter_butler.py` — contract-level; not accuracy tests |

**Gaps:**
1. Discord connector is draft/v2-only; cannot be included in "all active connectors."
2. Accuracy >90% threshold unverified for the primary model (Claude).
3. Benchmark not included in CI; accuracy regressions could go undetected.

---

### SC-3 — Scheduled tasks fire reliably with auto-retry

> Scheduled tasks fire reliably on cron cadences with automatic retry on
> transient failures.

**Status: implemented**

Cron scheduler runs in every butler daemon. Retry semantics keep failed
tasks in `pending` state for the next tick. All butlers define `[[butler.schedule]]`
cron entries.

| Evidence | Location |
|----------|----------|
| Cron scheduler | `src/butlers/core/scheduler.py` |
| Tick-based retry | `scheduler.py::_tick_deferred_notification_pass()` — "Keep status=pending for next-tick retry" |
| Butler schedule declarations | All `roster/*/butler.toml` — `[[butler.schedule]]` blocks with cron strings |
| Scheduler tests | `tests/core/` — scheduler unit tests |
| Smoke test — daemon lifecycle | `tests/smoke/test_daemon_lifecycle.py` |

**Note:** Retry is tick-based (pending task re-fires on next cron tick); there
is no configurable max-retry cap or exponential backoff for job failures. This
is sufficient for most transient failures but may not handle sustained outages.

---

### SC-4 — Memory subsystem stores, retrieves, and consolidates facts

> The memory subsystem stores, retrieves, and consolidates facts across butler
> sessions.

**Status: implemented**

Three-tier architecture (Eden → Mid-Term → Long-Term) is implemented with
LRU-based promotion/eviction and cross-butler fact sharing.

| Evidence | Location |
|----------|----------|
| Tier model | `src/butlers/api/models/memory.py` — `Episode` (Eden/raw), `Fact` (Mid-Term), `Rule` (Long-Term) |
| Fact storage | `src/butlers/modules/memory/storage.py` — `store_fact()`, `_upsert_fact()` |
| Consolidation | `src/butlers/modules/memory/consolidation.py`, `consolidation_executor.py` |
| Retrieval / search | `src/butlers/modules/memory/search.py`, `search_vector.py` |
| Re-embedding | `src/butlers/modules/memory/reembedding.py` |
| Maturity promotion | `storage.py` — candidate → established → proven lifecycle |
| Cross-butler sharing | `public.memory_catalog` table (referenced in migrations) |
| Entity resolution | `src/butlers/modules/memory/tools/entities.py` |
| Memory tests | `tests/modules/memory/` |

---

### SC-5 — Dashboard provides real-time visibility

> The dashboard provides real-time visibility into butler status, sessions,
> contacts, and ingestion flow.

**Status: implemented**

FastAPI backend (port 41200) and Vite frontend (port 41173 dev) are fully
built out. All required views exist: butler status, session browser, contacts,
ingestion monitoring, settings console, audit log, webhooks, data ops.

| Feature area | Evidence |
|--------------|----------|
| Butler status | `frontend/src/pages/ButlersPage.tsx`, `ButlerDetailPage.tsx` |
| Session browser | `frontend/src/pages/SessionsPage.tsx`, `SessionDetailPage.tsx` |
| Contact / identity views | `frontend/src/pages/EntityDetailPage.tsx`, `GroupsPage.tsx`, `SocialMapPage.tsx` |
| Ingestion monitoring | `frontend/src/pages/IngestionPage.tsx`, `IngestionConnectorsPage.tsx`, `IngestionTimelinePage.tsx` |
| Settings console (`/settings`) | `frontend/src/pages/SettingsConsolePage.tsx` |
| Models tab | `frontend/src/pages/SettingsModelsPage.tsx` |
| Spend dashboard | `frontend/src/pages/SettingsSpendPage.tsx` |
| Permissions / data-ops | `frontend/src/pages/SettingsPermissionsPage.tsx` |
| Audit log | `frontend/src/pages/AuditLogPage.tsx`; `src/butlers/api/routers/audit.py` |
| Webhooks | `src/butlers/api/routers/webhooks.py` — HMAC-SHA256, test-fire endpoint |
| Data ops | `src/butlers/api/routers/data_ops.py` — 60-min signed URL export + phrase-gated wipe |
| Insight delivery tile | `frontend/src/components/system/InsightDeliveryTile.tsx` |
| Backend API | `src/butlers/api/` — per-butler routers, auto-discovered via `router_discovery.py` |

---

### SC-6 — 7 consecutive days without manual intervention

> The system runs for 7 consecutive days without manual intervention beyond
> LLM API key rotation.

**Status: unproven**

Infrastructure for autonomous operation exists (QA patrol every 10 min,
self-healing module, cron scheduler, context bus). No CI gate or uptime
attestation tracks this. This criterion requires accumulated field evidence.

| Supporting infrastructure | Evidence |
|---------------------------|----------|
| QA patrol cron | `roster/qa/butler.toml` — `cron = "*/10 * * * *"` |
| Self-healing module | `src/butlers/modules/self_healing/__init__.py` — `SelfHealingModule` |
| Smoke tests | `tests/smoke/test_clean_start.py`, `test_health.py`, `test_route_inbox_recovery.py` |
| Clean-start validation | `tests/smoke/test_scaffolding.py` |

**Note:** "7 days without manual intervention" is a runtime criterion, not
a code property. It will be attested after a sustained live deployment.

---

### SC-7 — Owner uses system daily for three domains

> The owner uses the system daily for at least three domains (e.g., health
> tracking, relationship context, general assistance).

**Status: unproven**

This is an adoption/behavioral criterion, not a code property. Butlers for
all expected domains (health, relationship, general, finance, education, travel,
home, lifestyle) are built and operational. Daily-use evidence requires a live
deployment and user data that cannot be observed from the repository.

---

### SC-8 — Proactive insights delivered at sustainable cadence; adaptive ratchet works

> Proactive insights are delivered at a sustainable cadence without manual
> tuning, and the adaptive ratchet correctly reduces frequency on
> disengagement.

**Status: implemented (unproven in production)**

EPIC C (proactive insight delivery) has fully landed. All three
phases of the RFC 0011 pipeline are wired end-to-end: butler-side insight
generation, Switchboard brokering (dedup, budget, adaptive ratchet, anti-spam),
and durable delivery via Messenger. The dashboard surfaces live delivery state.
A gen-1 spec-to-code reconciliation confirmed faithful implementation across
98 tests. Field-proven delivery cadence at scale is not yet attested.

| Evidence | Detail |
|----------|--------|
| Broker implementation | `roster/switchboard/tools/insight/broker.py` — `propose_insight_candidate()`, `expire_candidates()`, `filter_by_cooldown()`, `deduplicate_candidates()`, `compute_effective_budget()`, `check_and_update_engagement()`, `check_total_disengagement_auto_off()`, `delivery_cycle()` |
| Scheduled delivery cron | `roster/switchboard/butler.toml` — `cron = "0 8 * * *"`, job `insight_delivery_cycle` |
| API endpoint | `GET /api/system/insights/delivery-state` (`src/butlers/api/routers/system.py`) |
| Dashboard tile | `frontend/src/components/system/InsightDeliveryTile.tsx` |
| Test coverage | `tests/modules/test_module_insight_broker.py` (11 tests), `tests/modules/test_insight_engine.py` (69 tests), `tests/api/test_system_insight_delivery.py` (8 tests), `tests/jobs/test_insight_delivery_job.py` (10 tests) — **98 tests total** |
| Adaptive ratchet | `broker.py::compute_effective_budget()` — one-way ratchet; `check_total_disengagement_auto_off()` — auto-off on sustained disengagement |
| Global budget + cooldowns | `broker.py` — `public.insight_candidates`, `public.insight_cooldowns`, per-key cooldown enforcement |

**Why "unproven in production":** The code faithfully implements the spec
(confirmed by reconciliation), but the adaptive ratchet's real-world behaviour
— whether frequency actually self-adjusts to the owner's engagement patterns
without manual tuning — requires a live deployment window to observe. The
implementation is complete; the field result is pending.

---

## Component Coverage ("What v1 Ships")

A condensed view of the broader feature set listed in v1.md, for orientation.

### Core Infrastructure

| Component | Status | Evidence |
|-----------|--------|----------|
| Multi-butler daemon | **implemented** | `src/butlers/core/spawner.py`, `docker-compose.yml` |
| Switchboard routing | **implemented** | `roster/switchboard/`, `src/butlers/core/route_inbox.py` |
| Module system | **implemented** | `src/butlers/modules/base.py`, `registry.py` — topological sort |
| Task scheduler | **implemented** | `src/butlers/core/scheduler.py` |
| LLM CLI spawner | **implemented** | `src/butlers/core/spawner.py` — `Spawner` class, locked-down MCP configs |
| Session logging | **implemented** | `src/butlers/core/butler_logging.py` |
| State store | **implemented** | `src/butlers/core/state.py` — PostgreSQL JSONB KV |

### Modules

| Module | Status | Evidence |
|--------|--------|----------|
| Memory | **implemented** | `src/butlers/modules/memory/` — Eden/Mid-Term/Long-Term tiers |
| Calendar | **implemented** | `src/butlers/modules/calendar.py` |
| Email | **implemented** | `src/butlers/modules/email.py` |
| Telegram | **implemented** | `src/butlers/modules/telegram.py` |
| Approvals | **implemented** | `src/butlers/modules/approvals/` |
| Pipeline | **implemented** | `src/butlers/modules/pipeline.py` |
| Metrics | **implemented** | `src/butlers/modules/metrics/` |
| Mailbox | **implemented** | `src/butlers/modules/mailbox/` |
| Self-healing | **implemented** | `src/butlers/modules/self_healing/` |
| Home Assistant | **implemented** | `roster/home/modules/__init__.py` — `HomeAssistantModule` |
| Google Drive | **implemented** | `src/butlers/modules/google_drive/` — `GoogleDriveModule` |
| WhatsApp | **implemented** | `src/butlers/modules/whatsapp/` — `WhatsAppModule` |
| Steam | **implemented** | `src/butlers/modules/steam.py` (`SteamModule`); enabled in `roster/lifestyle/butler.toml` (`[modules.steam]`); `tests/modules/test_module_steam.py` |
| Insight broker | **implemented** | `roster/switchboard/modules/insight_broker.py` (see SC-8) |

### Connectors

| Connector | Status | Evidence |
|-----------|--------|----------|
| Telegram bot | **implemented** | `src/butlers/connectors/telegram_bot.py` |
| Telegram user client | **implemented** | `src/butlers/connectors/telegram_user_client.py` |
| Gmail | **implemented** | `src/butlers/connectors/gmail.py` |
| Discord | **partial** | `src/butlers/connectors/discord_user.py` — "**DRAFT — v2-only WIP**"; auth, scope, consent, retry incomplete |
| Heartbeat | **implemented** | `src/butlers/connectors/heartbeat.py` |
| Live listener | **implemented** | `src/butlers/connectors/live_listener/` |
| Google Calendar | **implemented** | `src/butlers/connectors/google_calendar.py` |
| Google Drive | **implemented** | `src/butlers/connectors/google_drive.py` |
| Home Assistant | **implemented** | `src/butlers/connectors/home_assistant.py` — `STATUS: COMPLETE` |
| OwnTracks | **implemented** | `src/butlers/connectors/owntracks.py` |
| Spotify | **implemented** | `src/butlers/connectors/spotify.py` |
| Google Health | **implemented** | `src/butlers/connectors/google_health.py` |
| WhatsApp user client | **implemented** | `src/butlers/connectors/whatsapp_user_client.py` |
| Steam | **implemented** | `src/butlers/connectors/steam.py` (`SteamConnector`, per-data-type polling); `src/butlers/steam_account_registry.py`, `src/butlers/api/routers/steam.py`; `tests/connectors/test_steam_connector.py` |

### Identity System

| Component | Status | Evidence |
|-----------|--------|----------|
| Shared entity registry | **implemented** | `public.entities` table; roles live on `public.entities.roles` |
| Cross-channel identity resolution | **implemented** | `relationship.entity_facts` `has-handle` triples (Telegram IDs, email addresses, Discord IDs) joined to `public.entities` (`src/butlers/identity.py`) |
| Owner bootstrapping | **implemented** | `src/butlers/core/owner.py` — resolves the owner via `public.entities WHERE 'owner' = ANY(roles)` |

### Situational Awareness

| Component | Status | Evidence |
|-----------|--------|----------|
| Context bus | **implemented** | `src/butlers/context_bus.py` — `public.user_context`, TTL signals; `tests/core/test_context_bus.py` |
| Proactive insight delivery | **implemented (unproven in production)** | See SC-8 above |

### Observability

| Component | Status | Evidence |
|-----------|--------|----------|
| OpenTelemetry instrumentation | **implemented** | `src/butlers/core/telemetry.py` — `init_telemetry()`, OTLP exporter |
| Telemetry collection & routing | **partial** | `docker-compose.observability.yml` uses `otel/opentelemetry-collector-contrib:0.105.0`; v1.md names "Grafana Alloy" but the deployed stack uses OTel Collector. Functionally equivalent for trace/metric routing; name discrepancy only. |
| Tempo | **implemented** | `docker-compose.observability.yml` — `grafana/tempo:2.5.0`, config at `tempo/config.yaml` |
| Prometheus | **implemented** | `prometheus/prometheus.yml`; `src/butlers/modules/metrics/prometheus.py` |
| Grafana | **implemented** | `docker-compose.observability.yml` — `grafana/grafana:11.1.0`, pre-provisioned dashboards |

---

## Summary

| Criterion | Status |
|-----------|--------|
| SC-1 All butlers run concurrently | partial — code complete; production concurrent run unproven |
| SC-2 Switchboard >90% routing accuracy | partial — Discord draft; accuracy benchmarked only on non-primary models |
| SC-3 Scheduled tasks with auto-retry | **implemented** |
| SC-4 Memory stores/retrieves/consolidates | **implemented** |
| SC-5 Dashboard real-time visibility | **implemented** |
| SC-6 7-day uninterrupted run | **unproven** — requires field observation |
| SC-7 Daily owner use across 3 domains | **unproven** — adoption criterion |
| SC-8 Proactive insights + adaptive ratchet | implemented, **unproven in production** |

**4 implemented · 2 partial · 2 unproven**

The two partial criteria (SC-1, SC-2) share the same root cause: operational
deployment evidence. SC-2 additionally has a concrete code gap (Discord
connector not production-ready) and a missing benchmark for the primary model.
The two unproven criteria (SC-6, SC-7) are field observations that no amount of
code can substitute for — they will resolve naturally once the system reaches a
sustained production run.
