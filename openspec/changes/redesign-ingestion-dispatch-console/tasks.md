## 1. Prerequisites (gating closure)

- [x] 1.1 bu-ty7gh: audit gate on `GET /api/ingestion/events/{id}` shipped to main (PR #1718)
- [x] 1.2 File a follow-up bead to re-open `connector-detail-archetype-conformance` and amend the breadcrumb `href` from `?tab=connectors` to `/ingestion/connectors` (sibling change's tasks are marked complete; the one-line amendment lives in its `specs/connector-base-spec/spec.md:48`). The follow-up bead MUST be filed at change-creation time and MUST merge before §3.4 starts.
- [x] 1.3 Cross-coupling note: no Wave 2/3 task enumerated in this changeset invokes the discretion model evaluator — they only mutate `connector_registry` and call Approvals. The `redesign-settings-dispatch-console` `discretion → specialty` rename does not gate any task in this list. Flag retained at the changeset level for follow-up beads filed later that DO touch model resolution. **resolved-no-action**
- [x] 1.4 File a follow-up bead to amend `openspec/specs/connector-base-spec/spec.md:424` ("resolves models … at the `discretion` complexity tier") to read `specialty` once `redesign-settings-dispatch-console` Phase 1b merges. Bead is non-blocking for this change.

## 2. Wave 1 — no spec dependency (parallel)

- [ ] 2.0 Bootstrap feature flag: add `INGESTION_DISPATCH_CONSOLE` env var read in the dashboard bootstrap module; default `true` in dev, `false` in prod for v1 staged rollout. Single Wave-1 prerequisite consumed by §2.1.
- [ ] 2.1 Sub-route scaffolding: add `/ingestion`, `/ingestion/connectors`, `/ingestion/filters`, `/ingestion/history` routes in `frontend/src/router.tsx` (gated on `INGESTION_DISPATCH_CONSOLE`); add 301 redirects from `?tab=connectors|filters|history`; preserve query-string filters (period, channel, status)
- [ ] 2.2 Cursor pagination — BREAKING: extend `src/butlers/core/ingestion_events.py` with keyset query (`received_at DESC, id DESC`); extend `src/butlers/api/routers/ingestion_events.py` `GET /api/ingestion/events` to accept `cursor` param and return `next_cursor` + `has_more`; remove `total` field
- [ ] 2.3 Update `frontend/src/hooks/use-ingestion-events.ts` to consume cursor response; remove `total`; add `fetchNextPage` semantics for infinite scroll
- [ ] 2.4 Refactor `frontend/src/components/ingestion/TimelineTab.tsx` to render infinite-scroll list (drop page indicator); add hour-group headers; render flame strip with tooltip "Approximation: bars are proportional to step duration, not actual token cost."
- [ ] 2.5 Drawer additions: anchor-scroll for session-index, click-to-copy session ID button
- [ ] 2.6 Resolve `sender_identity` to contact name in event drawer via `resolve_contact_by_channel()`; show "unresolved" indicator on miss
- [ ] 2.7 Replay history endpoint: `GET /api/ingestion/events/:id/replays` — query `public.audit_log` for `action='ingestion.event.replay'` and `target=<event_id>`; return chronological list
- [ ] 2.8 Saved Views: client-side `localStorage` key `ingestion-saved-views`; UI selector in Timeline header; built-in views: All / Errors / Priority / Spend (priority filters by `priority_contacts` once Wave 2 wires it — until then "Priority" is a no-op placeholder)
- [ ] 2.9 Connector Attention Strip: implement in TimelineTab header; filter `connectors` where `auth.status != 'ok'`; depends on bu-ju4kh primitive OR extracted local component (decide at bead time)
- [ ] 2.10 Wave-1 tests: unit tests for cursor handler, replay-history handler, contact-resolution rendering; Playwright smoke test for sub-route 301 redirects (depends on bu-vavsq Playwright bootstrap merged — done)

## 3. Wave 2 — spec-delta-gated (parallel within wave)

- [ ] 3.1 Alembic migration: create `priority_contacts(contact_id UUID FK → public.contacts.id ON DELETE CASCADE, butler TEXT NOT NULL, added_at TIMESTAMPTZ NOT NULL DEFAULT now(), added_by TEXT, PRIMARY KEY(contact_id, butler))` in `src/butlers/migrations/versions/`; same migration creates an AFTER DELETE trigger on `priority_contacts` that emits `audit.append()` per cascaded row with `action='ingestion.priority_contact.cascade_remove'` and `actor='system:contact_cascade'` (per `ingestion-priority-contacts` spec "Cascade-delete emits audit entry")
- [ ] 3.2 CRUD API: `GET/POST/DELETE /api/ingestion/priority-contacts` in `src/butlers/api/routers/ingestion_events.py`; emit `audit.append()` on every mutation; reject any roles-related writes (route to PATCH /api/contacts instead)
- [ ] 3.3 GmailPolicyEvaluator wiring: `src/butlers/modules/connectors/gmail.py` queries `priority_contacts` via DB lookup at 15-min TTL; flat-file `GMAIL_KNOWN_CONTACTS_PATH` retained as one-cycle fallback (DB primary; flat-file consulted only on DB query failure). Cleanup of the env var and flat-file code path is deferred to §4.7 per spec "GMAIL_KNOWN_CONTACTS_PATH deprecation" (two-bead split — file the §4.7 cleanup bead concurrently with this one)
- [ ] 3.4 `/ingestion/connectors` list page: extract `ConnectorsListPage.tsx` from existing `ConnectorsTab.tsx`; mount at the new route; gated on §1.2 merging
- [ ] 3.5 Connector available discovery: `GET /api/ingestion/connectors/available` returns enumerable connector profiles; surface in roster as "dormant/available" section
- [ ] 3.6a Connector lifecycle pause (audit-only): `POST /api/ingestion/connectors/:type/:identity/pause` — handler sets connector to `paused` state, emits `audit.append()` with `action='connector.pause'`; no Approvals gate
- [ ] 3.6b Connector lifecycle run-now (audit-only, paused-state-guarded): `POST /api/ingestion/connectors/:type/:identity/run-now` — handler validates connector is currently `paused` (HTTP 409 otherwise), clears pause, triggers next poll cycle, emits `audit.append()` with `action='connector.run_now'`; no Approvals gate
- [ ] 3.7 Alembic migration: add `connector_registry.deleted_at TIMESTAMPTZ NULL` (soft-delete); add `connector_registry.replay_safe BOOLEAN NOT NULL DEFAULT TRUE`
- [ ] 3.8 Alembic migration: create `channel_defaults(channel TEXT PRIMARY KEY, default_policy_json JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT NOT NULL)` per `ingestion-ui-information-architecture` spec "Channel defaults data model and REST API"
- [ ] 3.9 CRUD API: `GET /api/ingestion/channel-defaults/{channel}` (404 on missing); `PATCH /api/ingestion/channel-defaults/{channel}` (upsert with per-channel schema validation, HTTP 400 on validation failure); emit `audit.append()` on PATCH; no DELETE surface (HTTP 405)
- [ ] 3.10 Bulk rule ops: `POST /api/switchboard/ingestion-rules/bulk` accepting `action ∈ {enable, disable, delete}` + array of rule ids (max 100); enforce connector-scope → block-only at handler (HTTP 400 on violation). Path aligned with existing CRUD at `/api/switchboard/ingestion-rules`
- [ ] 3.11 `/ingestion/filters` and `/ingestion/history` route wrappers: mount existing `FiltersTab` and `BackfillHistoryTab` inside thin route wrappers; no behavior change
- [ ] 3.12 Wave-2 tests: API tests for priority_contacts (incl. cascade-delete audit emission), channel_defaults (incl. PATCH schema validation + DELETE 405), bulk-rules at `/api/switchboard/ingestion-rules/bulk`, lifecycle pause/run-now, available-discovery; component tests for ConnectorsListPage including the "dormant/available" section render from §3.5

## 4. Wave 3 — serial, gate-dependent

- [ ] 4.1 Bulk replay handler: `POST /api/ingestion/events/replay/bulk` accepting array of event ids (max-batch-size 50); use `SELECT ... FOR UPDATE SKIP LOCKED` on the lock acquisition; block `source_channel='email'` at handler (HTTP 409); requires `connector-replay-idempotency-policy` spec ratified
- [ ] 4.2 PipelineStats endpoint: `GET /api/ingestion/pipeline?window=24h` via Prometheus PromQL through existing `prometheus.py`; 60s TTL cache; degraded mode returns zeros with `aggregates_available: false` (never 500); requires `connector-state-aggregates` spec ratified
- [ ] 4.3 Thread `aggregates_available` flag through connector aggregate surfaces: backend adds the flag to `GET /api/ingestion/connectors/summaries` + `GET /api/ingestion/connectors/cross-summary` + `GET /api/ingestion/pipeline?window=24h` responses; frontend refactors `useConnectorSummaries`, `useCrossConnectorSummary`, and the pipeline hook to consume it; UI shows "metrics unavailable" eyebrow on `false` instead of blank cards
- [ ] 4.4 Connector lifecycle disconnect: `POST /api/ingestion/connectors/:type/:identity/disconnect`; gate through Approvals module at MCP server level; soft-delete sets `connector_registry.deleted_at`
- [ ] 4.5 Connector lifecycle rotate-token: `POST /api/ingestion/connectors/:type/:identity/rotate-token`; Approvals-gated; response body returns `{success: true, rotated_at: <iso8601>}` only — credential MUST NOT appear in response; `is_sensitive=True` masking applied throughout
- [ ] 4.6 Connector lifecycle reauth: `POST /api/ingestion/connectors/:type/:identity/reauth`; Approvals-gated; BLOCK at handler with HTTP 503 until `connector-oauth-scope-surface` spec exists. Response body identifies the blocking spec dependency; NO `Retry-After` header (no time-based recovery is meaningful)
- [ ] 4.7 Gmail flat-file cleanup (paired with §3.3 per spec "GMAIL_KNOWN_CONTACTS_PATH deprecation"): remove `GMAIL_KNOWN_CONTACTS_PATH` env var read from `gmail.py`; remove fallback branch in `GmailPolicyEvaluator`; gated on `priority_contacts` live + one deploy cycle measured stable. Bead is filed concurrently with §3.3 (DB-wiring) and carries a `discovered-from` dependency on that bead
- [ ] 4.8 Wave-3 tests: bulk replay concurrency test (race against `filtered_event_buffer.py:drain`), email-channel block (HTTP 409), pipeline degraded mode (Prometheus unreachable → 200 + `aggregates_available: false`), rotate-token credential masking, reauth HTTP 503 + no Retry-After header

## 5. Documentation + cleanup

- [ ] 5.1 Update `roster/*/AGENTS.md` notes-to-self where ingestion surface affects butler behavior (Gmail evaluator change in particular)
- [ ] 5.2 Update `CLAUDE.md` if any new conventions emerge (cursor pagination shape; degraded-mode response envelope)
- [ ] 5.3 Archive superseded design assets in `pr/overview/ingestion-redesign/` with a `MERGED.md` note pointing at the merged change
- [ ] 5.4 Run `openspec validate redesign-ingestion-dispatch-console`; fix any structural drift
- [ ] 5.5 Run `openspec archive redesign-ingestion-dispatch-console` after all waves merge (moves the change directory to `openspec/changes/archive/`)

## 6. Acceptance criteria (must pass before archive)

- [ ] 6.1 All BREAKING items have a corresponding upgrade-path note (cursor hook shape; sub-route 301 redirects preserve filter state)
- [ ] 6.2 All 15 mandates from synthesis §5 are realized (audit-on-mutation, no-credentials-in-response, per-action gate matrix, email replay block, FOR UPDATE SKIP LOCKED, connector-scope block-only enforcement, contact resolution, no useConnectorDetail on roster, bu-ty7gh closed, cursor pagination, TTL cache for pipeline, retention declared per table, FK join schema for priority_contacts, breadcrumb amendment, flame strip approximation labeled)
- [ ] 6.3 Cross-change coupling with `redesign-settings-dispatch-console` (discretion→specialty rename) is either resolved or each affected sub-bead carries the explicit deferral
- [ ] 6.4 Test coverage: every new endpoint group has ≥1 API test; every new sub-route page has ≥1 component test; Wave 1 sub-route navigation has Playwright smoke coverage
- [ ] 6.5 Doctrine sweep: no hard constraint violation in research-02 §1; token economics rule preserved (no per-event LLM cost added by this change)
- [ ] 6.6 Production smoke: dashboard `/ingestion` loads under the feature flag without error; degraded mode (Prometheus stopped) returns `aggregates_available: false` rather than 500
