## Why

The dashboard's connector-detail page exposes a `ReauthCallout` for OAuth-bound
connectors (Spotify, Gmail, Google Calendar, Google Drive, Google Health,
Discord, etc.) and a `ScopeList` that renders per-scope status with serif notes
explaining drift. The sibling change `redesign-ingestion-dispatch-console`
introduces `POST /api/ingestion/connectors/:type/:identity/reauth`, but that
endpoint is **deliberately bricked with HTTP 503 and no `Retry-After`** because
the underlying contract — what scopes a connector requires, what scopes are
currently granted, how drift is detected, how reauth is initiated, what audit
trail it leaves — does not yet exist as a spec.

See:

- `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4` —
  "The `reauth` action additionally depends on a future
  `connector-oauth-scope-surface` capability and is blocked until that spec
  exists."
- `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:17` —
  gate matrix entry: "`reauth` — Approvals-gated; BLOCKED with HTTP 503 until
  `connector-oauth-scope-surface` spec exists."
- `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:36-40` —
  Scenario "Reauth is blocked".
- `openspec/changes/redesign-ingestion-dispatch-console/tasks.md:45` — Phase 4.6
  reauth bead (tracked in beads as `bu-1f91v.11`) is BLOCKED on this spec.
- `docs/redesigns/ingestion-connector-detail.jsx:70-101,216-245` —
  binding UI ground truth for `ReauthCallout` and `ScopeList`.
- `docs/redesigns/ingestion-connectors-data.jsx:97-121` — Spotify
  fixture demonstrating `auth.status: "needs_reauth"`, `scope drift` notes, and
  `scopes` array shape that the `GET /api/ingestion/connectors/{type}/{identity}`
  response must populate.

Today the live dashboard has nowhere to land scope drift signals, no contract
for what "needs reauth" actually means across connectors, no defined behavior
when a connector adopts new required scopes, and no audit trail for scope
rotation events. The 503 brick will sit there until this capability ships.

This change authors the missing contract. It does not implement it. Implementation
is a follow-up bead under epic `bu-1f91v` that unblocks `bu-1f91v.11`.

## What Changes

- **NEW capability** `connector-oauth-scope-surface`:
  - Per-connector declaration of `required`, `optional`, and `sensitive` OAuth
    scopes, sourced from the connector module's manifest (the existing
    `OAUTH_SCOPE_SETS` registry referenced by `google-multi-account-oauth`
    extended to non-Google providers).
  - Versioning rule: required scope sets evolve forward-only; older granted
    sets are detected as drift, never silently re-baselined.
  - Granted-scope observation: for OAuth providers that support
    introspection (Google `tokeninfo`, Spotify `scope` echoed on refresh,
    Discord identify endpoint), connectors record observed scopes on
    `connector_registry.observed_scopes` (TEXT[]) with
    `observed_scopes_fetched_at` freshness timestamp; for providers that do
    not (Telegram bot/user-client, OwnTracks, Home Assistant token auth), the
    spec defines the alternative surface (session-validity / token-validity).
  - Drift taxonomy: `ok` (granted ⊇ required), `extra` (granted has scopes
    beyond required — audit only, not drift), `drift` (granted ⊋ required, at
    least one required scope missing), `expired` (provider rejected the token
    entirely), `unsupported` (non-OAuth connector).
  - `auth.status` enum on connector aggregates: `ok | degraded | expired |
    rotation-needed | unsupported | unconfigured`.
  - UI surface contract: `scopes[]` block on the connector-detail API response
    with per-scope `status`, `granted_at?`, `required_since?`, `serif_note?`
    (binding to `ScopeList` in the bundle), plus the `auth.status` field that
    drives `ReauthCallout` rendering.
  - Reauth endpoint contract:
    `POST /api/ingestion/connectors/:type/:identity/reauth` returns
    `{auth_url, state, expires_in}` for OAuth providers (the auth_url is the
    provider's authorization URL with the union of currently-granted and
    required scopes), `{error: "unsupported", reason: "..."}` for non-OAuth
    providers, and HTTP 409 when the connector is not in a state that warrants
    reauth.
  - Reauth callback contract: the existing OAuth callback handlers update
    `observed_scopes` and append a second audit entry (`connector.reauth.completed`)
    on success or `connector.reauth.failed` on failure.
  - Approvals gating: reauth initiation is Approvals-gated (consistent with
    `connector-lifecycle-ceremony`). Granting an additional `sensitive` scope
    (e.g. `gmail.modify`, `calendar` write) emits a distinct audit entry with
    extra context (`scope.elevated_grant` action) on top of the standard reauth
    audit pair.
  - Rotation: when a connector module's declared `required` scopes change
    (operator edit OR provider deprecation), the spec defines how existing
    connectors are flagged `rotation-needed` and how the operator initiates
    rotation via the same reauth endpoint.
  - State token contract: reauth state tokens are CSRF-bound, single-use, and
    idempotent (rapid re-initiation revokes prior state tokens and returns
    fresh ones without stranding the connection in a half-authorized state).

