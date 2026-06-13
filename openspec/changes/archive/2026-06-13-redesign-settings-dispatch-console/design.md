# Design — Settings Dispatch Console refactor

This document captures the **cross-cutting decisions** that span multiple capabilities. Per-capability requirements live in `specs/*/spec.md`. PLAN.md in `pr/overview/settings-refactor/` remains the source of truth for what the result should *look like*; this document is the source of truth for the trade-offs and invariants.

---

## D1. Information architecture — three sub-routes, no more

| Route                  | Purpose                                       | Source component                                     |
|------------------------|-----------------------------------------------|------------------------------------------------------|
| `/settings`            | Console — overview panel grid + attention strip | `settings-redesign.jsx :: SettingsConsole`           |
| `/settings/models`     | Model catalog — tier-grouped, per-row controls | `settings-redesign.jsx :: ModelCatalogExpanded`      |
| `/settings/spend`      | Spend dashboard — forecast, breakdown, rules    | `settings-expanded.jsx :: SpendDashboard`            |
| `/settings/permissions`| Permissions matrix + audit reel + data ops + webhooks | `settings-expanded.jsx :: DataExpanded`         |

**Rejected alternatives.** Three top-level directions were prototyped (Ledger, Console, Manifest). Console was selected for its `panel-grid + attention-strip` shape because it answers "what's wrong / what's near a limit" *first*, then offers depth on demand. Ledger and Manifest are kept in the prototype only as reference for the model catalog's wide-window treatment.

**Adjacent surfaces, explicit:**

- `/approvals` is its own top-level route, **not** under `/settings/`.
- `/butlers/{name}` owns all per-butler configuration. `/settings/butlers` does **not** exist; the `ButlersExpanded` design from `settings-expanded.jsx` folds into the existing detail page.
- `/memory` owns all memory configuration; the `MemoryExpanded` design folds into it.
- `/secrets` owns per-user OAuth (Google, Spotify, Telegram, Steam, HomeAssistant, OwnTracks, WhatsApp). The current `SettingsPage` cards for these move to `/secrets`. `/settings` is **system-side only**.

## D2. The Dispatch attention-tint pattern

The redesign introduces one and only one expansion of the state-color system: **a 4–7% alpha background tint paired with a 2px left rail in the same state color**, used on rows or panels that *demand human attention* (open approval, auth-renewal needed, model in error). The doctrine amendment lands in `about/heart-and-soul/design-language.md` under the existing `### Butler hue scope` heading (around line 837) and/or the `### Attention list` heading (around line 820) — those sections currently declare the rule the attention tint is an exception to. The amendment lands in the same PR as the first consuming page.

Pattern in CSS (canonical):

```css
.attention-row[data-tone="red"] {
  background: oklch(0.685 0.250 29 / 0.06);
  border-left: 2px solid var(--red);
}
.attention-row[data-tone="amber"] {
  background: oklch(0.810 0.185 84 / 0.05);
  border-left: 2px solid var(--amber);
}
```

The pattern is **only** for "demands attention now" states. Routine status (healthy, idle, neutral) gets no tint, no rail. Two affordances on the same row (e.g. a row already showing a `Sev` glyph) **does not** get the tint either — `one affordance per signal` doctrine still applies.

## D3. Audit log primitive — first, not last

`audit.append(actor, action, target, note, ip, request_id)` is implemented and lands **before** any other backend work in this refactor. Schema: `public.audit_log (id BIGSERIAL, ts TIMESTAMPTZ DEFAULT now(), actor TEXT, action TEXT, target TEXT, note TEXT, ip INET, request_id UUID)`. **No deletes ever.** Indefinite retention — confirmed by doctrine.

Every subsequent mutation endpoint in this refactor calls `audit.append()` after the state change but before returning the response. Permissions mutations refuse without a non-empty `reason` field; the reason is stored in `note`.

