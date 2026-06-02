# redesign-secrets-passport

## Why

The current `/secrets` page is a 3-tab shell (System / User / CLI runtimes) wrapping a flat `SecretsTable` of `••••••••` rows with a per-row eye-toggle reveal. Two specific pains, both verified against current code (`frontend/src/pages/SecretsPage.tsx:1-529`):

1. **Opaque without leaking.** To know whether a credential is *the right one*, the owner has to reveal it. There is no fingerprint, no last-verified timestamp, no scope inventory, no provider-side state — the eye-toggle is the only diagnostic, and it is binary.
2. **Flat rhythm.** A silently-expired Google OAuth and a healthy Telegram token render at identical weight. Severity has no visual privilege; sick credentials hide in plain sight.

Compounding both: the User tab stacks six bespoke provider Setup cards (Google, Spotify, Home Assistant, WhatsApp, OwnTracks, Steam) in a single `<Card>`, each with divergent chrome — so the page has no visual rhythm even on a healthy day.

The redesign assets in `pr/overview/secrets-redesign/` (passport-book design, prototype, HANDOFF, prompts/00–05) propose a single passport-book IA with evidence-over-value affordances. The integration brief at `docs/redesigns/2026-05-25-secrets-brief.md` is the **binding** input for this change; Section 0 of that brief is binding design intent and Section 3 is the binding backend contract delta. This change formalises both into specs and operational requirements.

This change introduces a new capability `butler-secrets` (the operational `/secrets` page) and extends `dashboard-api` (envelope + endpoint surface) and `core-credentials` (test-state columns on existing tables, no storage migration). It does **not** modify the three-tier authority model declared in `about/heart-and-soul/security.md`.

## What Changes

- **BREAKING (UI):** hard cut of `frontend/src/pages/SecretsPage.tsx` (3-tab shell + `SecretsTable` + six bespoke provider Setup cards + `CLIAuthCard` + embedded `EntityPicker`). Replaced by a single passport-book route at `/secrets` (left spine index + right page editorial, no prototype tweaks chrome) per `pr/overview/secrets-redesign/DESIGN_LANGUAGE.md`.
- **NEW capability `butler-secrets`:** declares the `/secrets` page contract — passport-book IA, evidence-over-value affordance contract (fingerprint + last-verified + scope inventory + probe result + provider-side state + WhatBreaks), projection-lens identity-switcher semantics, OAuth per-provider unification, probe history, audit deep-link, and the binding "no LLM narration on `/secrets` surfaces" invariant.
- **MODIFIED `dashboard-api`:** adds the `/api/secrets/*` namespace (inventory, per-credential reads, mutations, probe, audit history, breaks catalogue), generalises `/api/oauth/<provider>/*` from Google-only to multi-provider, and extends `/api/audit-log` with a `?key=<credential-key>` filter. All new endpoints conform to the existing `ApiResponse<T>`/`PaginatedResponse<T>` envelope contract (RFC 0007 §Response Envelope).
- **MODIFIED `core-credentials`:** extends `butler_secrets` (all per-butler schemas) and `public.entity_info` with four test-state columns (`last_verified`, `last_test_ok`, `last_test_code`, `last_test_message`) for probe-result caching. **No** storage migration of the secret values themselves — `butler_secrets` remains the system store, `entity_info` remains the user-credential store on the owner entity.
- **NEW tables (cross-butler, `public` schema):**
  - `public.secret_probe_log` — LRU probe-result store; one row per probe call, indexed for fast "last N for key" queries; ≥ 90 day live retention.
  - `public.provider_feature_catalogue` — server-side catalogue of `(provider, butler, feature, severity, required_scopes)` rows powering the WhatBreaks affordance; bootstrapped via Alembic seed and UPSERTable by each butler at startup. (Resolves the Phase C "Option B" decision in brief §5 Q8.)
