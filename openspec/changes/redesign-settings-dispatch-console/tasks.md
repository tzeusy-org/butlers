## 1. Doctrine updates (Phase 0)

- [ ] 1.1 Edit `about/heart-and-soul/design-language.md` under `### Butler hue scope` (around line 837) and `### Attention list` (around line 820): add the 4–7% alpha attention-tint + 2px left rail pattern as the single state-color-on-background exception. Cite the use cases (open approval, auth-renewal, model in error). Without this amendment the existing rule rejects the pattern as a violation.
- [ ] 1.2 Add the matching `.attention-row[data-tone=...]` block to `frontend/src/index.css` under the existing OKLCH palette.
- [ ] 1.3 Edit `about/heart-and-soul/v1.md` "What v1 Ships → Dashboard" to add three sub-routes (`/settings/models`, `/settings/spend`, `/settings/permissions`), the `dashboard-audit-log` primitive, and webhooks/data-ops as v1-shipped capabilities. Cite this OpenSpec change as the implementing change.
- [ ] 1.4 Land 1.1–1.3 in the same PR as this OpenSpec change. No code consumes the class yet; the doctrine updates are prerequisites for everything below.

## 2. Foundations — audit log primitive (Phase 1)

- [ ] 2.1 Alembic migration: create `public.audit_log (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now() NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT, note TEXT, ip INET, request_id UUID)`. Indexes: `(ts DESC)`, `(action)`, `(actor)`. (Table does NOT exist today — verified by R2.)
- [ ] 2.2 Implement `audit.append(actor, action, target, note=None, ip=None, request_id=None) -> int` in `src/butlers/api/routers/audit.py` (extend, don't replace). Returns the new row id. **Contract per design.md §D17**: MUST raise `AuditTableNotAvailableError` (or `sqlalchemy.exc.ProgrammingError`) if the table is missing — no silent fallthrough. Helpers MUST be called INSIDE the same SQL transaction as the state change so the audit row commits atomically with the mutation.
- [ ] 2.2.1 Create `tests/api/conftest.py` fixture `audit_append_spy()` that wraps the per-module audit import and asserts the call. All Phase 2–8 endpoint tests use this fixture to verify `audit.append(action=...)` was invoked. (R9 recommendation.)
- [ ] 2.2.2 Negative test: drop `public.audit_log` in a test transaction, call `audit.append()`, assert `AuditTableNotAvailableError` is raised; assert the calling endpoint responds `503` not `500`.
- [ ] 2.3 Implement `GET /api/audit-log?since=&actor=&action=&limit=` returning `PaginatedResponse[AuditEntry]` (default `limit=100`, max `1000`). Server-side sort `ts DESC`. (Use the existing `/api/audit-log` prefix; do NOT rename to `/api/audit`.)
- [ ] 2.4 Implement `GET /api/audit-log/{id}` returning `ApiResponse[AuditEntry]`.
- [ ] 2.5 Prometheus counter `audit_log_appended_total{action}` incremented per write.
- [ ] 2.6 Migration regression test: insert, list, get-by-id; assert `(ts DESC)` ordering.
- [ ] 2.7 Confirm no delete endpoint exists for `audit_log` (no `DELETE` route, no `revoke`/`forget` helper, no SQLAlchemy delete in routers/services). Add a static-check unit test that fails if `DELETE FROM audit_log` appears in repo code.

## 3. Foundations — model catalog routing contract (Phase 1)

- [ ] 3.0 Inventory every runtime caller passing the old `complexity_tier` values (`trivial|medium|high|extra_high|discretion|self_healing`). Grep: `src/butlers/core/`, `src/butlers/modules/`, `roster/*/`, scheduler, complexity-classification logic. Produce a list of call sites that need the new vocabulary.
- [ ] 3.1 Alembic migration **Phase 1a per design.md §D18** (two-phase, NOT one-shot): rewrite `model_catalog.complexity_tier` CHECK constraint to accept BOTH OLD AND NEW values (`reasoning|workhorse|cheap|specialty|local|legacy|extra_high|high|medium|trivial|discretion|self_healing`). Remap existing rows using the table in design.md §D4 (`extra_high→reasoning`, `high→reasoning`, `medium→workhorse`, `trivial→cheap`, `discretion→specialty`, `self_healing→specialty`). Also add `last_verified_at TIMESTAMPTZ`, `last_verified_latency_ms INT`, `last_verified_ok BOOL` columns (none exist today — verified by R2). Include rollback path.
- [ ] 3.1.1 Update every call site from §3.0 to emit ONLY new values. Add a deprecation log in the runtime selector that fail-louds on any caller still passing old values (do NOT silently accept). Ship caller updates in the same PR as 3.1.
- [ ] 3.1.2 **Phase 1b (separate change, ≥7 days after Phase 1a soaks)**: drop the old values from the CHECK constraint; the runtime selector accepts only new values; remove deprecation logging. This eliminates the inconsistency window R10 flagged. File as a follow-up bead linked to `bu-5tnp0`.
- [ ] 3.2 Update `src/butlers/core/runtime/router.py` (or wherever model selection lives) to the new contract: highest-priority enabled model in tier whose `state ∈ {verified, untested}`; fall through to next tier in canonical order. Replace the existing "default = first verified" path.
- [ ] 3.3 Update `GET /api/settings/models` to sort server-side `(complexity_tier, priority DESC, enabled DESC, alias ASC)`. Frontend never sorts.
- [ ] 3.4 Add `PUT /api/settings/models/{id}/priority {delta: int}` — idempotent: stores `max(0, current + delta)`. Calls `audit.append()` with `action="model.priority"`, `target=model_id`, `note=str(delta)`.
- [ ] 3.5 Add `POST /api/settings/models/verify-all` — issues 1-token completions in parallel (bounded concurrency via `asyncio.Semaphore(8)`); writes `last_verified_*` per row; rate-limited to once per minute system-wide via an in-memory `time.monotonic()`-bucketed dict in the router module (acceptable for the single-worker dashboard daemon per design.md §D15; switch to a DB-backed sequence if multi-worker is ever introduced). Probe prompt is a fixed 1-token string `"hi"`. Calls `audit.append()` once per accepted run with `action="models.verify_all"`. Per-model failures set `last_verified_ok=false`; whole-call returns 200 with per-model results.
- [ ] 3.6 Add `GET /api/settings/models/{id}/failures?since=24h` — failure tail for the detail panel, drawn from `dispatch_failures` or equivalent table.
- [ ] 3.7 Tests: tier fallthrough order; priority stepper rounds at 0; verify-all parallel bound; verify-all rate limit; failures tail filters by `since`.

## 4. /settings/models — Dispatch UI (Phase 2)

- [ ] 4.1 Create `frontend/src/pages/SettingsModelsPage.tsx`. Implements `settings-redesign.jsx :: ModelCatalogExpanded`: tier-grouped sections in canonical order, per-row priority stepper (↑/↓), enable toggle, test, edit, delete, filter chips for provider and state.
- [ ] 4.2 Add hooks: `useUpdateModelPriority`, `useVerifyAllModels`, `useModelFailures(id)`. Reuse existing `useModelCatalog`, `useUpdateModelCatalogEntry`, `useDeleteModelCatalogEntry`, `useTestModelCatalogEntry`.
- [ ] 4.3 The priority stepper round-trips and re-fetches the list (no optimistic reorder — server sort is the source of truth). Assert visible round-trip ≤ 200ms in dev.
- [ ] 4.4 Empty state per tier: serif italic, single sentence, no illustration ("Nothing in this tier.").
- [ ] 4.5 Dev-mode `ApiWireFooter` analog showing `GET /api/settings/models`, `PUT /api/settings/models/{id}/priority`, `POST /api/settings/models/verify-all`. Off in prod.
- [ ] 4.6 Replace `frontend/src/components/settings/ModelCatalogCard.tsx` consumption — `SettingsPage` no longer imports it (page deleted in §11).
- [ ] 4.7 Playwright e2e: `/settings/models` happy path + one error-state model row.

## 5. /settings/spend — forecast + breakdown + rules (Phase 3)

- [ ] 5.0 **Rename `src/butlers/api/routers/costs.py` to `src/butlers/api/routers/spend.py`** with a **90-day deprecation period per design.md §D18**. Dual-mount: BOTH `/api/costs/*` (existing) AND `/api/spend/*` (new) point to the same handlers. `/api/costs/*` responses include `Deprecation: true` and `Sunset: <date 90 days out>` headers per RFC 8594. Migrate path mapping: `/api/costs/summary` ↔ `/api/spend/summary`, `/api/costs/daily` ↔ `/api/spend/daily`, `/api/costs/top-sessions` ↔ `/api/spend/top-sessions`, `/api/costs/by-schedule` ↔ `/api/spend/by-schedule`. Update frontend hook to consume `/api/spend/*` immediately. File a follow-up bead to delete `/api/costs/*` after 90 days.
- [ ] 5.1 Alembic migration: `public.spend_rules (id, position INT, condition JSONB, action JSONB, saved_7d NUMERIC DEFAULT 0, created_at, updated_at)`; unique on `position` partial-indexed `WHERE position IS NOT NULL`. Also `public.spend_ceiling (id INT DEFAULT 1 PRIMARY KEY, monthly_usd NUMERIC NOT NULL, updated_at)`. Neither table exists today (verified by R2).
- [ ] 5.2 Extend `src/butlers/api/routers/spend.py` (renamed from costs.py in §5.0) with the new endpoints:
  - `GET /api/spend?period=24h|7d|30d|90d|ytd|all` — totals from existing cost ledger.
  - `GET /api/spend/breakdown?by=butler|model|feature` — grouped totals.
  - `GET /api/spend/forecast` — `mtd ÷ days_elapsed × days_in_month` (clamp `days_elapsed ≥ 1`); returns `daily[]` series + `projected_eom`. TODO comment for smarter estimator.
  - `GET/POST /api/spend/rules`, `PUT/DELETE /api/spend/rules/{id}` — position is significant; mutations call `audit.append("spend.rule")`.
  - `PUT /api/spend/ceiling` — `audit.append("spend.ceiling")`.
- [ ] 5.3 Implement `WS /api/spend/stream` — emits one event per LLM call from the runtime cost reporter.
- [ ] 5.4 Daily job: compute `spend_rules.saved_7d` by comparing rule-chosen model cost vs. baseline (default tier model).
- [ ] 5.5 Create `frontend/src/pages/SettingsSpendPage.tsx` implementing `settings-expanded.jsx :: SpendDashboard`:
  - 4-cell KPI strip.
  - Hand-rolled SVG forecast chart (no chart library). Solid line for MTD, dashed line for projection, hairline at ceiling.
  - Breakdown bars (8 lines of CSS each, no library).
  - Routing rules table — drag-to-reorder (HTML5 drag, no library), show `saved_7d`.
  - Anomaly placeholder section with TODO copy.
- [ ] 5.6 Playwright e2e: `/settings/spend` happy path + chart renders + a ceiling-update flow.

## 6. /settings/permissions — matrix + audit reel + data ops + webhooks (Phase 4)

- [ ] 6.1 Alembic migration: `public.permissions (butler TEXT, permission TEXT, granted BOOL, reason TEXT, updated_at, updated_by TEXT, PRIMARY KEY (butler, permission))`.
- [ ] 6.2 Alembic migration: `public.webhooks (id UUID PK, endpoint TEXT, events JSONB, enabled BOOL, secret_hash TEXT, last_test_at TIMESTAMPTZ, last_test_ok BOOL, retry_policy JSONB, created_at, updated_at)`.
- [ ] 6.3 Alembic migration: `public.approvals_policy (id INT DEFAULT 1 PRIMARY KEY, quiet_start_hour INT, quiet_end_hour INT, timezone TEXT, updated_at)`.
- [ ] 6.4 Implement `src/butlers/api/routers/permissions.py`:
  - `GET /api/permissions` — full matrix as `{butlers[], permissions[], cells: {butler: {perm: {granted, reason, updated_at, inherited: bool}}}}`. Requires `X-API-Key` auth.
  - `PUT /api/permissions/{butler}/{perm} {granted: bool, reason: str}` — requires `X-API-Key` auth. Refuses with 422 `{error: "reason_required"}` if `reason` is empty/whitespace. Refuses with 422 `{error: "reason_contains_credential"}` if `reason` matches case-insensitive `(password|token|secret|api[_-]?key|credential|private[_-]?key)` via shared helper `validate_no_secrets()` in `src/butlers/api/security.py`. Calls `audit.append("permission.set", target=f"{butler}.{perm}", note=reason)` (inside the same transaction as the row update).
- [ ] 6.5 Implement `src/butlers/api/routers/data_ops.py`:
  - `POST /api/data/export {scope}` — generates encrypted zip, returns signed URL (TTL 60min). **Requires `X-API-Key` auth** (design.md §D16). Calls `audit.append("data.export", note=scope)`.
  - `DELETE /api/data/wipe {phrase}` — **MUST require `X-API-Key` auth first** (refuses with 401 if missing/wrong). If `DASHBOARD_API_KEY` env is unset, refuses with `503 {error: "auth_unconfigured"}`. Only after auth succeeds does the phrase check run; refuses if `phrase != "WIPE EVERYTHING IRREVERSIBLY"` (exact match, no trim, no case-fold). Drops every butler schema + audit_log + model_catalog + runtime_config + permissions + spend_ledger + webhooks inside a single SQL transaction (rolls back atomically on any drop failure; HTTP 500 `{error: "wipe_partial_failure", failed_at: "<step>"}` on failure per design.md §D17). Writes a pre-transaction non-transactional audit row first (so even a partial failure is logged), then transactional `audit.append("data.wipe")` as the last action before dropping audit_log itself.
- [ ] 6.6 Implement `src/butlers/api/routers/webhooks.py`:
  - CRUD endpoints.
  - `POST /api/webhooks/{id}/test` — synthesizes a `webhook.test` event, runs the dispatcher, returns receiver status code + latency.
  - Dispatcher signs payloads with HMAC-SHA256 using the per-row secret; retries per `retry_policy`.
- [ ] 6.7 Wipe-phrase tests: exact match passes; trailing-whitespace fails; lower-case fails; missing field 422.
- [ ] 6.8 Create `frontend/src/pages/SettingsPermissionsPage.tsx` implementing `settings-expanded.jsx :: DataExpanded`:
  - Permissions × Butlers matrix. Inherited cells render dim; explicit cells foreground.
  - On cell flip, modal prompts for `reason`; submit disabled until non-empty.
  - Audit reel (last 15 entries from `GET /api/audit-log?limit=15`).
  - Data ops sub-grid: export (scope picker → signed URL), wipe (phrase input — submit disabled until non-empty; server enforces).
  - Webhooks table: list, add (modal), edit, test, delete.
- [ ] 6.9 Playwright e2e: `/settings/permissions` matrix flip with reason; wipe phrase rejection; webhook test.

## 7. /settings — Console (Phase 5)

- [ ] 7.1 Implement `src/butlers/api/routers/settings_console.py`:
  - `GET /api/settings/console` — aggregates header counts (active butlers, total spend MTD, open approvals, models verified) + `attention[]` array (capped at 5 items + `attention_truncated_count` field per design.md §D15). Cache 10s in-memory keyed by actor (effectively global in single-owner deployments). Requires `X-API-Key` auth.
  - Partial-failure mode: when a sub-system aggregation fails (e.g., spend backend down), surface a single amber `attention` item naming the failed subsystem instead of erroring the whole response.
  - `WS /api/settings/stream` — multiplexes approval count, model verification results, spend ticks. **Auth at upgrade**: refuse the WS handshake unless `?api_key=<value>` matches `DASHBOARD_API_KEY` per design.md §D16. Apply the same WS-auth pattern to `WS /api/spend/stream` (§5.3) and `WS /api/approvals/stream` (§8.3).
- [ ] 7.2 Create `frontend/src/pages/SettingsConsolePage.tsx` implementing `settings-redesign.jsx :: SettingsConsole`:
  - Console grid of panels, one per sub-route. Each panel fetches its own summary endpoint (parallel queries).
  - AttentionStrip at top: items `{tone: red|amber, kind, text, action_route}` from `attention[]`. Pattern: attention-tint + 2px left rail.
  - Each panel is independent — a slow fetch in one does not block the page.
- [ ] 7.3 Sidebar nav-config: add `/settings`; the route absorbs the existing `/settings` entry. Sub-routes are reached via the Console panels, not the sidebar (no nested nav).
- [ ] 7.4 Playwright e2e: `/settings` Console renders + AttentionStrip displays an item from a seeded attention state.

## 8. /approvals — replacement (Phase 6)

- [ ] 8.1 Alembic migration: `ALTER TABLE pending_actions ADD COLUMN why TEXT`, `ADD COLUMN evidence JSONB DEFAULT '[]'::jsonb`. Backfill `null` for legacy rows (tolerated by UI).
- [ ] 8.2 Update the agent contract (module-approvals): when an approval is created, the LLM SHOULD emit `why` (a single serif paragraph) and `evidence` (an array of mono strings); both are stored alongside the action.
- [ ] 8.3 Update `src/butlers/api/routers/approvals.py`:
  - `GET /api/approvals?state=waiting|decided|all` — flat list (complements existing `/api/approvals/actions`).
  - `GET /api/approvals/{id}` — full detail including `why`, `evidence`, `proposed_action`, `title`, `expires`.
  - `POST /api/approvals/{id}/approve {edits?: object}` — applies optional edits then approves.
  - `POST /api/approvals/{id}/deny {reason?: str}`.
  - `POST /api/approvals/{id}/defer {hours: int}` — bounded `1 ≤ hours ≤ 168`. Re-presents the action at `now + hours`.
  - `GET /api/approvals/history?since=`.
  - `GET/PUT /api/approvals/policy` — quiet hours (singleton row).
  - `WS /api/approvals/stream` — live updates.
  - Every mutation calls `audit.append("approval.<verb>", target=action_id)`.
- [ ] 8.4 Rewrite `frontend/src/pages/ApprovalsPage.tsx` per `settings-expanded.jsx :: ApprovalsPage`:
  - Two-pane layout: rail of pending + right-pane dossier with serif `why`, mono `evidence`, primary `Approve` commit button, secondary `Deny` / `Defer`.
  - Quiet-hours editor under a `Policy` section.
  - History section under the active list.
- [x] 8.5 Delete the old `/api/actions/*`, `/api/suggestions/*`, `/api/rules/*` paths **only after** the frontend is migrated. Refactor `frontend/src/hooks/use-approvals.ts` (the single existing approvals hook — confirmed by R2 audit; the previously named `use-approval-actions.ts`, `use-autonomy-suggestions.ts`, `use-approval-rules.ts` do not exist) to consume the new `/api/approvals/*` paths. **Done (bu-b19qa):** Legacy router files (`actions.py`, `suggestions.py`, `rules.py`) never existed in this codebase — the migration was completed as part of bu-5xiu9 (PR #1699). `frontend/src/hooks/use-approvals.ts` already calls only `/api/approvals/*` via `@/api/index.ts`. `frontend/src/api/client.ts` uses `/approvals/...` paths throughout. No test files target legacy endpoints. Verified clean with grep across src/, tests/, frontend/. No code changes were required.
- [ ] 8.6 Notification dispatcher consults `approvals_policy.quiet_*` before paging the owner. **Location**: extend the existing approvals notify path (likely `src/butlers/modules/approvals/notifier.py`; confirm during implementation). This is a synchronous check, NOT a separate scheduled job. The approval re-presentation timer (defer mechanism) is owned by the existing approvals scheduler at `src/butlers/modules/approvals/scheduler.py` which consumes `pending_actions.expires_at` (per design.md §D15).
- [ ] 8.7 Playwright e2e: `/approvals` approve flow + deny flow + defer + quiet-hours edit.

## 9. /butlers/{name} — fold-in (Phase 7)

- [ ] 9.1 Alembic migration: `public.system_prompt_history (id BIGSERIAL, butler_name TEXT, prompt TEXT, version INT, updated_at, updated_by TEXT)`. Each `PUT` snapshots; `GET .../prompt/history` returns the chain ordered by `version DESC`.
- [ ] 9.2 Extend `roster/<butler>/api/router.py` (or central butler-detail router) with:
  - `GET/PUT /api/butlers/{name}/prompt` — current prompt.
  - `GET /api/butlers/{name}/prompt/history?limit=20`.
  - `GET /api/butlers/{name}/tools` — tool list with allowed/scope.
  - `PUT /api/butlers/{name}/tools/{tool}` `{allowed: bool, scope?: str}`.
  - `GET /api/butlers/{name}/memory-access` — tier read/write matrix.
  - `POST /api/butlers/{name}/kill {grace_seconds: int}` — initiates kill with 30s default grace.
- [ ] 9.3 Modify `frontend/src/pages/ButlerDetailPage.tsx` (existing) — fold in `ButlersExpanded` design sections:
  - Fallback chain (existing model + ordered fallbacks, with "add fallback" link).
  - System prompt section: serif body, mono caption with `tokens · NNN`, `last edit · <actor>`, `history · N versions →`, `diff vs vN-1 →`. Edit opens an inline editor; submit `PUT`s and snapshots.
  - Tools & integrations matrix with toggle + scope edit.
  - Memory access tiles (short / mid / long, read/write badges).
  - Activity stripe-chart (24h sessions).
  - Kill switch link — `kill switch · 30s grace →`.
- [ ] 9.4 Tests: prompt history snapshot ordering; kill-switch with grace; tool scope update audit log.

## 10. /memory — fold-in (Phase 8)

- [ ] 10.1 Alembic migration: `public.memory_retention_policies (kind TEXT PRIMARY KEY, ttl_days INT, max_rows BIGINT, updated_at, updated_by)`. Seed with current defaults per kind (`event|fact|preference|summary|transcript|embedding`).
- [ ] 10.2 Extend the memory API:
  - `GET/PUT /api/memory/retention-policies` — admin table.
  - `GET /api/memory/compaction-log?limit=50` — recent compaction events.
  - `GET /api/memory/inspect?q=&kind=&limit=` — search bar.
- [ ] 10.3 The cleanup job consults `memory_retention_policies` per kind; logs each compaction as an entry.
- [ ] 10.4 Modify `frontend/src/pages/MemoryPage.tsx` (existing) — fold in `MemoryExpanded` design:
  - Tier flow viz (events → mid-term → long-term).
  - Retention policy table (editable cells trigger `PUT`).
  - Compaction log feed.
  - Inspect search bar.
- [ ] 10.5 Tests: cleanup honors per-kind policy; inspect search hits and pagination.

## 11. Remove legacy surfaces

- [ ] 11.1 Delete `frontend/src/pages/SettingsPage.tsx` in the same PR as `SettingsConsolePage` lands.
- [ ] 11.2 Delete the legacy card components AND their test files in their actual locations (verified by R2 + R9):
  - `frontend/src/components/settings/BlobStorageCard.tsx`
  - `frontend/src/components/settings/QASettingsCard.tsx`
  - `frontend/src/components/settings/ModelCatalogCard.tsx`
  - `frontend/src/components/GeneralSettingsCard.tsx` (note: this file lives at `/components/`, NOT `/components/settings/`)
  - `frontend/src/components/GeneralSettingsCard.test.tsx` (test file exists — R9 confirmed; delete in same PR as the component to avoid CI breakage)
- [ ] 11.2.1 Run `grep -r "ComponentName" frontend/src/` for each deleted card to verify no orphan imports remain. Add a lint/static-check that fails CI if a deleted-component name reappears.
- [ ] 11.3 Move provider-setup cards (`GoogleOAuthSection`, `HomeAssistantSetupCard`, `OwnTracksSetupCard`, `SpotifySetupCard`, `SteamSetupCard`, `WhatsAppSetupCard`, `GoogleHealthStatusCard`) to be consumed by `frontend/src/pages/SecretsPage.tsx`. The component files stay; the host page changes.
- [ ] 11.4 No additional hook deletion required — `use-approvals.ts` was refactored in §8.5; the three hooks PLAN.md anticipated do not exist (verified by R2).
- [ ] 11.5 The old `/settings` URL hard-redirects to the new `/settings` Console (single 301 in `router.tsx`).
- [ ] 11.6 `src/butlers/api/routers/costs.py` was renamed in §5.0; ensure no stale imports of `costs` remain in `src/butlers/api/main.py`, router-discovery code, or other modules. Update FastAPI router registration accordingly.

## 12. Reconciliation report

- [ ] 12.1 Bootstrap `docs/reports/redesign-settings-dispatch-console.md` via `scripts/epic-report-scaffold.sh redesign-settings-dispatch-console`.
- [ ] 12.2 At epic end (all child beads landed AND doctrine PR `bu-9gzel` is merged to `main` — verify via `git log --oneline main | grep "design-language.md"` before writing), write the report covering: routes shipped, surfaces deleted, audit-log coverage matrix (every mutation endpoint × audit.append() call — should be 100%), open follow-ups (complexity_tier Phase 1b, /api/costs sunset, why/evidence NOT NULL, smarter forecast estimator), doctrine deltas merged with commit SHAs, and screenshots of the four Settings pages plus /approvals.
- [ ] 12.3 Link the report from the epic bead's `description` field and from `docs/reports/README.md`.
