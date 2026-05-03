## 1. Spec Landing

- [ ] 1.1 Land this proposal, design, and delta specs (`system-overview-page`, `butler-base-spec`) via the standard OpenSpec review workflow.
- [ ] 1.2 Confirm with the reviewer that the privacy contract (owner-only egress catalog assertion) is acceptable for v1 before implementation begins.
- [ ] 1.3 Verify audit log coverage for egress paths (LLM API, Telegram, Google APIs, Gmail SMTP); file follow-up beads for any paths not captured before shipping the EgressCatalogTile.

## 2. Backend: API Endpoints (bu-ngfzz.2)

- [ ] 2.1 Create `src/butlers/api/routers/system.py` with the five endpoints: `GET /api/system/instance`, `GET /api/system/database`, `GET /api/system/backups`, `GET /api/system/egress`, `GET /api/system/butlers/heartbeat`.
- [ ] 2.2 Create co-located `src/butlers/api/routers/system_models.py` (or `models.py`) with Pydantic response models: `InstanceFacts`, `DatabaseFacts`, `SchemaSize`, `TableSize`, `BackupFacts`, `BackupEvent`, `EgressCatalog`, `EgressActor`, `HeartbeatFacts`, `ButlerHeartbeat`.
- [ ] 2.3 Wire the router into `src/butlers/api/app.py` router registration (confirm auto-discovery or add explicit include).
- [ ] 2.4 Implement the owner-contact assertion for `GET /api/system/egress`; return HTTP 403 if the authenticated session cannot be mapped to `public.contacts WHERE roles @> ARRAY['owner']`.
- [ ] 2.5 Implement backup recency: discover the backup strategy (Minio/S3 or filesystem), implement the source-reachable health check, and handle graceful degradation when the source is unreachable.
- [ ] 2.6 Implement actor registry (server-side constant mapping `actor_id` to `display_name`); populate it for all currently known egress paths.
- [ ] 2.7 Write tests in `tests/api/test_system_router.py` covering: each endpoint's happy path, the egress 403 path, the backup degraded path, and the DB error 503 path.

## 3. Frontend: Route and Navigation (bu-ngfzz.3)

- [ ] 3.1 Add `/system` route to `frontend/src/router.tsx` alongside the Telemetry routes.
- [ ] 3.2 Add a "System" nav entry to the Telemetry section in `nav-config.ts`; mark it with no butler-presence filter.
- [ ] 3.3 Create `frontend/src/pages/SystemPage.tsx` using the `<Page archetype='overview'>` shell (after Vertical A lands the shared Page primitive).
- [ ] 3.4 Add TypeScript types for the five response models to `frontend/src/api/types.ts`.
- [ ] 3.5 Add API client functions for the five system endpoints to `frontend/src/api/client.ts` or a new `frontend/src/api/system.ts`.

## 4. Frontend: Tile Components (bu-ngfzz.4 through bu-ngfzz.6)

- [ ] 4.1 Build `VersionTile`: displays `version`, `uptime_seconds` (formatted as human duration), and `started_at` (via `<Time>`).
- [ ] 4.2 Build `UptimeTile`: visual uptime indicator; auto-refreshes every 30s.
- [ ] 4.3 Build `DbSizeTile`: shows `total_size_bytes` (formatted), per-schema bar breakdown, and top tables.
- [ ] 4.4 Build `BackupTile`: shows `last_backup_at` (via `<Time>`), `last_backup_size_bytes`, and the degraded state when `backup_source_reachable = false`.
- [ ] 4.5 Build `EgressCatalogTile`: shows the actor list with `display_name`, `last_seen_at` (via `<Time>`), and `total_calls`; shows `catalog_covers_from` as a footnote; shows 403 state if the session cannot assert owner contact.
- [ ] 4.6 Build `ButlerHeartbeatTile`: per-butler row with `name`, `last_heartbeat_at` (via `<Time>`), `heartbeat_age_seconds` formatted as freshness indicator, and `active_session_count`.

## 5. Verification and Reconciliation (bu-ngfzz gen-1)

- [ ] 5.1 Run `openspec validate system-page-capability` and confirm no validation errors.
- [ ] 5.2 Run `openspec verify system-page-capability` against the landed implementation to confirm all spec requirements are satisfied.
- [ ] 5.3 Manually verify the egress catalog on a running instance: confirm at least Anthropic Claude API appears in the actor list with a non-zero `total_calls`.
- [ ] 5.4 Verify the backup tile graceful degradation: confirm HTTP 200 with `backup_source_reachable = false` when Minio is unreachable.
- [ ] 5.5 Verify the owner-contact 403 gate: confirm the egress endpoint returns 403 when called without a valid dashboard session.
- [ ] 5.6 Close gen-1 reconciliation bead once all `openspec verify` checks pass.

## 6. Open Question Resolution

- [ ] 6.1 Resolve: does the audit log uniformly cover all egress paths? File follow-up beads for any gaps before shipping the EgressCatalogTile.
- [ ] 6.2 Resolve: should database growth rate be shown in v1? If yes, design and implement a periodic snapshot mechanism (scheduled job + new migration) before closing the DbSizeTile task.
- [ ] 6.3 Document the backup strategy in `AGENTS.md` after the implementation bead discovers it; link from the BackupTile implementation notes.
