# Design — redesign-secrets-passport

## Context

The binding input for this change is:

- **Brief** (binding): `docs/redesigns/2026-05-25-secrets-brief.md`. Section 0 is binding design intent; §3 is binding backend-contract delta; §4 is guardrail output; §5 is the consolidated open-questions log; §6 is the binding-paths declaration.
- **Bundle** (binding visual artefacts): `pr/overview/secrets-redesign/` — passport-book prototype, HANDOFF.md, prompts/00–05, VISION.md.
- **Design language** (binding): `pr/overview/secrets-redesign/DESIGN_LANGUAGE.md` (Dispatch).
- **Doctrine** (constraint surface): `about/heart-and-soul/{security.md, vision.md, design-language.md}` + RFCs `0004-identity`, `0006-schema-isolation`, `0007-dashboard-api`.

This design document carries forward open questions, design decisions, and the rationale chain from the brief into the implementation phase. It does NOT re-specify what the `proposal.md` and the three spec deltas (`butler-secrets`, `dashboard-api`, `core-credentials`) already require.

## Goals / Non-Goals

**Goals:**

1. Replace the flat-table `/secrets` page with a passport-book IA that lets the owner distinguish healthy from sick credentials without revealing any value.
2. Unify the System / User / CLI families under one row template + per-provider drawer pattern.
3. Add evidence-over-value affordances (fingerprint, last-verified, scopes, probe result, WhatBreaks) backed by a small set of new read-side endpoints, two new public tables (probe log + feature catalogue), and four new test-state columns on existing credential tables.
4. Generalise the OAuth flow from Google-only to multi-provider, threading `page_of_origin` through the state token for cross-page reauth bookkeeping.
5. Lock the "no LLM narration on `/secrets`" guardrail into the spec as a binding invariant.

**Non-goals:**

1. No storage migration of secret values. `butler_secrets` stays for system, `entity_info` stays for user.
2. No multi-user access control. The identity switcher is a projection lens, not an auth boundary (matches `about/heart-and-soul/security.md:7-8, 18-20`).
3. No bulk operations (rotate-all, revoke-all, export-all). One credential at a time.
4. No merge with `/settings`. `/settings` is system-side knobs; `/secrets` is credentials. Strictly disjoint.
5. No attempt to be the ingestion-channel health dashboard; that remains `/ingestion/connectors`. Both surfaces reflect the same underlying credential state but render different views.

## Phase 1 Doctrine Reconciliation — Carried Forward

Two amendments emerged from Phase 1 reconciliation and have been encoded into the spec deltas:

- **F1 (envelope conformance):** Brief §3's response-shape examples (e.g. `{ cli: [...], system: [...], user: [...] }` returned bare from `GET /api/secrets/inventory`) did not show the `ApiResponse<T>` envelope required by RFC 0007 (`about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md:42-72`). Resolved: the `dashboard-api` spec delta requires `ApiResponse<T>` for every new `/api/secrets/*` and `/api/oauth/*` endpoint; arrays/aggregates nest inside `data`.
- **F2 (audit prefix):** Brief §3 referenced `/api/audit?key=<key>` but the live router is `/api/audit-log` (`src/butlers/api/routers/audit.py:42`). Resolved: spec deltas use `/api/audit-log?key=<key>` and the `core-credentials` delta defines a `normalize_credential_key()` utility so existing audit rows can be matched against the new canonical key format.

No other doctrine conflicts surfaced. Brief Phase D's full 11-butler manifesto sweep was not redone; spot-check of `roster/qa/MANIFESTO.md:172-173` confirmed Phase D's `identity preserved` verdict.

## Open Questions (Carried from Brief §5)

Each item below is either resolved in the spec or carried forward to the implementation phase. Items marked **resolved-in-spec** are encoded in the three spec delta files; items marked **decision-needed** require human input before the relevant child bead is started; items marked **defer-to-implementation** are implementation-detail choices that the beads-coordinator may make.

