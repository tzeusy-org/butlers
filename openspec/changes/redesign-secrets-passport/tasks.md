# Tasks — redesign-secrets-passport

Tasks are grouped into two parent tracks that the beads-writer will materialise as two epics with a `frontend → backend` blocking edge. Within each track, tasks are listed in implementation order.

## Track A — Backend Contracts (epic `secrets redesign — backend contracts`)

Per brief §3 "Proposed backend epic" the dependency order is `1, 2 → 3 → 4 → {7, 8, 9, 10}`, with `5, 6, 11` parallelisable. Bead 12 follows bead 7.

### A1 — DB foundations
- [ ] Alembic migration: create `public.secret_probe_log` with columns and index per `core-credentials §public.secret_probe_log Cross-Butler Probe History Table`. (brief §3 bead 1)
- [ ] Alembic migration: extend `public.audit_log` action enum with `verified`, `failed`, `rotated`, `connected`, `disconnected`, `warned`, `overrode`, `attempted`, `set` per `core-credentials §Audit Action Enum Extension`. (brief §3 bead 1)
- [ ] Alembic migration: add `ix_audit_log_target_ts` index on `public.audit_log (target, ts DESC)` per `core-credentials §public.audit_log Index for Credential-Key Filtering`. (`public.audit_log` timestamp column is `ts`, per `redesign-settings-dispatch-console`'s `dashboard-audit-log` spec.) (brief §3 bead 1)
- [ ] Alembic migration: add `last_verified`, `last_test_ok`, `last_test_code`, `last_test_message` columns to `butler_secrets` (per-butler-schema-aware) and `public.entity_info` per `core-credentials §Test-State Columns on Credential Tables`. Backfill: NULL. (brief §3 bead 2)
- [ ] Alembic migration: create `public.provider_feature_catalogue` with columns and indexes per `core-credentials §public.provider_feature_catalogue WhatBreaks Source-of-Truth Table`. (brief §3 bead 6)
- [ ] Alembic seed: bootstrap `public.provider_feature_catalogue` with the providers known at change-implementation time (Google, Telegram, Spotify, Home Assistant, WhatsApp, OwnTracks, Steam, plus any others discovered in the roster).
- [ ] Python utility: implement `normalize_credential_key(scope: str, key: str) -> str` in `src/butlers/credential_store.py` (or a new `src/butlers/credentials/keys.py`) per `core-credentials §Credential-Key Normalisation Function`.

### A2 — Backend reads
- [ ] Create `src/butlers/api/routers/secrets_v2.py` (decision: see design.md "Decision: Separate /api/secrets/* Namespace").
- [ ] Implement `GET /api/secrets/inventory` per `dashboard-api §Inventory endpoint shape`. Reads `butler_secrets` across all butler schemas + `public.entity_info` + CLI runtime store; merges probe-log LRU; computes fingerprints on-read. (brief §3 bead 3)
- [ ] Implement `GET /api/secrets/user/<provider>`, `GET /api/secrets/system/<key>`, `GET /api/secrets/cli/<id>` per `dashboard-api §Per-credential read endpoints`. (brief §3 bead 4)
- [ ] Implement `GET /api/secrets/audit/<scope>/<key>` per `dashboard-api §Audit history endpoint`. Server-side timestamp pre-formatting. (brief §3 bead 5)
- [ ] Implement `GET /api/secrets/breaks-catalogue` per `dashboard-api §Breaks-catalogue endpoint`. (brief §3 bead 6 — backend half)
- [ ] Add `?key=` query param to `GET /api/audit-log` per `dashboard-api §Audit Log Filter by Credential Key`. Use `normalize_credential_key()`. (brief §3 bead 11)
- [ ] Wire per-butler startup hook so each butler UPSERTs its `(provider, butler, feature, severity, required_scopes)` rows into `public.provider_feature_catalogue` on boot. Idempotent.

### A3 — Backend writes + OAuth generalisation
- [ ] Implement `POST /api/secrets/user/<provider>/probe`, `/system/<key>/probe`, `/cli/<id>/probe` per `dashboard-api §User credential mutations` and §System credential mutations. Each writes to `public.secret_probe_log` + updates test-state columns + writes audit row in one transaction. (brief §3 beads 7, 8, 9, 10 — probe portion)
- [ ] Implement `POST /api/secrets/user/<provider>/rotate`, `/disconnect` per `dashboard-api §User credential mutations`. Audit on every action. (brief §3 bead 8)
- [ ] Implement `POST /api/secrets/system/<key>`, `DELETE /api/secrets/system/<key>` per `dashboard-api §System credential mutations`. Supports `target=shared` and `target=<butler>` for overrides. Audit with `set` / `rotated` / `overrode` / `disconnected` / `revoked`. (brief §3 bead 9)
- [ ] Implement `POST /api/secrets/cli/<id>/rotate`, `/revoke` per `dashboard-api §CLI runtime mutations`. Rotate returns the raw value once. (brief §3 bead 10)
- [ ] Generalise `src/butlers/api/routers/oauth.py` from Google-only to `<provider>` path per `dashboard-api §OAuth Per-Provider Generalisation`. Existing `/api/oauth/google/*` paths preserved as `provider=google`. Scope-sets resolved from `butler.toml`. (brief §3 bead 7)
- [ ] Update OAuth callback to inspect `state.page_of_origin` and route accordingly per `dashboard-api §Generalised callback endpoint` and `butler-secrets §Cross-Page Reauth Bookkeeping`. (brief §3 bead 12)
- [ ] Implement `POST /api/secrets/user/<provider>/reauthorize` per `dashboard-api §User credential mutations` — returns `redirect_url` with `page_of_origin=secrets` in the state token.

### A4 — Backend tests
- [ ] Unit tests for `normalize_credential_key()` covering all three scopes.
- [ ] Integration tests for `GET /api/secrets/inventory` covering: empty store, mixed states, projection-lens identity filtering, envelope conformance.
- [ ] Integration tests for each per-credential read endpoint covering: hit, miss (404), envelope conformance.
- [ ] Integration tests for probe-log writes: one row per probe call; test-state cache updates inside the same transaction.
- [ ] Integration tests for OAuth generalisation: `provider=google` regression; `provider=spotify` happy path (mocked OAuth); `page_of_origin` round-trip.
- [ ] Integration tests for `/api/audit-log?key=` filter: matches normalised target; ignores rows with non-matching target.
- [ ] Performance test: `/api/secrets/inventory` returns in < 500 ms at p99 with 100 credentials + 10k probe-log rows.

## Track B — Frontend (epic `secrets redesign — passport book frontend`)

Track B is `blocked-by` Track A: frontend cannot land until backend contracts land. Within Track B the order below is implementation-suggested; the beads-writer may resequence based on parallelisation opportunities.

### B0 — Stack preparation
- [ ] Verify fonts (Inter Tight / Source Serif 4 / JetBrains Mono) are loaded by prior Dispatch rollouts (`frontend/src/index.css`). Add `@import` only if missing. (brief §5 Q10)
- [ ] Verify oklch token names (`--bg`, `--fg`, `--mfg`, `--dim`, `--border`, etc.) against the binding `pr/overview/secrets-redesign/DESIGN_LANGUAGE.md`. Reconcile any naming mismatch. (brief §5 Q11)
- [ ] Decide tweaks-persistence mechanism per design.md Q9 default (match ingestion/entity redesign pattern if shipped; else `localStorage`).

### B1 — Primitives extraction (reusable shadcn-aligned components)
- [ ] Extract `Eyebrow` to `frontend/src/components/ui/Eyebrow.tsx` (mono 10px, tracking 0.14em).
- [ ] Extract `Mono` to `frontend/src/components/ui/Mono.tsx`.
- [ ] Extract `Voice` to `frontend/src/components/ui/Voice.tsx` (Source Serif 4).
- [ ] Extract `Display` to `frontend/src/components/ui/Display.tsx` (44px, weight 500, tight tracking).
- [ ] Extract `Title` to `frontend/src/components/ui/Title.tsx` (22–24px, weight 500).
- [ ] Verify `StateDot` (existing) covers all four states `ok/degraded/error/waiting` with oklch values matching the Dispatch spec.
- [ ] Verify `ButlerMark` (existing) renders 16px square letter-mark with butler hue only (no leakage).
- [ ] Verify `Pill` (existing) supports `commit` and `danger` variants per `PillBtn` in brief §2.

### B2 — Passport-book primitives (new)
- [ ] `Sliver` — 2px vertical rail; coloured only when `state` demands.
- [ ] `StateLabel` — mono 10px lowercase state label.
- [ ] `Fingerprint` — hash display with scheme/hash split (`sha256:7a3f…`).
- [ ] `FingerprintRow` — two-line stack: scheme·hash + verify command (toggled by tweak).
- [ ] `KV` — generic label + mono value pair.
- [ ] `BlockHead` — mono eyebrow with optional right caption.
- [ ] `StampGlyph` — 1-char mono shape per audit action (one of `✓ verified`, `↻ rotated`, `✕ failed`, `⊘ revoked`, `⊕ connected`, `! warned`, `⤳ overrode`, `▷ attempted`, `⊙ set`).
- [ ] `StampRow` — `StampGlyph + date/time + action + actor + serif note`.
- [ ] `SeverityPip` — 1-char mono pip (`high/medium/low`) for WhatBreaks rows.
- [ ] `ProviderMark` — mono 22px square letter-mark; hairline border, no colour.
- [ ] `IdentityChip` — name + role + colour-coded dot.
- [ ] `ScopeRow`, `ScopeBalance`, `VisaRow` — scope inventory components.
- [ ] `ProbeResult` — latency / code / timestamp / serif-italic verbatim message.
- [ ] `WhatBreaks` — list component; consumes `BreakEntry[]` from `/api/secrets/breaks-catalogue`.

### B3 — Page composition
- [ ] `Spine` — left index; `SpineSearch` + `SortPicker` + `IdentityChip` header; `SpineGroup` + `SpineRow` for each grouped row.
- [ ] `PageUser` — replaces Integrations section + SecretsTable (user mode). Per-kind variants (oauth / token / apikey / webhook) with shared row template + per-provider drawer.
- [ ] `PageSystem` — replaces SystemSecretsSection + SecretsTable (system mode). Single system secret on page; shared vs local row state; override affordance; plain-text-value branch.
- [ ] `PageCli` — replaces `CLIAuthCard`. CLI runtime detail; how-to-use snippet; rotate-with-reveal flow.
- [ ] `TweaksPanel` — reveal mode, default sort, show-verify-cmd, voice-paragraph toggles.
- [ ] `DirectionPassport` — top-level orchestrator; holds identity / focus / sort / search state; URL-syncs via `useSearchParams()`.

### B4 — Routing + integration
- [ ] Replace `frontend/src/pages/SecretsPage.tsx` with the new `DirectionPassport` mount.
- [ ] Wire `?focus=<key>` deep-link via `parseFocus` / `encodeFocus` per `butler-secrets §Deep-Link Focus Routing`.
- [ ] Wire `?identity=<id>` projection-lens switching per `butler-secrets §Projection-Lens Identity Switcher`.
- [ ] Wire `?sort=<mode>` per spec.
- [ ] Wire OAuth begin (`/secrets/connect/<provider>`) and callback re-entry (`/secrets?focus=u:<provider>&toast=connected`).
- [ ] Adapt `IdentitySwitcher` from existing `EntityPicker` (`SecretsPage.tsx:177-272`) — keep selection logic, replace UI with compact identity chip.
- [ ] Delete the six bespoke provider Setup cards under `frontend/src/components/settings/` (Google, Spotify, Home Assistant, WhatsApp, OwnTracks, Steam).
- [ ] Delete `frontend/src/components/CLIAuthCard.tsx`.
- [ ] Delete embedded `EntityPicker` usage from the legacy `SecretsPage.tsx`.

### B5 — Frontend tests
- [ ] Component tests for each B2 primitive: render shapes, state-colour behaviour, mono-font assertions.
- [ ] Component test: spine renders empty `needs-hand` group correctly (zero red/amber pixels on all-`ok` day).
- [ ] Component test: identity-switch re-projects User group; CLI + System unchanged.
- [ ] Component test: deep-link `/secrets?focus=u:google` renders the User page for `google` with the spine row highlighted.
- [ ] Component test: tweak `reveal-mode=never` hides eye-toggle from all rows.
- [ ] Snapshot test: full passport-book page renders with the binding Dispatch design language (typography, spacing, colour tokens).
- [ ] E2E test (playwright): OAuth round-trip — click reauthorize, mock provider, return to `/secrets?focus=u:<p>&toast=connected`.
- [ ] Lint rule (if feasible): forbid LLM-related imports (`@anthropic-ai/sdk`, etc.) from any file under `frontend/src/pages/secrets/`. Enforces `butler-secrets §No-LLM-Narration Invariant`.

## Track C — Cross-cutting (small, do not need their own epic)

- [ ] Update `docs/redesigns/` index to link this brief, this change, and the eventual implementation PRs.
- [ ] Final reconciliation: run `openspec change validate redesign-secrets-passport --strict` and resolve any remaining warnings.
- [ ] Final reconciliation: post-implementation, run the spec-vs-code reconciliation per `/reconcile-spec-to-project` to confirm zero drift on the three modified specs.
