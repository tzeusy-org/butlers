## Why

The dashboard's connector-detail page exposes a `ReauthCallout` for OAuth-bound
connectors (Spotify, Gmail, Google Calendar, Google Drive, Google Health,
Discord, etc.) and a `ScopeList` that renders per-scope status with serif notes
explaining drift. The existing ingestion connector surface exposes
`POST /api/ingestion/connectors/:type/:identity/reauth`, but that endpoint is
**deliberately bricked with HTTP 503 and no `Retry-After`** because the
underlying contract — what scopes a connector requires, what scopes are
currently granted, how drift is detected, how reauth is initiated, what audit
trail it leaves — does not yet exist as a spec.

See:

- Historical source of the HTTP 503 gate (context only) —
  `openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4,17,36-40`.
- Durable dashboard owner —
  `openspec/specs/dashboard-ingestion-dispatch-console/spec.md:331-454`, whose
  existing recovery resolver is extended by this change's delta.
- Archived Phase 4.6 reauth handoff —
  `openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/tasks.md:45`
  (tracked in beads as `bu-1f91v.11`).
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
    `{auth_url, state, expires_in}` for generic OAuth providers other than
    Spotify (the auth_url is the provider's authorization URL with the union
    of currently-granted and required scopes), `{error: "unsupported",
    reason: "..."}` for non-OAuth providers, and HTTP 409 when the connector
    is not in a state that warrants reauth. Spotify instead enters its
    connector-owned Passport recovery journey.
  - Reauth callback contract: generic OAuth callbacks update
    `observed_scopes` and append `connector.reauth.completed` on success or
    `connector.reauth.failed` on failure. Spotify continues directly from its
    content-blind Passport projection to the connector-owned PKCE flow. Its
    connector-owned callback stays outside the generic approval and audit
    sequence. Non-OAuth reauth is rejected before the Approvals module.
  - Approval + audit authority: Only generic OAuth reauth is Approvals-gated
    and emits the generic reauth audit sequence. Granting an additional
    `sensitive` scope (e.g. `gmail.modify`, `calendar` write) emits a distinct
    audit entry with extra context (`scope.elevated_grant` action) on top of
    that generic OAuth sequence.
  - Rotation: when a connector module's declared `required` scopes change
    (operator edit OR provider deprecation), the spec defines how existing
    connectors are flagged `rotation-needed` and how the operator initiates
    rotation via the declared provider-owned recovery journey.
  - Generic reauth state token contract: generic OAuth state tokens are
    CSRF-bound, single-use, and idempotent (rapid re-initiation revokes prior
    state tokens and returns fresh ones without stranding the connection in a
    half-authorized state). Spotify PKCE state stays connector-owned.

- **Spotify authority reconciliation** (spec-only, owner-approved):
  - Spotify connector PKCE is the only production Spotify authorization flow;
    `POST /api/connectors/spotify/oauth/start` and
    `GET /api/connectors/spotify/oauth/callback` are connector-owned.
  - Spotify access and refresh tokens are RFC 0006 Tier 2 credentials. The
    connector-owned callback stores them in `public.entity_info` on the owner
    entity, and connector/runtime reads resolve them through
    `resolve_owner_entity_info()`. `CredentialStore` remains authoritative only
    for the system-level Spotify OAuth app client ID; `connector_registry` may
    retain only derived connection and scope metadata.
  - `/secrets?focus=u:spotify` is a content-blind, connector-owned Passport
    projection, not an editable User credential row, credential mirror, or
    generic OAuth provider alias. It projects closed state from the connector
    without becoming the Tier 2 secret authority. Its fixed v1 capability
    evidence is `listening-history`.
  - The generic OAuth provider surface remains Google-only in production. The
    later serialized cleanup removes the Spotify registry, route,
    configuration, UI, documentation, and test exemplar; generalized OAuth
    tests retain a synthetic generalized-provider fixture only, with no
    compatibility alias, shim, or production registry entry for Spotify.