| ID | Origin | Item | Status | Resolution / next step |
|----|----|----|----|----|
| Q1 | Brief §5 / BRIEF.md:419-423 | Probe safety for paid LLM keys (1-token completion vs `models.list`-style free endpoint) | **resolved-by-owner (2026-05-25)** | Decision: **1-token completion, rate-limited to 1 call per page-load per key**. Brief §4 rates `green` at this cadence (~$0.0003/user/day). BE-8 and BE-9 implementations MUST enforce the per-page-load-per-key throttle (server-side debounce or client-side `useRef` guard wired into the probe button). |
| Q2 | Brief §5 / BRIEF.md:425-429 | Audit retention deep-link param name confirmation | **resolved-in-spec** | `dashboard-api §Audit Log Filter by Credential Key`: param name is `key`, value format `u:<provider>` / `s:<KEY>` / `c:<id>`. |
| Q3 | Brief §5 / BRIEF.md:429-434 | Webhook secret rotation external-reconfig instructions | **resolved-by-owner (2026-05-25)** | Decision: **render the hint inline inside the webhook-provider drawer; do not block rotation on confirmation**. Stored prose only (no LLM narration, per §0 invariant). FE-3 freezes drawer copy as part of page composition; copy lives in `frontend/src/config/provider-drawers.json` (or equivalent) alongside other provider-specific oddities. |
| Q4 | Brief §5 / BRIEF.md:436-438 | Identity switcher chrome when only one identity exists | **resolved-in-spec** | `butler-secrets §Projection-Lens Identity Switcher`: chip hidden when only the owner is in scope; `?identity=` ignored if present. |
| Q5 | Brief §5 / prompts/00-foundation.md §5.3 | Focus-key URL encoding (colon vs encoded variant) | **resolved-in-spec** | `butler-secrets §Deep-Link Focus Routing`: colons are permitted unencoded per RFC 3986 §3.4. |
| Q6 | Brief §5 / HANDOFF.md §2.5 | Per-kind PageUser field deltas (oauth / token / apikey / webhook) | **resolved-in-spec** | `dashboard-api §Per-credential read endpoints`: `UserSecret` shape lists every field; per-kind variants populate / omit fields by `kind`. |
| Q7 | Brief §5 | Spine `needs-hand` pin: backend tag vs client-side compute | **defer-to-implementation** | Per-row `needs_hand` flag is client-derived from `state != ok` (so the backend `/inventory` row shape stays stable). The aggregate `meta.needs_hand_count` field IS server-computed and SHALL be returned by `/inventory` for spine ordering / page-header KPI — see `dashboard-api/spec.md §Inventory endpoint shape`. Beads-writer may flag for revisitation if performance evidence emerges. |
| Q8 | Brief §5 | `WhatBreaks` empty-state when `state=never_set` | **defer-to-implementation** | Default per `butler-secrets`: render the block only when the catalogue returns at least one row for the provider; otherwise omit the entire block (no "no features depend on this" placeholder). |
| Q9 | Brief §5 | Tweaks persistence mechanism (localStorage vs URL fragment vs server-side prefs) | **superseded** | Product decision: `/secrets` no longer exposes prototype tweaks chrome. Stale `secrets.tweaks.*` localStorage is ignored; `?sort=` remains the URL-backed sort override. |
| Q10 | Brief §5 | Font verification (Inter Tight / Source Serif 4 / JetBrains Mono) | **defer-to-implementation** | First bead in the frontend epic verifies fonts are loaded by prior Dispatch rollouts; if not, adds `@import` to `index.css`. |
| Q11 | Brief §5 | Color-token reconciliation (`--bg/--fg/--mfg/--dim/--border` naming) | **defer-to-implementation** | First bead in the frontend epic verifies against `frontend/src/index.css` and reconciles any naming mismatch against the Dispatch spec. |
| Q12 | Brief §5 | Member-view access control implementation | **resolved-in-spec** | `butler-secrets §Projection-Lens Identity Switcher` + `dashboard-api §Mutation endpoints ignore ?identity= for authorization`: documented as binding invariant for v1; no auth boundary at the backend. |
| Q13 | Brief §5 | OAuth router namespace (`oauth.py` with `<provider>` vs new `secrets.py`) | **resolved-by-owner (2026-05-25)** | Decision: **extend `src/butlers/api/routers/oauth.py` in place** with a `<provider>` path parameter. Do NOT introduce a parallel router. BE-7 implements the generalisation against the existing router; the Google-specific code paths become the default `<provider>=google` branch and are pulled out into a provider registry as new providers are added. |
| Q14 | Brief §5 | Audit `?key=` filter — `target` column format normalization | **resolved-in-spec** | `core-credentials §Credential-Key Normalisation Function`: `normalize_credential_key(scope, key) -> "<scope-letter>:<key>"`. New audit writes use this format directly; the `/api/audit-log?key=` filter normalises the query param before matching. |
| Q15 | Brief §5 | LLM-narration guardrail enforcement (lint rule vs doctrine ADR) | **resolved-in-spec** | `butler-secrets §No-LLM-Narration Invariant`: encoded as a binding spec invariant. A future doctrine ADR under `about/heart-and-soul/` (or an automated lint) MAY further enforce; this change does not author such an ADR. |
| Q16 | Brief §5 | Pricing reference `last_verified` < 60 days check | **defer-to-implementation** | First Phase-G bead in the change-implementation flow MUST verify `references/llm-pricing.md last_verified` is current; if not, refresh from anthropic.com/pricing before starting bead work. |

## Decision: Router Namespace Strategy

Recommendation: **extend `src/butlers/api/routers/oauth.py` in place** to accept `<provider>` paths. Rationale:

