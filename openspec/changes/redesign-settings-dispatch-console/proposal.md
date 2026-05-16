## Why

The current `/settings` page is a single-scroll stack of card components (CLI auth, model catalog, general settings, blob storage, QA settings, appearance, refresh defaults, command palette) that predates the **Dispatch** design language already shipped on `/overview`, `/butlers`, and `/qa`. It conflates *system-side configuration* (catalog, permissions, audit, webhooks, spend) with *per-user* and *per-butler* concerns that belong on other surfaces. The page reads as a SaaS preferences screen rather than the operator control plane it is meant to be.

The redesign assets in `pr/overview/settings-refactor/` propose a **Console + three sub-routes** information architecture, a complete server-side surface contract, and a Dispatch-language treatment that finally answers "what can I configure about this system?" without scrolling through unrelated provider setup cards. The user has selected direction B (the Console) after reviewing three top-level proposals (Ledger, Console, Manifest). This change implements that direction end-to-end, plus the adjacent route fold-ins for `/approvals`, `/butlers/{name}`, and `/memory` that the redesign also specifies.

## What Changes

- **BREAKING (UI)**: hard cut of the existing monolithic `/settings` page. Replaced by a Console grid at `/settings` plus three sub-routes:
  - `/settings/models` — model catalog grouped by complexity tier, server-sorted `(tier, priority DESC, enabled DESC)`, per-row priority stepper, enable toggle, test, edit, delete; filter chips.
  - `/settings/spend` — hand-rolled SVG forecast chart (dashed from "today"), per-butler / per-model / per-feature breakdown bars, routing rules table (store-and-eval, top-to-bottom order, per-rule 7-day savings), monthly ceiling, anomaly alerts, live spend stream.
  - `/settings/permissions` — full Permissions × Butlers matrix, last-15 audit reel + link to `/audit`, data ops sub-grid (encrypted-zip export, wipe phrase enforcement), webhooks registry with test action.
- **BREAKING (UI)**: hard cut of the existing `/approvals` page. Replaced by `ApprovalsPage` per `settings-expanded.jsx :: ApprovalsPage`: serif `why` paragraph, mono `evidence` lines, primary `Approve` commit button, secondary `Deny` / `Defer`, quiet-hours editor, history.
- **API rename**: `/api/actions/*`, `/api/suggestions/*`, `/api/rules/*` → `/api/approvals/*` (existing spec already uses `/api/approvals/...`, code drifted; this lands the rename and updates all hooks + tests). The existing `/api/approvals/actions` path style stays; PLAN.md's `/api/approvals` flat list is implemented as `/api/approvals/actions` to preserve the existing capability structure.
- **API additions** (dashboard-settings-console NEW capability):
  - `GET /api/settings/console` — header counts + `attention[]` strip items (10s cache).
  - `WS  /api/settings/stream` — live approval count, model verification ticks, spend ticks.
- **API additions** (dashboard-model-settings MODIFIED — note: all model endpoints live under `/api/settings/models/*`, not `/api/models/*` per PLAN.md, to match the existing dashboard namespace):
  - `PUT /api/settings/models/{id}/priority` `{ delta: int }` — idempotent priority adjustment.
  - `POST /api/settings/models/verify-all` — re-verify every key, return per-model latency.
  - `GET /api/settings/models/{id}/failures?since=24h` — failure tail for detail panel.
  - Server-side sort contract: `(complexity_tier, priority DESC, enabled DESC, alias ASC)`.
  - Routing contract: highest-priority enabled model with `state ∈ {verified, untested}` wins; fall through to next tier.
  - **PLAN.md's `PUT /api/models/{id}/role` is dropped** — `role` is a free-text label in the existing schema (`extra_args` or `description`); no dedicated endpoint is needed. The existing `PUT /api/settings/models/{id}` already accepts `extra_args` updates.
