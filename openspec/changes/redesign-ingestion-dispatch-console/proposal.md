## Why

The `/ingestion` dashboard is a single-route surface (`?tab=` URL param) with four mixed-concern tabs, offset pagination that double-counts via `UNION ALL` on every poll, no audit gate on event-payload reads (live PII leak through `decomposition_output`), no UI contract for connector lifecycle actions (pause/run-now/disconnect/rotate-token/reauth), no `priority_contacts` infrastructure (Gmail relies on a flat-file `GMAIL_KNOWN_CONTACTS_PATH` env var), and no spec backing for ~60% of the proposed redesign surface. The redesign introduces a Dispatch-language information architecture with first-class sub-routes, doctrine-compliant lifecycle gates, replay idempotency policy, and the structured `priority_contacts` table that supersedes the flat-file. Now: the design language and prototype assets exist in `pr/overview/ingestion-redesign/`, four Phase 1 review rounds + four Phase 2 agent passes have reconciled the proposal against doctrine, and bu-ty7gh (the audit gate) has shipped to main — clearing the prerequisite for Wave 1.

## What Changes

- **BREAKING**: Replace offset+total pagination on `GET /api/ingestion/events` with keyset cursor; `use-ingestion-events.ts` hook shape changes (no `total` field).
- **BREAKING**: Promote `/ingestion/connectors`, `/ingestion/filters`, `/ingestion/history` to first-class routes. Tab-param URLs 301-redirect to the new routes.
- Introduce `priority_contacts(contact_id FK → public.contacts.id, butler, added_at, added_by)` table + CRUD API + Gmail wiring. Deprecate `GMAIL_KNOWN_CONTACTS_PATH` (one-cycle fallback, then removed).
- Introduce `channel_defaults(channel, default_policy_json, updated_at)` table + CRUD API.
- Introduce `connector_replay_history` lineage (sourced from `audit_log` initially; promote to table only if query cost demands it).
- Add connector lifecycle ceremony: `pause`, `run-now` (audit-only), `disconnect`, `rotate-token`, `reauth` (Approvals-gated). `connector_registry` rows soft-delete via `deleted_at`.
- Add bulk replay endpoint with `FOR UPDATE SKIP LOCKED` + max-batch-size 50. Block `source_channel='email'` at handler until idempotency policy is ratified.
- Add `GET /api/ingestion/pipeline?window=24h` (PipelineStats) via Prometheus PromQL + 60s TTL cache (degraded mode returns zeros + `aggregates_available: false`).
- Add `GET /api/ingestion/connectors/available` for discovery.
- Add bulk rule operations (enable/disable/delete) on ingestion rules. Enforce `block`-only action for `connector:*` scope at the handler (HTTP 400 on any other action).
- Wire audit gate (`audit.append()`) on every `/ingestion` mutation: rule CRUD, lifecycle actions, priority-contact add/remove, channel-default updates, bulk replay.
- Resolve `sender_identity` via `resolve_contact_by_channel()` in event drawer; show "unresolved" indicator on miss.
- Add Saved Views (client-side localStorage), connector attention strip, hour-group headers, flame strip (duration-proportional approximation — labeled in tooltip), drawer anchor-scroll.
- Explicit deferrals (out of scope): live SSE stream, full-text payload search, DSL editor, native per-step token tracking, Timeline virtualisation, Gmail non-idempotent replay.

## Capabilities

### New Capabilities
- `ingestion-ui-information-architecture`: route hierarchy, 301 redirects, filter control contracts, sub-route wrappers for FiltersTab/BackfillHistoryTab (no rewrite), AttentionStrip dependency, resolved-contact rendering in drawer.
- `ingestion-priority-contacts`: schema, CRUD API, audit/retention contract, GmailPolicyEvaluator wiring (15-min TTL), `GMAIL_KNOWN_CONTACTS_PATH` deprecation path.
- `connector-replay-idempotency-policy`: per-channel replay safety classification (`connector_registry.replay_safe`), email block, bulk handler concurrency contract (FOR UPDATE SKIP LOCKED + max-batch-size 50), 90-day history retention.
- `connector-lifecycle-ceremony`: per-action gate matrix (audit-only vs Approvals-gated), credential-masking contract on rotate-token, soft-delete contract for `connector_registry`, reauth blocked until `connector-oauth-scope-surface` exists.
- `connector-state-aggregates`: Prometheus + 60s TTL cache contract for `spark24h`/`rate1h`/`routedPct`/`filtered24h`; degraded-mode response shape; prohibition on per-request UNION ALL aggregation at poll cadence.

