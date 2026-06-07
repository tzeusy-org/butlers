# Design — google-health-secrets-surface

## Context

This change is a **spec-only amendment** — no implementation code changes land in this change. The grounding evidence is:

1. `openspec/specs/dashboard-google-accounts/spec.md` — already mandates the per-account scope-set picker (§Requirement: Per-Account Scope Set Picker) and Google Health status card (§Requirement: Google Health Connector Status Card), but omits any route binding. The picker and card exist "in the settings page" with no spec anchor.

2. `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md` — defines the `/secrets` passport IA, the `?focus=u:<provider>` deep-link protocol (§Requirement: Deep-Link Focus Routing), and the `?identity=<id>` projection-lens switcher (§Requirement: Projection-Lens Identity Switcher). The `PageGoogleAccounts` component (rendered at `?focus=u:google`) is the natural home for the picker and card, but the spec does not require the owner-default inventory to surface any Google account entity.

3. `src/butlers/api/routers/secrets_v2.py:701-721` — the `_fetch_user_secrets` owner-default branch joins `public.entity_info` through `public.entities WHERE 'owner' = ANY(roles)`. The `{owner}` entity carries no `google_oauth_refresh` entry; those live on `{google_account}` companion entities (`roles = ['google_account']`). Result: the owner-default inventory returns zero user credentials for the `u:google` focus key.

4. `openspec/specs/google-account-registry/spec.md:71-89` — companion entity excluded from identity resolution (`entity_resolve()`) by design; BUT the secrets inventory is NOT `entity_resolve()`; it is a direct `entity_info` join. The exclusion from entity resolution does not imply exclusion from the secrets surface.

5. `openspec/changes/add-connector-oauth-scope-surface` — owns the systemic `auth_status` enum and reauth endpoint contract. This change explicitly defers to it.

## Goals / Non-Goals

**Goals:**

1. Add a normative route-binding statement to `dashboard-google-accounts/spec.md`: the scope-set picker and Google Health status card MUST render inside `PageGoogleAccounts` at `/secrets?focus=u:google`, not a standalone settings page.
2. Add a `Scenario: multi-account leak prevention` to `dashboard-google-accounts/spec.md`: the owner-default `/secrets` projection SHALL surface ONLY the primary Google account's credential; non-primary accounts SHALL appear only under explicit `?identity=<entity>`.
3. Amend `redesign-secrets-passport/specs/butler-secrets/spec.md` with a new requirement: the owner-default inventory SHALL include the primary Google account's `google_oauth_refresh` entity_info entry, making `?focus=u:google` reachable without a manual `?identity=` parameter.

**Non-Goals:**

1. Do NOT re-spec the `auth_status` enum (`ok | degraded | expired | rotation-needed`). That is owned by `add-connector-oauth-scope-surface`.
2. Do NOT specify the reauth endpoint or durable reauth CTA. Owned by `add-connector-oauth-scope-surface`.
3. Do NOT specify the backend join implementation detail — that is owned by implementation bead `bu-2kejb`. This change only specifies the behavioral outcome.
4. Do NOT touch multi-account poll logic (already specified in `connector-google-health-multi-account`).
5. Do NOT add new DB tables, endpoints, or migrations. This is pure spec.

## Decisions

### D1: Amend `dashboard-google-accounts` in-place vs. a new spec

**Decision:** Amend `dashboard-google-accounts/spec.md` in-place (this change's `specs/dashboard-google-accounts/` delta).

**Rationale:** The picker and card are already specified there. A new spec would split the specification of the same UI surface across two files, making future maintainers hunt for the route binding. The amendment is additive (one new normative paragraph + one new scenario), not a rewrite.

**Alternative considered:** New `google-health-passport-surface` spec. Rejected — it would duplicate intent already present in `dashboard-google-accounts` and create a coordination burden between two specs for the same component.

### D2: Amend `butler-secrets` inside the existing `redesign-secrets-passport` change delta vs. a standalone amendment

**Decision:** Produce the `butler-secrets` delta as `specs/butler-secrets/spec.md` inside THIS change (`google-health-secrets-surface`), following the OpenSpec convention for `## ADDED Requirements` / `## MODIFIED Requirements` blocks that layer on existing specs.

**Rationale:** The `redesign-secrets-passport` change is actively being implemented (3/78 tasks done). Editing its spec file directly would contaminate the in-progress change's review history. Placing the delta here keeps the audit trail clean and mirrors how `add-connector-oauth-scope-surface` handles its modification of `connector-lifecycle-ceremony`.

**Alternative considered:** Edit `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md` directly. Rejected — the active change's spec files are the in-flight source of truth; cross-change edits bypass the proposal/review gate.

### D3: Which entity anchors the Google account in the owner-default inventory?

**Decision:** The primary Google account's credential is included in the owner-default inventory when `is_primary = true AND status != 'revoked'` — this includes both `active` and `expired` accounts so that the owner can reach the reauth CTA from the owner-default view. The spec mandates the behavioral outcome (primary account's `google_oauth_refresh` appears in owner-default inventory); the backend join strategy is an implementation detail left to bead `bu-2kejb`.

**Rationale:** The spec should be implementation-neutral. Whether the backend adds a LEFT JOIN to `google_accounts` or materializes the credential into an `{owner}` entity_info row is a backend concern. Mandating the join strategy in the spec would over-specify and constrain the implementer.

### D4: Leak prevention — spec precision

**Decision:** The leak-prevention scenario uses the word "SHALL" (normative) and identifies non-primary accounts by the runtime characteristic `is_primary = false` (not by email or hard-coded identity). This makes the constraint portable across account configurations.

**Rationale:** Hard-coding `tzeuse@` into the spec would make the scenario instance-specific and non-generalizable. The constraint is a security invariant that must hold for any multi-account setup.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| `add-connector-oauth-scope-surface` `auth_status` enum changes field names surfaced in `PageGoogleAccounts` | Cross-link is explicit in both proposals. When `add-connector-oauth-scope-surface` archives, implementers of `bu-2kejb` and the frontend bead MUST verify field name alignment. The cross-link in this spec's Source References makes the dependency discoverable. |
| `redesign-secrets-passport` archives before this change is approved, merging its `butler-secrets` delta into `openspec/specs/butler-secrets/spec.md` | If that happens, this change's `butler-secrets` delta targets a spec that now lives at `openspec/specs/butler-secrets/spec.md` rather than the change-local path. The delta content remains valid; only the application target changes. The archive process must reconcile. |
| Owner-default inventory including a Google account credential surprises existing callers that assume only `{owner}` credentials appear | The `butler-secrets` spec already states that identity switcher identity ≠ auth boundary (§Projection-Lens Identity Switcher). The new requirement is additive and does not break existing call sites. However, any FE component that renders the owner-default inventory without a `?focus=` filter will now show a `u:google` row — this is the intended behavior and is not a regression. |

## Open Questions

| ID | Question | Status |
|---|---|---|
| OQ1 | Should `PageGoogleAccounts` at `?focus=u:google` also render non-primary accounts as disabled/dimmed rows (showing their existence without exposing tokens)? | **Deferred to implementation (`bu-2kejb`)** — the spec mandates leak prevention (non-primary tokens hidden from owner-default); whether the UI shows a dimmed row or silently omits it is a UX decision for the frontend bead. |
| OQ2 | When `add-connector-oauth-scope-surface` ships its `auth_status` enum, does `PageGoogleAccounts` need a spec update to wire `auth_status` into the Health card rendering? | **Deferred** — that change's proposal already cross-links here for harmonization. The implementation bead for `add-connector-oauth-scope-surface` will own the wiring spec update if needed. |