- **MODIFIED capability** `connector-lifecycle-ceremony` (spec lives in the
  sibling change `redesign-ingestion-dispatch-console`):
  - Removes the blocking scenario "Reauth is blocked" once both changes are
    archived. The lifecycle ceremony spec's reauth row in the gate matrix is
    updated to read: "Approvals-gated; delegates to
    `connector-oauth-scope-surface/spec` for behavior contract".
  - This modification is documented as a `## MODIFIED Requirements` block in
    this change's spec delta. The owning change (`redesign-ingestion-dispatch-console`)
    is not edited directly per the constraints.

- **MODIFIED capability** `connector-base-spec`:
  - Adds the `observed_scopes`, `observed_scopes_fetched_at`,
    `required_scopes_version`, and `auth_status` fields to the
    `connector_registry` row and to the `ConnectorDetail` Pydantic response
    model.
  - Adds the requirement that connectors with OAuth credential type SHALL
    refresh `observed_scopes` opportunistically on every token refresh and
    SHALL re-introspect on a configurable cadence (default 6h) so drift is
    visible without operator action.

- **NO new database tables.** The contract leans entirely on additive columns
  to `public.connector_registry` and re-uses the existing `public.audit_log`
  for the audit trail. No separate `scope_history` table is needed; audit log
  retention (indefinite, per `connector-lifecycle-ceremony`) covers it.

- **NO new approval primitives.** Re-uses the existing `module-approvals`
  Approvals module per `connector-lifecycle-ceremony`.

## Capabilities

### New Capabilities

- `connector-oauth-scope-surface` — declared scopes, observed scopes, drift
  taxonomy, `auth.status` enum, scope rotation gating, reauth flow contract,
  per-connector applicability (OAuth vs. non-OAuth). Powers the
  `ReauthCallout` and `ScopeList` UI components from the redesign bundle and
  unblocks `POST /api/ingestion/connectors/:type/:identity/reauth`.

### Modified Capabilities

- `connector-base-spec` — additive columns on `connector_registry` and
  additive fields on `ConnectorDetail` Pydantic response. No behavior of the
  base spec changes.
- `connector-lifecycle-ceremony` — the "Reauth is blocked" scenario is
  superseded; the gate matrix entry's blocker note is removed. This delta is
  encoded in this change's spec via a `## MODIFIED Requirements` block per
  OpenSpec convention.

## Impact

- **Code (implementation, not in this change)**:
  - `src/butlers/api/routers/oauth.py` — extend `_probe_google_token`-style
    introspection to per-connector providers; add Spotify, Discord introspection
    paths; add the `scopes[]` block to `ConnectorDetail` responses.
  - `src/butlers/api/routers/ingestion_events.py` — replace the HTTP 503 stub
    in the reauth handler with the contract defined here.
  - `src/butlers/migrations/versions/` — Alembic migration adding
    `observed_scopes TEXT[]`, `observed_scopes_fetched_at TIMESTAMPTZ`,
    `required_scopes_version SMALLINT`, `auth_status VARCHAR` columns to
    `public.connector_registry`.
  - `src/butlers/connectors/spotify/` — periodic re-introspection task.
  - `frontend/src/components/ingestion/ConnectorDetail.tsx` — wire `scopes[]`
    block; render `ReauthCallout` from `auth.status`; render `ScopeList` from
    `scopes[]` per-row `status` + `serif_note`.

- **APIs**:
  - `GET /api/ingestion/connectors/{type}/{identity}` gains a `scopes` block
    and an `auth.status` field. Additive — no existing field changes type.
  - `POST /api/ingestion/connectors/{type}/{identity}/reauth` (currently 503)
    becomes a working endpoint per this spec.
  - `GET /api/oauth/{provider}/reauth/callback` is extended to update
    `observed_scopes` and emit the second audit entry.