### Modified Capabilities
- `ingestion-event-registry`: replace offset+total with keyset cursor (or ≥30s TTL count). Add `GET /api/ingestion/pipeline?window=24h` contract. Add `GET /api/ingestion/connectors/available`. State flame strip is duration-proportional only. 90-day replay history retention.
- `ingestion-policy`: bulk enable/disable/delete. Handler-level enforcement of connector-scope → block-only (HTTP 400 on other actions).
- `connector-replay-queue`: extended via ADDED requirements only (existing replay behavior is unchanged). New: bulk replay handler concurrency contract (delegating idempotency to new `connector-replay-idempotency-policy` spec); replay-history endpoint.
- `connector-base-spec`: lifecycle ceremony requirements (per-action gates); discovery endpoint; lines 350-354 amended from DB rollup to Prometheus + 60s TTL; breadcrumb `href` → `/ingestion/connectors`.

## Impact

- **Code**: `frontend/src/pages/IngestionPage.tsx` (split into sub-route pages), `frontend/src/router.tsx` (sub-routes + redirects), `frontend/src/components/ingestion/*` (TimelineTab additions, ConnectorsListPage extraction, AttentionStrip), `frontend/src/hooks/use-ingestion-events.ts` (cursor shape), `src/butlers/api/routers/ingestion_events.py` (cursor pagination, pipeline, available, replay-history, lifecycle handlers, priority-contacts, channel-defaults, bulk operations), `src/butlers/core/ingestion_events.py` (keyset queries, replay concurrency), `src/butlers/modules/connectors/gmail.py` (DB-backed priority lookup; flat-file deprecation), `src/butlers/migrations/versions/` (priority_contacts, channel_defaults, replay_safe column on connector_registry).
- **APIs**: 13 new/changed endpoints across `/api/ingestion/*`. Hook contract change is BREAKING for `useIngestionEvents` consumers.
- **DB**: new tables `priority_contacts`, `channel_defaults`; new column `connector_registry.replay_safe`; new column `connector_registry.deleted_at` (soft-delete); audit_log entries become source of replay history (no new table in v1).
- **Cross-change coordination**:
  - `connector-detail-archetype-conformance` (in-flight change) must amend its breadcrumb `href` from `?tab=connectors` to `/ingestion/connectors` and merge before the `/ingestion/connectors` list-route bead starts.
  - `redesign-settings-dispatch-console` (in-flight change, bu-do5q0) renames `complexity_tier.discretion → specialty`. Any ingestion bead touching connector model resolution is gated on that rename merging or being explicitly deferred.
- **Dependencies**: existing `prometheus.py` aggregation path; existing `audit.append()` infrastructure (bu-ty7gh shipped); Approvals module (already wired).
- **Doctrine**: closes 7 doctrine gaps identified in research-02 (UI IA, lifecycle ceremony, OAuth scope, bulk ops, per-connector cost visibility, per-butler filtering surface, discretion-tier visibility). Honors all 10 hard constraints; respects token-economics rule (no per-event LLM calls anywhere on the surface).
- **Test coverage**: ~8 new endpoint groups need API tests; component tests for new sub-route pages; Playwright smoke for sub-route navigation + 301 redirects (after bu-vavsq Playwright bootstrap lands).
- **Telemetry**: existing `IngestionPolicyMetrics` unchanged; new Prometheus query patterns documented in `connector-state-aggregates` spec.