- **MODIFIED audit log:** adds new audit action enum values `verified`, `failed`, `rotated`, `connected`, `disconnected`, `warned`, `overrode`, `attempted`, `set` for credential lifecycle events; adds `(target, recorded_at DESC)` index on `public.audit_log` to support `/api/audit-log?key=<key>` filtering. (Reuses the existing audit primitive shipped by `redesign-settings-dispatch-console`; no new audit storage.)
- **NEW binding invariant:** the `/secrets` surfaces (spine, page, drawers) MUST NOT trigger LLM inference. All voice paragraphs, scope explanations, audit notes, WhatBreaks rows, and probe-result messages are either (a) stored prose from a catalogue, (b) templated strings interpolating server data, or (c) verbatim provider error tails. This is a doctrine guardrail that future beads proposing LLM-elaborated "smart explanations" on `/secrets` must be rejected against. Captured in brief §0 ("What we are deliberately NOT doing") and brief §4 ("Recommended de-scopes").
- **Identity-switcher semantics (binding for v1):** the `?identity=<id>` URL state on `/secrets` is a **projection lens** over the owner's view of household-member credential data — *not* an authentication boundary. Backend MUST NOT enforce identity-scoped access; every mutation runs with owner privilege regardless of switcher state. This matches existing single-owner doctrine (`about/heart-and-soul/security.md:7-8, 18-20`). A future RFC under `about/legends-and-lore/` may introduce a household-member privilege tier; this change is forward-compatible because the same `?identity=<id>` URL state will then bind to a session principal rather than to a projection lens.
- **No storage migration.** System secrets remain in `butler_secrets`. User secrets remain on `entity_info`. CLI runtime tokens remain in their existing store. The redesign is presentational plus a small number of read-side endpoints, new tables for probe-log/catalogue, and column extensions for test-state caching.

## Impact

- **Affected specs:**
  - `butler-secrets` (NEW capability) — full spec for the passport-book `/secrets` surface and its operational contract.
  - `dashboard-api` (MODIFIED) — `/api/secrets/*` namespace added; `/api/oauth/*` generalised; `/api/audit-log?key=` filter added; envelope contract extended to these endpoints.
  - `core-credentials` (MODIFIED) — `butler_secrets` and `public.entity_info` gain test-state columns; new tables `public.secret_probe_log` and `public.provider_feature_catalogue`; audit action enum extended.
- **Affected code:**
  - `frontend/src/pages/SecretsPage.tsx` — fully replaced.
  - `frontend/src/components/settings/` — six bespoke provider Setup cards deleted; replaced by one row template + per-provider drawer pattern.
  - `frontend/src/components/CLIAuthCard.tsx` — replaced by `PageCli`.
  - `src/butlers/api/routers/secrets.py` — extended OR replaced by `secrets_v2.py` (router-naming decision deferred to design.md per brief §5 Q13).
  - `src/butlers/api/routers/oauth.py` — generalised from Google-only to `<provider>` path.
  - `src/butlers/api/routers/audit.py` — `?key=<key>` query param added.
  - New Alembic migrations under `src/butlers/migrations/versions/` for the two new public tables, the four test-state columns, the audit action enum values, and the new audit index.
- **Affected doctrine:** none. The change is consistent with `about/heart-and-soul/security.md` (three-tier authority model preserved), `about/heart-and-soul/vision.md` (single owner, sovereignty), and `about/heart-and-soul/design-language.md` (read-mostly observability surface). No manifesto requires amendment (brief §4 verified all 11 touched butlers as `identity preserved`).
- **No new dependencies, models, or LLM costs.** Brief §4 LLM-cost feasibility table shows every `/secrets` affordance as `green` (zero LLM inference); the only provider-side cost is the optional 1-token completion for LLM-key probes (~$0.0003/user/day at 5 probes).
- **Cross-page reauth bookkeeping:** the OAuth callback contract is extended to carry a `page_of_origin` parameter so an OAuth dance initiated from `/ingestion/connectors` returns there, and one initiated from `/secrets` returns to `/secrets?focus=u:<provider>&toast=connected`. This is co-owned with the in-flight `complete-ingestion-redesign-parity` change; this proposal explicitly delegates the `/ingestion/connectors` side to that change and only specifies the `/secrets` side here.