- **MODIFIED capability** `dashboard-ingestion-dispatch-console`:
  - Adds a durable reauth lifecycle-authority requirement to the existing
    canonical dashboard contract. Generic OAuth is Approvals-gated; Spotify is
    a connector-owned Passport-to-PKCE journey with RFC 0006 Tier 2 owner
    `entity_info` token authority; and non-OAuth connectors reject before
    Approvals.
  - The active delta targets an existing canonical spec, so archive applies it
    directly instead of relying on a missing lifecycle-ceremony target.

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
  retention is defined directly by the active carrier's Audit trail
  requirement.

- **NO new approval primitives.** Re-uses the existing `module-approvals`
  Approvals module only for generic OAuth reauth; Spotify stays direct and
  connector-owned, and non-OAuth reauth rejects before approval.

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
- `dashboard-ingestion-dispatch-console` — an active `## ADDED Requirements`
  delta extends the existing canonical recovery resolver with the durable
  generic-OAuth, Spotify, and non-OAuth authority split.

## Impact

- **Code (implementation, not in this change)**:
  - `src/butlers/api/routers/oauth.py` — retain generic Google provider
    behavior and remove the Spotify production registry, route, configuration,
    UI coupling, and test exemplar in the serialized cleanup lane. Generic
    provider tests use a synthetic fixture after that cleanup.
  - `src/butlers/api/routers/spotify.py` — retain connector-owned Spotify
    PKCE start/callback ownership; move identity-bound access and refresh token
    handling to owner `public.entity_info` via `resolve_owner_entity_info()`;
    keep the OAuth app client ID in `CredentialStore`; and add only derived
    scope or connection metadata where this capability requires it.
  - `src/butlers/api/routers/ingestion_events.py` — replace the HTTP 503 stub
    in the reauth handler with the contract defined here.
  - `src/butlers/migrations/versions/` — Alembic migration adding
    `observed_scopes TEXT[]`, `observed_scopes_fetched_at TIMESTAMPTZ`,
    `required_scopes_version SMALLINT`, `auth_status VARCHAR` columns to
    `public.connector_registry`.
  - `src/butlers/connectors/spotify/` — periodic re-introspection task;
    connector-owned Passport projection data remains content-blind.
  - `frontend/src/components/ingestion/ConnectorDetail.tsx` — wire `scopes[]`
    block; render `ReauthCallout` from `auth.status`; render `ScopeList` from
    `scopes[]` per-row `status` + `serif_note`.

- **APIs**:
  - `GET /api/ingestion/connectors/{type}/{identity}` gains a `scopes` block
    and an `auth.status` field. Additive — no existing field changes type.
  - `POST /api/ingestion/connectors/{type}/{identity}/reauth` (currently 503)
    becomes a working generic-OAuth endpoint per this spec; Spotify recovery
    remains the connector-owned Passport journey.
  - Generic Google OAuth callback behavior updates `observed_scopes` and emits
    the second audit entry. Spotify uses only
    `GET /api/connectors/spotify/oauth/callback` for its connector-owned
    callback and never a generic OAuth Spotify callback.