- **API additions** (dashboard-spend-dashboard NEW capability — REPLACES the existing `costs.py` router):
  - The existing `src/butlers/api/routers/costs.py` is **renamed** to `spend.py`. Old `/api/costs/*` endpoints (`/api/costs/summary`, `/api/costs/daily`, `/api/costs/top-sessions`, `/api/costs/by-schedule`) are renamed to `/api/spend/*` (`/api/spend?period=...`, `/api/spend/daily`, `/api/spend/top-sessions`, `/api/spend/by-schedule`). Frontend consumers (`use-costs.ts` or equivalent hooks) update accordingly.
  - `GET /api/spend?period=24h|7d|30d|90d|ytd|all`
  - `GET /api/spend/breakdown?by=butler|model|feature`
  - `GET /api/spend/forecast` — projected month-end land + daily series (naive `mtd ÷ days_elapsed × days_in_month` for v1).
  - `GET/POST/PUT/DELETE /api/spend/rules` — routing rules.
  - `PUT /api/spend/ceiling`
  - `WS /api/spend/stream` — per-call spend events.
- **API additions** (dashboard-permissions NEW capability):
  - `GET /api/permissions` — full matrix.
  - `PUT /api/permissions/{butler}/{perm}` `{ granted: bool, reason: str }` — **reason is required**.
  - `GET /api/audit-log?since=&actor=&action=&limit=` (existing prefix preserved; PLAN.md's `/api/audit` is implemented as `/api/audit-log` to match the shipped router).
  - `GET /api/audit-log/{id}`
  - `POST /api/data/export` `{ scope: 'full'|'memory'|'audit'|'config' }` — signed URL.
  - `DELETE /api/data/wipe` `{ phrase: str }` — requires literal `WIPE EVERYTHING IRREVERSIBLY`.
  - `GET/POST/PUT/DELETE /api/webhooks`, `POST /api/webhooks/{id}/test`.
- **API additions** (dashboard-approvals MODIFIED):
  - `GET /api/approvals?state=waiting|decided|all` (flat list, complements `/actions`).
  - `GET /api/approvals/{id}` returns `title`, `butler`, `ts`, `expires`, `why` (serif paragraph), `evidence` (mono lines), `proposed_action`.
  - `POST /api/approvals/{id}/approve` `{ edits?: object }`, `POST .../deny` `{ reason?: str }`, `POST .../defer` `{ hours: int }`.
  - `GET /api/approvals/history?since=`, `GET/PUT /api/approvals/policy` (quiet hours), `WS /api/approvals/stream`.
- **Doctrine updates**:
  - **design-language.md (Butler hue scope §)**: amend the `### Butler hue scope` section (`about/heart-and-soul/design-language.md` around line 837) and/or the `### Attention list` section (around line 820) to formally permit the 4–7% alpha attention-tint pattern (paired with a 2px left rail in the same state color) as the single state-color-on-background exception. Without this amendment, the existing non-negotiable rule "hue only on letter-mark; never on backgrounds, borders, buttons, headers" rejects the attention tint as a violation.
  - **v1.md (What v1 Ships §)**: amend to explicitly list the three settings sub-routes (`/settings/models`, `/settings/spend`, `/settings/permissions`), the `dashboard-audit-log` infrastructure primitive, and the webhooks/data-ops surface as v1-shipped capabilities. Without this, the spec/code drifts from v1.md's stated scope.
- **Database migrations**:
  - **Audit log primitive** — `public.audit_log` (`ts, actor, action, target, note, ip, request_id`). No deletes ever. Every write endpoint in this refactor calls `audit.append()`.
  - **Approvals data model** — extend `pending_actions` with `why TEXT` (serif paragraph) and `evidence JSONB` (array of strings).
  - **Permissions** — `public.permissions` (`butler TEXT, permission TEXT, granted BOOL, reason TEXT, updated_at, updated_by`); composite PK.
  - **Webhooks** — `public.webhooks` (`id, endpoint, events JSONB, enabled, secret_hash, last_test_at, last_test_ok, retry_policy JSONB`).
  - **Spend routing rules** — `public.spend_rules` (`id, position INT, condition JSONB, action JSONB, saved_7d NUMERIC, created_at, updated_at`); position is significant (top-to-bottom evaluation order).
- **Routing change**: model selection updated to the new contract above. Old "default = first verified" logic replaced.
- **complexity_tier rename + remap**: the existing `model_catalog.complexity_tier` CHECK constraint values (`trivial | medium | high | extra_high | discretion | self_healing`) are replaced with PLAN.md's canonical six (`reasoning | workhorse | cheap | specialty | local | legacy`). A one-time data migration remaps existing rows by intent: `extra_high → reasoning`, `high → reasoning`, `medium → workhorse`, `trivial → cheap`, `discretion → specialty`, `self_healing → specialty`. Existing runtime callers passing the old values are updated in the same change. The CHECK constraint is rewritten to the new six values.
- **Adjacent fold-ins** (PLAN.md §6 phases 7–8):
  - `/butlers/{name}` — fold in `ButlersExpanded` design: fallback chain, system prompt (with version history `GET .../prompt/history`, each `PUT` snapshots prior), tools matrix, memory access, activity stripe-chart, kill switch.
  - `/memory` — fold in `MemoryExpanded` design: tier flow, retention policy table (keyed by `kind` ∈ `event|fact|preference|summary|transcript|embedding`), compaction log, memory-inspect search.
- **Per-user OAuth (`/secrets`) is explicitly out of scope.** It stays on `/secrets`. Provider cards (Google, Spotify, Telegram, Steam, HomeAssistant, OwnTracks, WhatsApp) currently rendered on `/settings` move to `/secrets` and are not part of the Console.
- **Frontend tokens**: no new tokens. Dispatch already ships in `frontend/src/index.css`. The redesign only consumes existing tokens plus the new attention-tint pattern.

## Capabilities

### New Dashboard Capabilities

- **dashboard-settings-console** — the `/settings` Console grid, attention strip, breadcrumb-less editorial shell, the `GET /api/settings/console` aggregator, and the `WS /api/settings/stream` ticker.
- **dashboard-spend-dashboard** — the `/settings/spend` page, the spend endpoints (`/api/spend/*`), the hand-rolled SVG forecast chart, the routing-rules store-and-eval engine, and the `WS /api/spend/stream` ticker.
- **dashboard-permissions** — the `/settings/permissions` page, the Permissions × Butlers matrix, the audit reel (`GET /api/audit-log`), the data-ops sub-grid (`POST /api/data/export`, `DELETE /api/data/wipe`), and the webhook registry (`GET/POST/PUT/DELETE /api/webhooks`).

### New Infrastructure Primitive

- **dashboard-audit-log** — the audit primitive itself (table + `audit.append()` helper + indefinite retention). NOT a dashboard capability per se; this is cross-cutting infrastructure shared by every mutation endpoint in this refactor and all future write-bearing endpoints. It owns the `/api/audit-log` read API (already mounted) and the `public.audit_log` table.

### Modified Capabilities

- **dashboard-shell** — route registration for `/settings`, `/settings/models`, `/settings/spend`, `/settings/permissions`; replace `/approvals` route; sidebar nav-config updates; remove provider-setup cards from `/settings` (move to `/secrets`).
- **dashboard-model-settings** — Dispatch-language UI rewrite; tier-based grouping using existing `complexity_tier` column; server-side sort `(tier, priority DESC, enabled DESC, alias ASC)`; new `priority` stepper, `verify-all`, per-model `failures` tail; new routing-tier resolution contract.
- **dashboard-approvals** — `/api/approvals` flat list + per-id detail with `why`/`evidence`/`proposed_action`; quiet-hours policy GET/PUT; `WS /api/approvals/stream`; replace existing `/approvals` page UI with `ApprovalsPage` per `settings-expanded.jsx`.
- **module-approvals** — `pending_actions.why TEXT`, `pending_actions.evidence JSONB`; the agent contract that fills them in at action creation time; defer policy (`POST /api/approvals/{id}/defer` translates to scheduling the action's re-presentation).
- **dashboard-butler-management** — fold the `ButlersExpanded` design (fallback chain, system prompt + version history, tools + scope, memory access, activity stripe-chart, kill switch with 30s grace) into the existing `/butlers/{name}` detail surface. System prompt becomes a real CRUD surface: each `PUT` snapshots the previous prompt and `GET .../prompt/history` returns the chain.
- **module-memory** — fold the `MemoryExpanded` design into the existing `/memory` page: tier flow viz, retention policy table keyed by `kind`, compaction log feed, memory-inspect search bar. Retention policies become a small admin table; the cleanup job consults it.

## Impact

**Doctrine**
- `about/heart-and-soul/design-language.md` — append the 4–7% attention-tint pattern + 2px state-color left rail to §1b. This is the only state-color-on-background exception in the system.

**Database (Alembic migrations)**
- New: `public.audit_log`, `public.permissions`, `public.webhooks`, `public.spend_rules`.
- Modified: `pending_actions` — add `why TEXT`, `evidence JSONB`.
- No schema change to `model_catalog` (reuse existing `complexity_tier`, `priority`, `enabled`).
- New: `public.system_prompt_history` keyed by `butler_name` for the system-prompt versioning surface.
- New: `public.memory_retention_policies` keyed by `kind` for the memory retention table.

**Backend (Python)**
- `src/butlers/api/routers/audit.py` — extend with `audit.append()` helper called by every mutation endpoint in scope; expose `GET /api/audit`, `GET /api/audit/{id}`.
- `src/butlers/api/routers/settings_console.py` (NEW) — `GET /api/settings/console`, `WS /api/settings/stream`.
- `src/butlers/api/routers/spend.py` (NEW) — `GET /api/spend*`, rules CRUD, ceiling, `WS /api/spend/stream`.
- `src/butlers/api/routers/permissions.py` (NEW) — matrix CRUD, mandatory `reason`.
- `src/butlers/api/routers/data_ops.py` (NEW) — `POST /api/data/export` (signed URL), `DELETE /api/data/wipe` (phrase enforcement).
- `src/butlers/api/routers/webhooks.py` (NEW) — registry CRUD + test dispatcher with retry policy.
- `src/butlers/api/routers/approvals.py` (MODIFIED) — `/api/approvals` flat list + `/api/approvals/{id}` detail with `why`/`evidence`; quiet-hours `GET/PUT`; `WS /api/approvals/stream`.
- `src/butlers/api/routers/model_settings.py` (MODIFIED) — priority stepper, verify-all, failures tail, new sort contract.
- `src/butlers/core/runtime/router.py` (or wherever model selection lives) — implement the new `(tier, priority DESC, enabled DESC, state ∈ {verified, untested})` selection contract.
- `roster/<butler>/api/router.py` for butler-detail — extend with prompt history, tool scopes, memory access, kill-switch endpoints.

**Frontend (Vite + React 18 + React Router v7)**
- `frontend/src/router.tsx` — add `/settings`, `/settings/models`, `/settings/spend`, `/settings/permissions`; replace `/approvals` route; remove provider cards from `/settings`.
- `frontend/src/pages/SettingsConsolePage.tsx` (NEW) — Console grid + AttentionStrip.
- `frontend/src/pages/SettingsModelsPage.tsx` (NEW) — tier-grouped catalog.
- `frontend/src/pages/SettingsSpendPage.tsx` (NEW) — SVG forecast + breakdowns + rules.
- `frontend/src/pages/SettingsPermissionsPage.tsx` (NEW) — matrix + audit reel + data ops + webhooks.
- `frontend/src/pages/ApprovalsPage.tsx` (REWRITE) — replace existing with Dispatch-language version per `settings-expanded.jsx`.
- `frontend/src/pages/ButlerDetailPage.tsx` (existing — MODIFIED) — fold in fallback chain, prompt history surface, tools matrix, memory-access tiles, activity stripe-chart, kill switch.
- `frontend/src/pages/MemoryPage.tsx` (existing — MODIFIED) — fold in tier flow, retention table, compaction log, inspect search.
- `frontend/src/pages/SettingsPage.tsx` (existing) — DELETED in same PR as new SettingsConsolePage lands.
- `frontend/src/components/settings/` — delete old `BlobStorageCard`, `GeneralSettingsCard`, `QASettingsCard`, `ModelCatalogCard` (replaced); keep provider-setup cards but move their host page to `/secrets`.
- Tests: e2e Playwright for `/settings/*`, `/approvals`; unit tests for every new endpoint with `audit.append()` assertion.

**Out of scope (explicitly)**
- Per-user OAuth setup remains on `/secrets`.
- No "density" toggle. No new theming knobs. Dispatch is dark-canonical with paper-warm light variant; no other themes. No theme-toggle UI in any new page.
- No graphical "wiring diagram" of butlers ↔ models ↔ permissions. The matrix is enough.
- No onboarding tooltips on settings.
- No new charting library. Spend chart is hand-rolled SVG.
- No `PUT /api/models/{id}/role` endpoint (PLAN.md proposed this; we drop it — role is editable via the existing model-catalog PUT).
- No new design tokens. Dispatch already ships in `frontend/src/index.css`; the new attention-tint class consumes existing OKLCH state-color variables.