- The existing `oauth.py` already owns the credential-storage logic (`butler_secrets` for client_id/secret, `public.entity_info` for refresh tokens via `google_accounts`). A parallel `secrets.py` would either duplicate this logic or thread cross-router dependencies.
- The existing routes (`/api/oauth/google/*`) become aliases for the generalised form (`/api/oauth/<provider=google>/*`), preserving backward compatibility with no consumer-facing breakage.
- Auto-discovery wiring in `src/butlers/api/router_discovery.py` already discovers `oauth.py`; no discovery changes needed.

Trade-off: `oauth.py` becomes longer (currently 1893 lines). If the implementer prefers a per-provider sub-module pattern (e.g. `oauth/google.py`, `oauth/spotify.py`, with a thin dispatcher), that is a refactor within the same router contract and does not require a spec change.

Bead 7 (OAuth generalisation) should confirm with the human reviewer before starting if the implementer disagrees with the in-place extension recommendation.

## Decision: Separate `/api/secrets/*` Namespace vs Extending Existing Router

The existing `src/butlers/api/routers/secrets.py` is scoped to `/api/butlers/{name}/secrets/*` and exposes raw CRUD over `butler_secrets`. The redesigned `/secrets` page needs aggregated, evidence-rich, multi-table reads that span `butler_secrets`, `public.entity_info`, the CLI runtime store, the probe log, the feature catalogue, and the audit log.

Recommendation: **introduce a new router at `src/butlers/api/routers/secrets_v2.py`** (or rename `secrets.py` → `secrets_legacy.py` and let the new one take the `secrets.py` slot) that owns the `/api/secrets/*` prefix. Rationale:

- Keeps the legacy CRUD endpoints stable for any internal callers.
- Separates the aggregate read concerns of the redesigned page from the per-credential write concerns of the underlying CRUD.
- Makes the new router's surface area visible in one file (~12 endpoints), which aids review and audit.

This decision is a refactor of the code organisation; it does not change the spec contract (which only specifies URL paths and response shapes, not which Python file owns the handler).

## Migration Strategy

1. **Hard cut, no flag.** Per brief §1 and the precedent set by `redesign-settings-dispatch-console`, the existing `/secrets` page is replaced wholesale. There is no feature flag, no gradual rollout, no parallel `/secrets-v2` route. This matches the dashboard's read-mostly observability surface (`about/heart-and-soul/design-language.md:25-43`) and the single-owner deployment model.
2. **Migration order:**
   - Phase A (DB): probe-log table + feature-catalogue table + audit enum + test-state columns + audit index.
   - Phase B (backend reads): `/api/secrets/inventory`, per-credential reads, audit history, breaks-catalogue.
   - Phase C (backend writes + OAuth): mutations + OAuth generalisation + callback rewiring + `/api/audit-log?key=` filter.
   - Phase D (frontend): passport-book components, spine, page, drawers, deep-link routing, identity switcher.
   - Phase E (cleanup): delete `SecretsPage.tsx`, `SecretsTable`, the six bespoke Setup cards, `CLIAuthCard`, embedded `EntityPicker`.
3. **Backward compatibility:**
   - Existing `/api/butlers/{name}/secrets/*` CRUD endpoints continue to function (preserved by `dashboard-api §Generic Butler Secrets CRUD (compatibility)` requirement).
   - Existing `/api/oauth/google/*` endpoints continue to function (preserved as `provider=google` under the generalised path).
   - Old audit rows with non-canonical `target` formats are queryable via the unmodified `?since=/?actor=/?action=` filters; the new `?key=` filter applies normalisation only.

## Risks

| Risk | Mitigation |
|----|----|
| OAuth callback `page_of_origin` collision with `/ingestion/connectors` redesign | This change scopes its callback behaviour explicitly to `secrets`-originated flows. The `complete-ingestion-redesign-parity` change owns the `ingestion`-originated flow. Both changes must agree on the state-token schema (single shared field name `page_of_origin`). Coordinated via the cross-change dependency in bead generation. |
| Test-state column write contention | Probe writes touch one row per probe; the cache is per-credential. No global locks; no contention. |
| Provider-feature catalogue drift | UPSERT-on-startup is idempotent; if a butler is removed from the roster, its rows are NOT auto-deleted. This is acceptable: stale entries surface as `WhatBreaks` rows pointing at unused features, which is a soft-deprecate signal and not a correctness bug. |
| Fingerprint as side-channel | Mitigated by on-read computation + truncation to 8 hex (32 bits of leakage; not enough for offline brute-force against any non-trivial secret). |
| Cross-page state staleness | Both `/secrets` and `/ingestion/connectors` rely on the same `last_verified` cache + the probe-log LRU. TanStack Query refresh intervals must be aligned between the two pages; coordinated via the same `complete-ingestion-redesign-parity` change. |
| Spec-vs-bundle drift if VISION.md is amended later | The spec deltas cite specific brief sections and quote intent verbatim where binding. If VISION.md is amended, the spec deltas need to be re-validated against the new VISION. |