The dashboard exposes the log via `GET /api/audit-log?since=&actor=&action=&limit=` and `GET /api/audit-log/{id}`. The router prefix is `/api/audit-log` (matching the existing router; PLAN.md's shorter `/api/audit` is rejected to avoid a rename for an already-shipped surface). The Permissions page shows the last 15 entries; a future top-level `/audit` page (out of scope for this change) would consume the full stream.

## D4. Model catalog routing contract

The catalog is sorted **server-side** by `(complexity_tier, priority DESC, enabled DESC, alias ASC)`. The frontend never sorts; it only filters via chips.

When a butler asks the runtime for a model in tier `T`, the runtime selects the **highest-priority enabled** model in `T` whose `state ∈ {verified, untested}`. If no model qualifies, the runtime falls through to the next tier in the canonical order `reasoning → workhorse → cheap → specialty → local → legacy`.

We **rename + remap the existing `complexity_tier` column** values. The current CHECK constraint values `trivial | medium | high | extra_high | discretion | self_healing` are replaced with the canonical six `reasoning | workhorse | cheap | specialty | local | legacy`. A one-time data migration remaps existing rows by intent:

| Old value      | New value   | Reasoning |
|----------------|-------------|-----------|
| `extra_high`   | `reasoning` | Highest-complexity tasks route to reasoning models. |
| `high`         | `reasoning` | High-complexity tasks also route to reasoning models. |
| `medium`       | `workhorse` | Medium tasks route to default workhorse models. |
| `trivial`      | `cheap`     | Trivial tasks route to cheap models. |
| `discretion`   | `specialty` | Discretion-routed tasks treated as specialty (no default mapping; review per butler). |
| `self_healing` | `specialty` | Self-healing tasks treated as specialty. |

Existing runtime callers passing the old values are updated in the same Alembic revision + code change. The CHECK constraint is rewritten to the new six values. Any caller that cannot be updated mechanically is flagged in tasks.md §3.0 as a follow-up.

`POST /api/settings/models/verify-all` issues a 1-token completion against every enabled model in parallel (bounded concurrency = 8) and stores `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok` per row. **These three columns do not exist today** and are added by the migration in tasks.md §3.1. Re-verification is rate-limited to once per minute system-wide.

## D5. Spend dashboard — forecast and rules

The forecast endpoint returns a daily series (one point per day MTD) and a projected month-end land. **v1 algorithm: `mtd_total ÷ days_elapsed × days_in_month`** with `days_elapsed` clamped to `≥ 1`. Smarter estimation (per-butler decay, weekend adjustment) is left as a `TODO`. The chart is hand-rolled SVG: solid line from day 1 to today, dashed extrapolation from today to month end, hairline at the monthly ceiling.

Routing rules are **stored as JSON in the order they will be evaluated**. Each rule has `condition` (e.g. `{butler: "qa", complexity: "high"}`) and `action` (e.g. `{model: "claude-haiku-4-5"}`). The runtime evaluates top-to-bottom; first match wins. Each rule's `saved_7d` is computed by a daily job comparing the chosen model's cost against the would-have-been baseline.

`WS /api/spend/stream` emits one event per LLM call with `{ts, butler, model, input_tokens, output_tokens, cost_cents}`. The chart appends without re-fetch; it never lags.

## D6. Permissions matrix — `reason` is required

`PUT /api/permissions/{butler}/{perm}` **refuses** without a non-empty `reason` (HTTP 422). The reason is recorded in the audit log alongside the change. The frontend never grays-out the input; it prevents submission until the field is non-empty.

The matrix's columns are butlers (one per active roster entry); rows are permissions (`memory.read`, `memory.write`, `sessions.spawn`, `butlers.logs`, `metrics.read`, `audit.write`, `tools.invoke`, etc.). Cells render as `on/off/inherited`; cells inherit from a default profile if not explicitly set. Inherited cells render dim; explicit cells render foreground.

## D7. Data ops — wipe phrase enforcement

`DELETE /api/data/wipe` requires the literal phrase `WIPE EVERYTHING IRREVERSIBLY` in the request body. Server-side enforcement is the source of truth; the frontend collects but does not pre-validate beyond non-empty (the server is the bouncer).

Wipe deletes: every butler schema, the audit log, the model catalog, runtime config, permissions, spend ledger, webhook registry. It preserves: the user's OAuth tokens on `/secrets` (those are user property, not system property; a separate wipe flow exists at `/secrets`).

`POST /api/data/export` returns a signed URL to an encrypted zip. Scopes: `full | memory | audit | config`. The signed URL TTLs to 60 minutes.

## D8. Webhooks — minimal, signed, retryable

Webhooks are POST-only outbound; the registry stores `endpoint`, `events JSONB` (array), `enabled`, `secret_hash` (HMAC key, hashed at rest), `retry_policy` (`{max_attempts, backoff_seconds}`). On event match, the dispatcher signs the payload with HMAC-SHA256 and POSTs. Retries use the policy. Failures after exhaustion mark `last_test_ok = false` and emit an `attention` strip item on `/settings/permissions`.

`POST /api/webhooks/{id}/test` synthesizes a `webhook.test` event and runs the dispatcher path; the response includes the receiver's status code and latency.

## D9. Approvals — `why` and `evidence`, plus quiet hours

Two columns added to `pending_actions`:

- `why TEXT` — serif paragraph the LLM emitted explaining *why this needs human input*. Rendered in `font-family: var(--font-serif); font-size: 16px;` in the dossier body.
- `evidence JSONB` — array of mono lines (log excerpts, IDs, links). Rendered in `font-family: var(--font-mono); font-size: 11px;` as a rule-separated list.

The agent contract that emits these is captured in module-approvals' spec delta. The migration backfills `null` for legacy rows; the UI tolerates `null` (omits the section) and serif-italic-empty-states the missing pieces.

Quiet hours stored as `{start_hour, end_hour, timezone}` in `public.approvals_policy` (singleton row). The notification dispatcher consults it before paging the owner.

`POST /api/approvals/{id}/defer` `{hours: int}` re-presents the approval at `now + hours`. Defer is bounded `1 ≤ hours ≤ 168` (one week).

## D10. Adjacent fold-ins — same PR or separate?

The fold-ins for `/butlers/{name}` and `/memory` ship in **separate PRs** within this epic. They are listed in tasks.md as Phase 7 and Phase 8 (matching PLAN.md). They depend only on the audit primitive (Phase 1) and can land in parallel with the Settings work once that lands.

Reasoning: each fold-in is a substantial UI rewrite of an existing page. Shipping them inside the same PR as the Console would balloon the change beyond reviewable size and conflate scope. Keeping them as their own PRs preserves rollback granularity.

## D11. Removal of legacy surfaces

In the same PR that lands `SettingsConsolePage`:

- `frontend/src/pages/SettingsPage.tsx` is **deleted**.
- `frontend/src/pages/ApprovalsPage.tsx` is **rewritten** (same file path, new content).
- `frontend/src/components/settings/{BlobStorageCard,QASettingsCard,ModelCatalogCard}.tsx` are **deleted** (their content lives on the new pages or has moved to `/secrets`).
- `frontend/src/components/GeneralSettingsCard.tsx` is **deleted** (note: this file lives at `frontend/src/components/`, NOT `frontend/src/components/settings/` — verified by R2 audit).
- `frontend/src/components/settings/{GoogleOAuthSection,HomeAssistantSetupCard,OwnTracksSetupCard,SpotifySetupCard,SteamSetupCard,WhatsAppSetupCard,GoogleHealthStatusCard}.tsx` **move** under `frontend/src/pages/SecretsPage.tsx` consumption (the components stay; their host page changes).
- `src/butlers/api/routers/costs.py` is **renamed** to `src/butlers/api/routers/spend.py`; the URL prefix changes from `/api/costs` to `/api/spend`. The existing `/api/costs/summary`, `/api/costs/daily`, `/api/costs/top-sessions`, `/api/costs/by-schedule` endpoints become `/api/spend/summary`, `/api/spend/daily`, `/api/spend/top-sessions`, `/api/spend/by-schedule`. Frontend hooks (`use-spend.ts` or equivalent) update their paths in the same change.
- `frontend/src/hooks/use-approvals.ts` is **refactored** to consume the new `/api/approvals` flat-list endpoints. The PLAN.md draft assumed split hooks (`use-approval-actions.ts`, `use-autonomy-suggestions.ts`, `use-approval-rules.ts`); those files DO NOT exist in the repo today (R2 audit confirmed). The single `use-approvals.ts` hook is the only thing that needs editing.

No backwards-compatibility shims. No "legacy" route alias. The old `/settings` URL hard-redirects to the new `/settings` Console.

## D12. Tests, observability, dev mode

- **Unit:** every new endpoint has an `audit.append()` assertion. The wipe phrase enforcement has a unit test for the exact-string case (and a fuzz test for trailing whitespace / case variations).
- **Integration:** model verify-all spawns N concurrent 1-token completions and asserts the parallel bound. Spend forecast asserts the projection arithmetic. Webhooks have an integration test that runs the test endpoint against an in-process httpx mock receiver.
- **e2e (Playwright):** `/settings`, `/settings/models`, `/settings/spend`, `/settings/permissions`, `/approvals` — happy path + one attention-state per page.
- **Dev-mode `ApiWireFooter`**: an analog of the prototype's footer that lists, in mono, the endpoints each page is hitting. Off in production. Useful for spotting accidental N+1 fetches during development.
- **Observability:** `audit_log_appended_total`, `model_verify_all_duration_seconds`, `spend_forecast_latency_seconds`, `permissions_changes_total{reason_provided}` (which is always 1 — refuses 0). Counters land in the same PR as their endpoint.

## D13. Open questions resolved (PLAN.md §9)

| Q | Answer |
|---|---|
| Wipe phrase | `WIPE EVERYTHING IRREVERSIBLY` (fixed, server-side). |
| Default routing tier on butler creation | `workhorse`, with the option to override per-butler. |
| Anomaly detection threshold | Deferred — surface as a `TODO` in spend; not in v1 of this refactor. |
| Audit retention | Indefinite, no expiry — committed. |
| Approval auto-decisions copy | "auto-approve" (not "merge", not "land") — neutral, descriptive. |

## D15. Async job ownership (R7 wiring closure)

Every async / scheduled job and storage primitive in this change has a single owning module/bead. Where the owner is implicit, it is named here.

| Concern | Owner module | Owner bead | Notes |
|---|---|---|---|
| **Approval re-presentation timer** (defer mechanism) | `src/butlers/modules/approvals/scheduler.py` (extend the existing approvals scheduler) | `bu-5xiu9` (Phase 6 — Approvals) | The defer endpoint stores `expires_at = max(current, now + hours)` and the existing approvals scheduler consumes it. No separate cron. |
| **Notification dispatcher quiet-hours consultation** | `src/butlers/modules/approvals/notifier.py` (or wherever the existing notify-on-pending logic lives — confirm during B8 implementation) | `bu-5xiu9` (Phase 6 — Approvals) | The dispatcher reads `approvals_policy.quiet_*` before paging; this is a synchronous check, not a separate job. |
| **Spend rules `saved_7d` daily computation** | `src/butlers/api/routers/spend.py` (new background task) | `bu-dvb7i` (Phase 3 — Spend) | Triggered by the dashboard daemon scheduler at a fixed UTC time (default 04:15). |
| **Memory cleanup job (retention)** | `src/butlers/modules/memory/cleanup.py` (extend existing) | `bu-1kzbg` (Phase 8 — Memory) | Existing job extended to read `memory_retention_policies` per kind. |
| **Verify-all rate-limit storage** | In-memory module global keyed by minute window (Python `time.monotonic()`-bucketed dict) in `src/butlers/api/routers/model_settings.py` | `bu-q2nz3` (Phase 2 — Models page) | Single-process FastAPI worker (the existing dashboard daemon is single-worker per the deployment topology). On daemon restart the limit resets; this is acceptable for v1. If we move to multi-worker, switch to a DB-backed sequence. |
| **Settings Console aggregator cache** | In-memory TTL=10s cache keyed by `actor` identity (single-tenant: a single "owner" identity, so effectively global). | `bu-ju4kh` (Phase 5 — Console) | If multi-tenant is added later, the cache key becomes `(actor, key)`. v1 has one owner per deployment. |
| **AttentionStrip cap** | Server caps `attention[]` at 5 items; UI renders all returned items + a "…N more" indicator if `_truncated_count > 0` field is non-zero. | `bu-ju4kh` (Phase 5 — Console) | Prevents wall-of-attention. |
| **Webhook delivery retries** | Existing `httpx` async client in `src/butlers/api/routers/webhooks.py` consults `retry_policy` per row; no separate retry queue. | `bu-vz6pi` (Phase 4 — Permissions) | After exhaustion → `last_test_ok=false` + an attention item surfaces via the Console aggregator. |

## D16. Authentication contract (R10 critical closure)

Every mutation endpoint and every WebSocket endpoint introduced by this change SHALL require authentication. The dashboard authentication primitive in the codebase is the existing `ApiKeyMiddleware` consuming `DASHBOARD_API_KEY` via the `X-API-Key` header.

| Surface | Auth requirement | Failure mode |
|---|---|---|
| `DELETE /api/data/wipe` | **MUST** require valid `X-API-Key`. If `DASHBOARD_API_KEY` is unset on the server, the endpoint MUST refuse with `503 Service Unavailable` body `{error: "auth_unconfigured"}` — the endpoint is not reachable at all without configured auth. Plus the phrase check on top. | 503 (auth unconfigured), 401 (wrong key), 422 (phrase mismatch). |
| `DELETE /api/data/export` | Require `X-API-Key`. Signed URL has 60-minute TTL and is single-use. | 401 (wrong key). |
| `PUT /api/permissions/{butler}/{perm}` | Require `X-API-Key`. | 401. |
| `PUT /api/spend/ceiling`, all `/api/spend/rules` writes, all `/api/webhooks` writes, all `/api/settings/models/*` writes, all `/api/approvals/{id}/*` mutations, all `/api/butlers/{name}/*` mutations | Require `X-API-Key`. | 401. |
| `WS /api/settings/stream`, `WS /api/spend/stream`, `WS /api/approvals/stream` | Require auth at the HTTP upgrade. Two acceptable patterns: (a) `?api_key=<value>` query parameter validated before upgrade; (b) session cookie if dashboard has session auth. Choose (a) for v1 — simplest, matches existing pattern. | 401 (upgrade refused). |

**Reason field secret filter**: `PUT /api/permissions/{butler}/{perm}` rejects with `422 {error: "reason_contains_credential"}` if the `reason` matches case-insensitive `(password|token|secret|api[_-]?key|credential|private[_-]?key)`. This prevents the audit log from accidentally storing credentials in plain text. Implemented as a helper `validate_no_secrets(text) -> None` in `src/butlers/api/security.py`.

**Webhook secret retrieval**: No endpoint returns `secret` after creation. `POST /api/webhooks` returns the secret ONCE in the response body and never again. The DB stores `secret_hash` (HMAC key for signing, not a cryptographic hash of an unknowable secret — clarify in implementation: this is the symmetric signing key, stored encrypted-at-rest via the secrets manager). `GET /api/webhooks` and `GET /api/webhooks/{id}` SHALL NOT include the `secret` field; they MAY include a `secret_id` (UUID) or `secret_prefix` (first 6 chars + ellipsis) for human identification. Secret rotation is `PUT /api/webhooks/{id} {regenerate_secret: true}` which returns the new value once.

## D17. Failure-mode contracts (R7 closure)

| Endpoint | Failure mode | Behavior |
|---|---|---|
| `audit.append()` | Audit table missing | Raises `AuditTableNotAvailableError`. Mutation endpoint propagates the exception; HTTP 503 with body `{error: "audit_unavailable"}`. The mutation MUST NOT have been committed if the audit append failed (use a SQL transaction wrapping both the state change and the audit append). |
| `DELETE /api/data/wipe` | Partial-drop failure (drop of schema X fails mid-wipe) | All drops are wrapped in a single SQL transaction. If any drop fails, the entire wipe rolls back; HTTP 500 with body `{error: "wipe_partial_failure", failed_at: "<step>"}`. The audit_log retains the failed-attempt entry as the first row appended in the transaction. |
| `GET /api/settings/console` | A sub-system aggregation fails (e.g., spend backend is down) | The endpoint returns a partial response: header counts that succeeded + `attention[]` items composed from sub-systems that responded. Failed sub-systems contribute one `attention` item `{tone: "amber", kind: "system", text: "Spend aggregation failed: <error_id>", action_route: "/settings/spend"}` so the operator notices. Cache TTL still applies. |
| `POST /api/settings/models/verify-all` | A single model verification fails | The failed model's row gets `last_verified_ok=false` and a transition (if applicable) to `state="error"`. The endpoint returns 200 with per-model results; failures are not the whole-call failure. The audit.append records the run, not per-model. |
| `WS /api/settings/stream` (and siblings) | Client disconnects mid-stream | The server cleans up the subscription; reconnection emits a full snapshot before resuming incremental events. No retained backlog. |

## D18. Migration safety contracts (R10 closure)

**Two-phase `complexity_tier` migration** (replaces the one-shot in tasks.md §3.1):

Phase 1a (this change): expand the CHECK constraint to accept BOTH old AND new values (`trivial|medium|high|extra_high|discretion|self_healing|reasoning|workhorse|cheap|specialty|local|legacy`). Add the three `last_verified_*` columns. Ship code that emits ONLY new values; add a deprecation log for any caller still emitting old values.

Phase 1b (separate change, ≥ 7 days after Phase 1a soaks): drop old values from the CHECK constraint. The runtime selector accepts only new values; any row still holding an old value is normalized in this migration's `UPDATE` step. Deprecation logging removed.

This eliminates the inconsistency window R10 flagged.

**`costs.py` → `spend.py` deprecation period**:

Phase 3 ships dual mounts: BOTH `/api/costs/*` (existing) AND `/api/spend/*` (new). Both routes call the same handlers. `/api/costs/*` responses include `Deprecation: true` and `Sunset: <date 90 days out>` headers per RFC 8594. Frontend hooks update to `/api/spend/*` immediately. After 90 days, a separate change deletes `/api/costs/*`.

This eliminates the breaking-change risk for any external consumer.

**Pending actions `why`/`evidence` migration soaking**:

Phase 6 ships nullable columns with backfill `NULL` / `'[]'`. The UI tolerates null. After 7 days of agent rollout, a follow-up migration MAY add `NOT NULL` if observed agent emission is ≥ 99%. This follow-up is OUT of scope for this change (a future bead). Counter `approvals_created_without_evidence_total` is added in Phase 6 to monitor.

## D19. Forecast sanity (R10 closure)

The naive forecast `mtd / max(days_elapsed, 1) × days_in_month` is unreliable in the first 2 calendar days of a month (high variance). Implementation rules:

- If `days_elapsed < 3`, the API returns `projected_eom_usd` AND a sibling field `projection_confidence: "low"`.
- If `days_elapsed >= 3`, `projection_confidence: "normal"`.
- The Console aggregator's `attention` item "spend within 10% of ceiling" SHALL NOT fire when `projection_confidence == "low"` to prevent false positives early in the month.

Out of scope: smarter estimator (weekend adjustment, per-butler decay). Tracked as future work.

## D15–D19 finalize the wiring/security/migration risks surfaced by R7 and R10 reconciliation passes. Phase 0–8 tasks below reference these contracts.

## D14. Acceptance criteria (matches PLAN.md §7)

The work breaks into phases that share a hard dependency on Phase 0 (doctrine) and Phase 1 (foundations: audit log + model routing). Once Phase 1 lands, Phases 2–4 and 6–8 can run in parallel; Phase 5 (Console) requires all sub-pages to ship first. Cleanup (Phase 11) and Report (Phase 12) gate behind all preceding phases.

```
Phase 0 — Doctrine (design-language amendment, v1.md amendment, attention-tint CSS)
    │
Phase 1 — Foundations
    ├── audit_log migration + audit.append() helper + GET /api/audit-log
    └── model_catalog tier rename/remap + last_verified_* columns + routing contract
    │
    ├──→ Phase 2 — /settings/models  (model endpoints, frontend page)
    ├──→ Phase 3 — /settings/spend   (spend.py rename + new endpoints + page)
    ├──→ Phase 4 — /settings/permissions  (permissions/data_ops/webhooks + page)
    ├──→ Phase 6 — /approvals replacement  (why/evidence migration, /api/approvals, page rewrite)
    ├──→ Phase 7 — /butlers/{name} fold-in  (prompt history, tools, kill switch)
    └──→ Phase 8 — /memory fold-in  (retention policies, compaction log, inspect)
            │
            └──→ Phase 5 — /settings Console  (aggregator + AttentionStrip + nav)
                    │
                    └──→ Phase 11 — Cleanup (delete legacy files, redirect)
                            │
                            └──→ Phase 12 — Reconciliation report
```

Parallel-safe pairs after Phase 1: {2 ∥ 3 ∥ 4 ∥ 6 ∥ 7 ∥ 8}. No shared files within the listed pages/endpoints. Phase 5 (Console) is the only fan-in; it consumes all sub-page summaries.

## D14. Acceptance criteria (matches PLAN.md §7)

- [ ] All routes in §D1 render and use Dispatch.
- [ ] No emoji anywhere in new pages.
- [ ] Numerals are tabular everywhere.
- [ ] State color only when state demands; the attention-tint pattern is implemented consistently (4–7% alpha + 2px left rail).
- [ ] Model catalog server-sorts `(tier, priority DESC, enabled DESC, alias ASC)`. Priority stepper round-trips within 200ms in dev.
- [ ] Spend chart is hand-rolled SVG; forecast line dashed from today.
- [ ] Permissions mutations 422 without `reason`.
- [ ] `/approvals` replaces — not duplicates — the existing route. Old component file deleted in the same PR.
- [ ] Per-butler config does **not** live under `/settings/`. Anyone looking for it lands on `/butlers/{name}`.
- [ ] Audit log records every config change. `/audit` reads as prose.