- **Database**: additive columns on `public.connector_registry`. No new tables.
  No data migration required (NULL `observed_scopes` is interpreted as "not yet
  probed").

- **Audit log**: new `action` values: `connector.reauth.submit`,
  `connector.reauth.approved`, `connector.reauth.denied`,
  `connector.reauth.completed`, `connector.reauth.failed`,
  `connector.scope.observed`, `connector.scope.elevated_grant`,
  `connector.scope.required_changed`. These reuse the existing audit-log
  infrastructure; no schema change.

- **Doctrine alignment**:
  - **Non-Negotiable Rule 1 (user-federated)**: scope surface is per-instance,
    per-owner. No multi-user state. (See `about/heart-and-soul/vision.md:60-63`.)
  - **Security model — credential lifetime**: scopes are NOT credentials. Scope
    strings (e.g. `gmail.readonly`, `user-read-recently-played`) are safe to
    surface. Refresh tokens and access tokens MUST NOT appear in any response
    body per `connector-lifecycle-ceremony` spec.md:103-109. (See
    `about/heart-and-soul/security.md:96-147` for the credential authority
    model.)
  - **v1 scope**: the dashboard's OAuth credential configuration surface is in
    v1 (per `about/heart-and-soul/v1.md:103-110`); this spec strengthens it
    rather than expanding scope.
  - **Non-Negotiable Rule 7 (transport is connector responsibility)**: scope
    introspection is a connector-side responsibility, not a butler-side one.
    The dashboard API reads from `connector_registry`; it does not call
    provider APIs directly. (See `about/heart-and-soul/vision.md:110-115`.)

- **Cross-change coordination**:
  - This change is **independent** of `redesign-ingestion-dispatch-console`
    and can land in either order. If this lands first, the reauth bead
    `bu-1f91v.11` can immediately move from "blocked" to "ready" once the
    redesign Wave-3 prerequisites are met. If the redesign lands first, the
    503 brick remains in production until this lands; that is the explicit
    contract per `connector-lifecycle-ceremony` and is by design.
  - The MODIFIED requirement on `connector-lifecycle-ceremony` (removing the
    "Reauth is blocked" scenario) is encoded here as a delta but only takes
    effect when BOTH changes are archived. If this change archives first, the
    `connector-lifecycle-ceremony` capability does not yet exist in
    `openspec/specs/`; the archive process will merge this delta when
    `redesign-ingestion-dispatch-console` later archives. If the redesign
    archives first, this change's delta applies cleanly on archive.

- **Tests (implementation, not in this change)**:
  - Drift detection unit tests per drift class (`ok`, `extra`, `drift`,
    `expired`, `unsupported`).
  - Reauth state token round-trip including replay protection.
  - Cross-connector applicability matrix: smoke test asserting each connector
    type returns a defined `auth.status` (not `null`, not `undefined`).
  - Sensitive-scope grant audit trail.
  - Rotation scenario (operator bumps `required_scopes_version`; existing
    connectors flip to `rotation-needed`).

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) —
  `about/heart-and-soul/vision.md:60-63`
- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md:110-115`
- Security model — credential authority tiers and credential masking —
  `about/heart-and-soul/security.md:96-147`
- v1 scope — dashboard OAuth credential configuration is in v1 —
  `about/heart-and-soul/v1.md:103-110`
- Blocking dependency declaration —
  `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4,17,36-40`
- Tracked implementation bead — `bu-1f91v.11`
  (`openspec/changes/redesign-ingestion-dispatch-console/tasks.md:45`)
- UI ground truth: `ReauthCallout` —
  `docs/redesigns/ingestion-connector-detail.jsx:70-101`
- UI ground truth: `ScopeList` —
  `docs/redesigns/ingestion-connector-detail.jsx:216-245`
- Spotify fixture (auth.status, scopes shape) —
  `docs/redesigns/ingestion-connectors-data.jsx:97-121`
- Design language: serif italic note / mono scope label / state colors —
  `docs/redesigns/ingestion-design-language.md:30,108-140,217-232`
- Existing Google OAuth scope plumbing being extended —
  `openspec/specs/google-multi-account-oauth/spec.md:84-145`
- Existing `granted_scopes` precedent on `public.google_accounts` —
  `openspec/specs/google-account-registry/spec.md:22,150-162`
- Reference token introspection implementation —
  `src/butlers/api/routers/oauth.py:164,1547-1620`
- Spotify connector OAuth scope declaration —
  `openspec/specs/connector-spotify/spec.md:229-247`
- Spotify dashboard's existing `needs_reauth` pattern —
  `openspec/specs/dashboard-spotify-setup/spec.md:84-99`
- Connector base spec (extension target) —
  `openspec/specs/connector-base-spec/spec.md:381-419`
- Credential masking contract (must not contradict) —
  `openspec/specs/core-credentials/spec.md:52-99,200-223`
- Non-OAuth connectors (must degrade gracefully) —
  `openspec/specs/connector-telegram-bot/spec.md:154-159`,
  `openspec/specs/connector-telegram-user-client/spec.md:111-137`,
  `openspec/specs/connector-owntracks/spec.md:47-105`
- Approvals dependency — `openspec/specs/module-approvals/spec.md`
- OpenSpec config rule on Source References footer —
  `openspec/config.yaml:9-15`