- **Database**: additive columns on `public.connector_registry`. No new tables.
  No data migration required (NULL `observed_scopes` is interpreted as "not yet
  probed").

- **Audit log**: generic OAuth reauth, and only generic OAuth reauth, emits
  `connector.reauth.submit`, `connector.reauth.approved`,
  `connector.reauth.denied`, `connector.reauth.completed`, and
  `connector.reauth.failed`. `connector.scope.observed`,
  `connector.scope.elevated_grant`, and `connector.scope.required_changed`
  retain their own specified OAuth-connector lifecycle. Spotify's direct
  connector-owned path does not require the generic submit or approval-
  resolution records, and non-OAuth reauth attempts reject before approval.
  These values reuse the existing audit-log infrastructure; no schema change.

- **Doctrine alignment**:
  - **Non-Negotiable Rule 1 (user-federated)**: scope surface is per-instance,
    per-owner. No multi-user state. (See `about/heart-and-soul/vision.md:60-63`.)
  - **Security model — credential lifetime**: scopes are NOT credentials. Scope
    strings (e.g. `gmail.readonly`, `user-read-recently-played`) are safe to
    surface. Refresh tokens and access tokens MUST NOT appear in any response
    body per the active carrier's response-shape requirements. See
    `about/heart-and-soul/security.md:96-147` for the credential authority
    model.
  - **v1 scope**: the dashboard's OAuth credential configuration surface is in
    v1 (per `about/heart-and-soul/v1.md:103-110`); this spec strengthens it
    rather than expanding scope.
  - **Non-Negotiable Rule 7 (transport is connector responsibility)**: scope
    introspection is a connector-side responsibility, not a butler-side one.
    The dashboard API reads from `connector_registry`; it does not call
    provider APIs directly. (See `about/heart-and-soul/vision.md:110-115`.)

- **Cross-change coordination**:
  - The historical lifecycle artifact is already archived and is not a live
    authority. This change's dashboard-ingestion delta targets the existing
    canonical spec, so its authority survives this change's own archive with
    no second-change ordering or no-op fallback.
  - Once this change ratifies, the reauth bead `bu-1f91v.11` has a durable
    contract to implement; this planning change does not change its tracker
    state or authorize source changes.
  - The owner-approved Spotify reconciliation is serialized and does not
    authorize source changes in this spec-only amendment: merge this carrier's
    canonical reconciliation first; then `bu-fj7lx` implements the
    content-blind connector-owned Passport projection; only then `bu-3ifcj`
    removes the generic OAuth Spotify production exemplar and repository
    cruft. The cleanup keeps a synthetic generalized-provider fixture and no
    compatibility alias, shim, or production registry entry.

- **Tests (implementation, not in this change)**:
  - Drift detection unit tests per drift class (`ok`, `extra`, `drift`,
    `expired`, `unsupported`).
  - Reauth state token round-trip including replay protection.
  - Cross-connector applicability matrix: smoke test asserting each connector
    type returns a defined `auth.status` (not `null`, not `undefined`).
  - Sensitive-scope grant audit trail.
  - Rotation scenario (operator bumps `required_scopes_version`; existing
    connectors flip to `rotation-needed`).
  - Spotify authority regression: project RFC 0006 Tier 2 into the active and
    canonical requirements; assert the Passport projection exposes only its
    closed connection state and `listening-history` capability evidence;
    assert access and refresh tokens remain owner-`entity_info`-only via
    `resolve_owner_entity_info()`; and assert the generic OAuth suite uses a
    synthetic generalized-provider fixture rather than Spotify.

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) —
  `about/heart-and-soul/vision.md:60-63`
- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md:110-115`
- Security model — credential authority tiers and credential masking —
  `about/heart-and-soul/security.md:96-147`
- Binding Tier 2 connector credential rule —
  `about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md#credential-store--three-tier-authority-model`
- v1 scope — dashboard OAuth credential configuration is in v1 —
  `about/heart-and-soul/v1.md:103-110`
- Historical HTTP 503 gate (context only) —
  `openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4,17,36-40`
- Durable dashboard lifecycle target —
  `openspec/specs/dashboard-ingestion-dispatch-console/spec.md:331-454`
- Tracked implementation bead — `bu-1f91v.11`
  (`openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/tasks.md:45`)
- UI ground truth: `ReauthCallout` —
  `docs/redesigns/ingestion-connector-detail.jsx:70-101`
- UI ground truth: `ScopeList` —
  `docs/redesigns/ingestion-connector-detail.jsx:216-245`
- Spotify fixture (auth.status, scopes shape) —
  `docs/redesigns/ingestion-connectors-data.jsx:97-121`
- Design language: serif italic note / mono scope label / state colors —
  `openspec/specs/dashboard-design-language/spec.md` (Voice Surface; Kind Tags; State Color Discipline)
- Existing Google OAuth scope plumbing being extended —
  `openspec/specs/google-multi-account-oauth/spec.md:84-145`
- Existing `granted_scopes` precedent on `public.google_accounts` —
  `openspec/specs/google-account-registry/spec.md:22,150-162`
- Reference token introspection implementation —
  `src/butlers/api/routers/oauth.py:164,1547-1620`
- Spotify connector OAuth scope declaration —
  `openspec/specs/connector-spotify/spec.md:229-247`
- Spotify dashboard's existing `needs_reauth` pattern —
  `openspec/specs/dashboard-spotify-setup/spec.md:86-102`
- Spotify connector-authority and Passport-projection reconciliation —
  `openspec/specs/connector-spotify/spec.md`,
  `openspec/specs/butler-secrets/spec.md`, `bu-fj7lx`, and `bu-3ifcj`
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
