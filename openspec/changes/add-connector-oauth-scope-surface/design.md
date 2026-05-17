## Context

OAuth-bound connectors in Butlers — Spotify, Gmail, Google Calendar, Google
Drive, Google Health, Discord (planned) — share a recurring failure mode that
none of the existing specs address coherently: the provider rotates a scope
name (Spotify, May 2026), revokes a grant (token expiry past refresh window),
or the connector author bumps the required scope set (e.g. Gmail moves from
`gmail.readonly` to `gmail.modify` for an outbound action). Today the
downstream symptom is a quiet 401 stream in the connector's logs and a stale
dashboard. The operator finds out when QA opens an investigation or when a
butler stops receiving the expected events.

The redesign bundle in `pr/overview/ingestion-redesign/` proposes a first-class
UI for this failure mode: a `ReauthCallout` band at the top of the
connector-detail page (red, mono-uppercase "REAUTH REQUIRED" eyebrow, serif
explanation, "re-authorize" pill button) and a `ScopeList` in the right column
that renders each scope with a status dot, a mono label, a status word
(`granted | mismatch | extra | missing | sensitive`), and an italic serif
"how to fix this" note below.

The bundle's Spotify fixture shows exactly the data the API must serve:

```js
{
  id: 'spotify',
  auth: { status: 'needs_reauth', note: 'scope `user-read-recently-played`
          rotated upstream', expires: 'now' },
  scopes: ['user-read-recently-played', 'user-read-playback-state', 'user-top-read'],
  // ...
}
```

— `pr/overview/ingestion-redesign/ingestion-connectors-data.jsx:97-121`

And the binding component contract (`ScopeList`):

```jsx
{c.scopes.map((s, i) => {
  const isBroken = s === 'user-read-recently-played';
  // dot color: red if broken, green if ok
  // label color: red if broken, fg if ok
  // status word: 'mismatch' if broken, 'granted' if ok
})}
// Below the list:
//   "Reauthorising will request the rotated scope name and resume the poll."
//   — italic serif, fg-muted
```

— `pr/overview/ingestion-redesign/ingestion-connector-detail.jsx:222-244`

The existing specs only cover this for Google specifically
(`google-multi-account-oauth/spec.md:84-120`, `google-account-registry/spec.md:150-162`)
and for Spotify within a narrow dashboard-setup card
(`dashboard-spotify-setup/spec.md:84-99`). Neither generalizes to the
connector-list-and-detail surface the redesign demands, neither defines a
shared `auth.status` enum, neither defines the per-scope `serif_note` field,
and neither defines what happens to connectors that have no OAuth surface at
all (Telegram user client uses TDLib session strings; OwnTracks uses a static
bearer; Home Assistant uses a long-lived access token).

The blocking dependency from `connector-lifecycle-ceremony` is explicit:

> The `reauth` action additionally depends on a future
> `connector-oauth-scope-surface` capability and is blocked until that spec
> exists.

— `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4`

A 503 with no `Retry-After` is the right behavior for a dependency that can
only be resolved by spec evolution. This change resolves it.

Stakeholders: the operator (Tze), QA staffer (which currently has to detect
scope drift through a 401-on-API failure-streak heuristic — see
`pr/overview/ingestion-redesign/ingestion-connectors-data.jsx:115-119`), and
every OAuth-bound connector maintainer.

## Goals / Non-Goals

**Goals:**

- Define `required` / `optional` / `sensitive` scope declarations as data on
  the connector module manifest, not as ad-hoc strings sprinkled through
  connector code.
- Define observed-scope storage and freshness in a way that does not require
  new tables (additive columns on `connector_registry`).
- Define a `drift` taxonomy that the dashboard renders consistently: `ok`,
  `extra`, `drift`, `expired`, `unsupported`, `unconfigured`.
- Define `auth.status` as the connector-detail-level rollup the
  `ReauthCallout` reads.
- Define the reauth endpoint contract: `{auth_url, state, expires_in}` for
  OAuth providers, `{error: "unsupported", reason}` for non-OAuth providers,
  HTTP 409 when reauth is not warranted.
- Define replay-safe state tokens.
- Define the audit trail for reauth and for scope-set rotation.
- Define the cross-connector applicability matrix so non-OAuth connectors
  return well-formed-but-empty scope surfaces (not `null`, not omitted).
- Honor existing credential-masking rules: no tokens in any response body.

**Non-Goals:**

- Implementation. Migrations, code, and tests are deferred to follow-up beads.
- A separate `scope_history` table. The existing `public.audit_log` with
  indefinite retention (per `connector-lifecycle-ceremony` spec.md:91) already
  serves this purpose.
- A separate `scope_catalog` registry. Per-provider scope semantics live in
  the connector module's manifest, which is where reviewers already look when
  adding new scope grants.
- Cross-account scope reconciliation for providers with N accounts (Gmail,
  Google Calendar, etc.). Each `(provider, endpoint_identity)` pair has its
  own row in `connector_registry`; scopes are tracked per-row. The
  `google_accounts` table already tracks per-account `granted_scopes`; this
  spec layers a connector-side observed-scopes view on top, it does not
  re-implement the account registry.
- Plugin marketplace for third-party scope sets. Per
  `about/heart-and-soul/v1.md:154-167`, no plugin distribution in v1.
- E2E OAuth flow re-engineering. The OAuth start / callback handlers per
  provider (`google-multi-account-oauth`, `dashboard-spotify-setup`, etc.)
  already exist; this spec adds the connector-detail reauth entrypoint that
  feeds into them.

## Decisions

### Decision 1 — Scope declaration lives in the connector module manifest, not the database

**What:** Each connector module declares its OAuth scopes as a structured
manifest constant (Python dict literal in the module's package, e.g.
`butlers.modules.spotify.SCOPES`). The manifest schema is:

```python
SCOPES = {
    "version": 2,                   # bump when required set evolves
    "required": [
        ScopeDecl(name="user-read-recently-played",
                  serif_note="Used to poll listening history every 10 minutes."),
        ScopeDecl(name="user-read-playback-state",
                  serif_note="Used to detect what is currently playing."),
    ],
    "optional": [
        ScopeDecl(name="user-top-read",
                  serif_note="Powers the weekly listening-summary skill."),
    ],
    "sensitive": [
        ScopeDecl(name="playlist-modify-public",
                  serif_note="Required only if you ask the butler to create playlists.",
                  approval_reason="Grants write access to your public Spotify playlists."),
    ],
}
```

**Why:**

- The manifest is the existing pattern: `google-multi-account-oauth/spec.md:84-97`
  already enumerates `scope_set` registries (`base`, `calendar`, `drive`,
  `gmail`, `health`); this generalizes that pattern across providers.
- A DB-backed scope catalog would require a CRUD UI to edit it, which means
  any operator (or compromised dashboard) could silently widen the required
  scope set. Per the security model (`about/heart-and-soul/security.md:60-95`),
  ephemeral LLM sessions cannot bypass the manifest; the manifest is git-tracked
  identity, not operational tuning.
- Per Non-Negotiable Rule 5
  (`about/heart-and-soul/vision.md:86-98`), git-tracked config is the source
  of truth for "what the butler is". A connector's scope requirements are
  identity ("Spotify the connector needs `user-read-recently-played` to
  function"), not operational tuning ("how often it polls").
- The `serif_note` belongs in the manifest because it is per-scope user-facing
  documentation; the UI binding (`ScopeList`) is dumb and renders whatever the
  API hands it. This avoids translating between scope strings and per-scope
  copy in the frontend.

**Alternatives considered:**

- **Database table `oauth_scope_catalog`:** rejected. Adds CRUD surface, adds
  migration churn when a new connector author wants to register scopes, and
  introduces a layer of indirection between "what the code requires" and
  "what is declared".
- **YAML manifest under `roster/`:** rejected. Connectors live under
  `src/butlers/connectors/`, not under `roster/` (which is per-butler, not
  per-connector). The connector module is the natural home.
- **Per-scope `i18n` strings:** rejected for v1. v1 is English-only per
  `about/heart-and-soul/v1.md:153-167`.

### Decision 2 — Observed scopes are additive columns on `connector_registry`, not a separate table

**What:** Add to `public.connector_registry` (additive only):

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `observed_scopes` | `TEXT[]` | `NULL` | Last-known granted scopes (NULL = never probed) |
| `observed_scopes_fetched_at` | `TIMESTAMPTZ` | `NULL` | Freshness timestamp |
| `required_scopes_version` | `SMALLINT` | `NULL` | Manifest version observed at last reauth (drift detection target) |
| `auth_status` | `VARCHAR(32)` | `NULL` | Computed rollup (`ok` / `degraded` / `expired` / `rotation-needed` / `unsupported` / `unconfigured`) |

**Why:**

- The existing `connector_registry` is already the connector-fleet root
  (`connector-base-spec/spec.md:319-348,381-419`). Adding rollup columns
  alongside `state`, `last_heartbeat_at`, and `settings` keeps the dashboard
  query simple (one row → all the data the connector-detail page needs for
  the auth panel).
- A separate `connector_scope_observation` table would force a JOIN on every
  connector list query, and there is exactly one "current" observation per
  connector — history goes to `audit_log`.
- `required_scopes_version` is what makes rotation detectable: when the
  manifest version increments and `required_scopes_version` on a connector
  row is still the prior value, the connector is flagged `rotation-needed`.
  This is cheap (no provider call needed) and deterministic.

**Alternatives considered:**

- **JSONB column `auth_metadata`:** rejected. Would shadow the typed
  `granted_scopes TEXT[]` precedent on `public.google_accounts`
  (`google-account-registry/spec.md:22`) and would require ad-hoc validation
  in every consumer.
- **Recompute `auth_status` on read:** rejected for hot paths. Connector list
  pages render many connectors; precomputing the rollup on write (heartbeat,
  reauth, scope observation) keeps reads cheap. The dashboard's existing
  pattern is "rollup on write" (see `connector-base-spec/spec.md:319-340` for
  the `state` rollup precedent).

### Decision 3 — Scope drift taxonomy, with `extra` as audit-only not drift

**What:** Five drift classes, computed from `(required, granted)`:

| Class | Condition | UI affordance |
|-------|-----------|---------------|
| `ok` | `granted ⊇ required` AND no `sensitive` granted | Green dot, "granted" |
| `extra` | `granted ⊋ required` (more than asked for) | Green dot, "granted" — but elevation audit-logged |
| `drift` | `granted ⊅ required` (at least one required is missing) | Red dot, "mismatch" — triggers ReauthCallout |
| `expired` | Provider rejected the token (no granted set observable) | Red dot, "expired" — triggers ReauthCallout |
| `unsupported` | Non-OAuth connector | List omitted; auth panel shows `auth.status = unsupported` with the alt-auth surface |

`sensitive` grant is orthogonal — a scope can be granted (so its row says
"granted") AND sensitive (so it has a yellow indicator and is mentioned in
the serif_note as an elevated permission).

**Why:**

- `extra` is the "user previously granted more than we now ask for" case. This
  happens when the connector author tightens scope requirements (e.g. drops
  `gmail.modify` for a read-only redesign). Surfacing it as "drift" would
  produce false positives. Surfacing it as audit-only is the right balance —
  the operator sees it in the audit log if they care, but the connector page
  doesn't shout.
- `expired` is distinct from `drift` because the remediation is different: a
  full reauth (new auth_url with current required scopes) rather than a scope
  upgrade.
- `unsupported` is required to keep the UI grid uniform — every connector
  card has an auth panel, but non-OAuth connectors say "no OAuth surface;
  this connector uses a static bearer token / TDLib session / app password."

**Alternatives considered:**

- **Collapse `extra` and `ok`:** rejected. The audit trail is what makes
  scope-set tightening reviewable. The dashboard does not need to
  distinguish, but the audit log does.
- **Distinct UI states for `sensitive-granted`:** considered. Rejected for v1
  in favor of overlaying sensitivity onto the existing `ok | drift | expired`
  status. The serif_note carries the sensitivity context: "Grants write
  access to your public Spotify playlists." A future enhancement can split
  the state if v1 use surfaces a need.

### Decision 4 — `auth.status` enum has six values, drives ReauthCallout

**What:** `auth.status ∈ {ok, degraded, expired, rotation-needed, unsupported, unconfigured}`.

| Value | Condition | ReauthCallout |
|-------|-----------|---------------|
| `ok` | All `required` scopes granted; no manifest version drift | Hidden |
| `degraded` | At least one `optional` scope missing (functionality reduced but not broken) | Hidden — surfaced as eyebrow only |
| `expired` | Token rejected; reauth required | Visible, red, "session expired" copy |
| `rotation-needed` | Manifest version > `required_scopes_version` on row OR scope drift detected | Visible, red, "reauth required" copy |
| `unsupported` | Non-OAuth connector | Hidden; replaced with alt-auth surface (see Decision 6) |
| `unconfigured` | No credentials stored at all | Hidden on the detail page; surfaced as "Connect" CTA |

**Why:**

- `degraded` covers the case where Spotify grants `user-read-recently-played`
  but not `user-top-read` (which only powers the weekly summary skill). The
  connector still works for its primary purpose; the operator does not need
  to be alarmed.
- `rotation-needed` is the version-bump trigger AND the drift trigger,
  because the UX affordance is identical: "re-authorize and we'll request the
  current required set."
- `unconfigured` is distinct from `expired` because the initial-connect flow
  is a different journey ("Connect Spotify" button on the settings page) than
  the reauth flow ("Re-authorize" pill on the connector-detail page).

**Alternatives considered:**

- **Boolean `needs_reauth`:** insufficient. The bundle's fixture uses
  `auth.status: 'needs_reauth'` for Spotify and `auth.status: 'expiring'` for
  Google Calendar (4 days from channel expiry). The enum needs to carry that
  granularity.
- **`expiring` as a distinct state:** considered. Rejected for OAuth because
  the connector should proactively refresh before expiry (see
  `connector-spotify/spec.md:131-135` for the 5-minutes-before-expiry refresh
  pattern). For non-OAuth where refresh is impossible, expiring-soon is
  surfaced as an eyebrow on the connector card, not as `auth.status`.

### Decision 5 — Reauth state tokens are CSRF-bound, single-use, idempotent

**What:**

- `POST /api/ingestion/connectors/{type}/{identity}/reauth` (Approvals-gated)
  returns `{auth_url, state, expires_in}` where `state` is a 32-byte
  URL-safe random token bound to `(connector_type, endpoint_identity,
  requesting_operator)`. The state is stored in the OAuth state store
  (existing infrastructure per `google-multi-account-oauth/spec.md:76-83`)
  with a 10-minute TTL.
- The provider callback (`GET /api/oauth/{provider}/callback?state=...&code=...`)
  validates the state, exchanges the code, updates `observed_scopes` and
  `auth_status` on the matching `connector_registry` row, and consumes the
  state (one-use).
- Rapid re-initiation: if `POST .../reauth` is called while a prior state is
  outstanding, the prior state is revoked and a fresh one issued. This is
  idempotent — the dashboard can spam-click the "Re-authorize" button and
  the worst case is one orphan state token in the store.
- Replay protection: a consumed state token returns HTTP 400
  `{error: "state_already_used"}` from the callback. Distinct from
  `state_expired` (TTL passed) and `state_unknown` (never issued).

**Why:**

- The existing Google OAuth callback already validates state (per
  `google-multi-account-oauth/spec.md:76-83`); this is a generalization, not
  a new mechanism.
- Single-use state tokens with revoke-on-reissue are standard OAuth practice.
  Not specifying replay protection here would leave the contract underspecified
  and would force the implementer to invent it.

**Alternatives considered:**

- **Long-lived state tokens (24h):** rejected. Increases the attack surface
  if the dashboard is compromised between issue and use. 10-minute TTL covers
  the realistic user journey (click button → open browser → consent → callback).
- **Stateless JWTs:** rejected. The state store is already there and is the
  established pattern. JWTs would add a signing-key dependency.

### Decision 6 — Non-OAuth connectors get an alt-auth surface, not a degraded scope surface

**What:** Connectors whose credential type is not OAuth (Telegram bot token,
Telegram user-client TDLib session, OwnTracks bearer token, Home Assistant
long-lived access token, WhatsApp Meta business app, etc.) return:

```json
{
  "auth": {
    "status": "unsupported",
    "type": "<credential_type>",
    "note": "<provider-specific copy>",
    "alt_surface": {
      "kind": "session-validity" | "static-token" | "device-pairing",
      "validity_known": true | false,
      "validity_expires_at": "<iso8601 | null>",
      "remediation_path": "<dashboard route>"
    }
  },
  "scopes": []
}
```

The reauth endpoint for these connectors returns HTTP 200 with
`{error: "unsupported", reason: "<explain>", remediation: "<dashboard route>"}`
(not 503, not 404 — the route exists; the operation is intentionally a no-op
because the underlying credential model has no reauth semantics).

**Why:**

- The UI grid must be uniform: every connector card has an auth panel. If the
  panel were absent for non-OAuth connectors, the layout would jump.
- The `alt_surface` block tells the dashboard exactly what to render
  (`session-validity` → "Session valid until {expires_at}", `static-token`
  → "Static bearer token; rotate via Settings", `device-pairing` → "QR code
  pairing; re-pair via mobile app").
- Returning 200 with a structured error (rather than 4xx) is intentional —
  the operator did nothing wrong; the connector simply does not support the
  operation. This is a "respect the operator's time" affordance.

**Per-connector classification (verified from existing specs):**

| Connector | Credential type | `auth.status` | Reauth supported? |
|-----------|----------------|----------------|-------------------|
| Spotify | OAuth 2.0 PKCE (`dashboard-spotify-setup/spec.md:9-50`) | `ok | degraded | expired | rotation-needed` | Yes |
| Gmail | Google OAuth (`connector-gmail/spec.md:9-34`) | same | Yes |
| Google Calendar | Google OAuth (`connector-google-calendar/spec.md:8-37`) | same | Yes |
| Google Drive | Google OAuth (referenced in `module-google-drive`) | same | Yes |
| Google Health | Google OAuth (`connector-google-health/spec.md:9-41`) | same | Yes |
| Discord | OAuth 2.0 (planned) | same | Yes |
| Telegram bot | Bot token (static, `connector-telegram-bot/spec.md`) | `unsupported` | No — rotate token via Settings |
| Telegram user client | TDLib session string (`connector-telegram-user-client/spec.md:111-137`) | `unsupported` | No — re-pair via mobile flow |
| OwnTracks | Bearer token (`connector-owntracks/spec.md:47-105`) | `unsupported` | No — regenerate token via Settings |
| Home Assistant | Long-lived access token | `unsupported` | No — rotate via HA UI |
| WhatsApp | Meta business app credentials | `unsupported` | No — rotate via Meta Business |
| Steam | API key (planned per `connector-steam`) | `unsupported` | No — regenerate via Steam Web API |

The classification matrix above is normative and lives in the spec under
`§Per-Connector Applicability Matrix`. New connector authors MUST update it
or the spec is out of date.

**Alternatives considered:**

- **404 on non-OAuth connectors:** rejected. The dashboard would have to
  special-case the route per-connector. The 200-with-structured-error pattern
  lets the same client code handle every connector type.
- **Omit `auth` block for non-OAuth connectors:** rejected. UI grid uniformity.

### Decision 7 — Re-introspection cadence is 6h default, opportunistic on every token refresh

**What:** OAuth connectors SHALL re-introspect their granted scopes:

1. **Opportunistically:** every time a token refresh succeeds (per
   `connector-spotify/spec.md:111-135` for Spotify, similar pattern for
   Google). This is the primary signal — connectors refresh tokens hourly to
   six-hourly depending on provider TTL, so observed scopes stay fresh
   without a dedicated probe loop.
2. **On a cadence:** a background task every 6 hours (configurable via
   `CONNECTOR_SCOPE_REINTROSPECT_INTERVAL_S`, default 21600) for connectors
   that have not refreshed in that window. Belt-and-suspenders for
   long-lived tokens.
3. **On operator demand:** the dashboard's connector-detail page may include
   a "Re-check scopes" affordance that triggers an immediate introspection.
   This is a future enhancement; the v1 spec only requires the auto cadence.

**Why:**

- Opportunistic-on-refresh is free (the refresh response already includes the
  scope list for Spotify and Google).
- A 6h fallback bounds the staleness window without hammering provider APIs.
- This is the same pattern as `last_heartbeat_at` for connector liveness
  (`connector-base-spec/spec.md:329-348`) — observation timestamp + freshness
  TTL.

**Alternatives considered:**

- **Re-introspect on every heartbeat:** rejected. Heartbeats are every 120s;
  scope introspection should not run that often.
- **Re-introspect only when operator views the page:** rejected. That makes
  the dashboard a real-time gate, which violates the "dashboard is a view,
  not a gate" pattern. The dashboard reads from `connector_registry`, period.

### Decision 8 — `sensitive` scope grants get a distinct audit entry on top of the standard reauth pair

**What:** Standard reauth audit pair (per `connector-lifecycle-ceremony`):

1. `connector.reauth.submit` (on POST `.../reauth` → Approvals submission)
2. `connector.reauth.approved` or `connector.reauth.denied` (on Approval resolution)

Reauth completion (callback):

3. `connector.reauth.completed` (success) or `connector.reauth.failed` (error)
   on the OAuth callback.

If the callback observes that the new `granted_scopes` includes any scope
marked `sensitive` in the manifest, a fourth entry is appended:

4. `connector.scope.elevated_grant` with structured target:
   `{connector_type, endpoint_identity, scope_name, manifest_version,
   approval_reason}`. The `approval_reason` is copied from the manifest
   `ScopeDecl.approval_reason`.

Scope-set version bumps (manifest edits) emit a fifth audit kind on the
**next deployment** (when the daemon notices that the manifest version is
ahead of the row's `required_scopes_version`):

5. `connector.scope.required_changed` with target:
   `{connector_type, endpoint_identity, from_version, to_version,
   newly_required: [...], newly_dropped: [...]}`. This is emitted once per
   row per version transition, idempotently keyed.

**Why:**

- The reauth audit pair already exists per `connector-lifecycle-ceremony`
  (spec.md:91-101). This spec adds the OAuth-specific completion entries and
  the elevation/rotation entries.
- The `elevated_grant` entry is what makes "did the operator actually grant
  write access to their Gmail?" auditable after the fact.
- The `required_changed` entry creates a paper trail when a connector
  maintainer bumps the required scope set, which is the only time existing
  connectors flip to `rotation-needed`.

**Alternatives considered:**

- **Combine all four into a single rich audit entry:** rejected. The
  Approvals submit / resolution split is mandated by the lifecycle ceremony
  spec; the completion entry is needed because OAuth callbacks run
  asynchronously after the Approval resolves.
- **Skip `required_changed` and let `rotation-needed` speak for itself:**
  rejected. The audit log is the only durable record that scope requirements
  changed; without it, "why is this connector suddenly in rotation-needed?"
  is hard to answer post-hoc.

## Risks / Trade-offs

### Risk 1 — Provider API drift breaks introspection

**Risk:** Spotify or Google could change their token-refresh response shape
or remove the `scope` echo (Google already sometimes omits `scope` on refresh
when nothing changed — see `src/butlers/api/routers/oauth.py:1607-1620`).

**Mitigation:** The spec already accommodates this: when `scope` is absent
from a refresh response, `observed_scopes` is NOT updated (the prior
observation is kept), and `auth_status` does not flip to `drift` on the basis
of a missing field. The 6h re-introspection cadence is the fallback. This
mirrors the existing Google probe behavior in `oauth.py:1607-1620`.

### Risk 2 — Manifest version bump strands existing connectors

**Risk:** A connector author bumps the manifest from v1 to v2 (adds
`user-modify-playback-state`) and the next deploy puts every Spotify connector
into `rotation-needed`. If the operator has 5 Spotify accounts, that is 5
reauth flows.

**Mitigation:** This is the intended behavior — the operator MUST be aware
that a scope upgrade happened. The `connector.scope.required_changed` audit
entry makes it explicit, and the dashboard's `ReauthCallout` carries the
serif explanation. If a connector author wants to add an `optional` scope
without forcing reauth, they declare it under `optional`, not `required`,
which does not bump the version semantically (the version is on the
`required` set only). The spec encodes this: `required_scopes_version`
increments ONLY when `required` changes, not `optional` or `sensitive`.

### Risk 3 — Approvals fatigue

**Risk:** Reauth is Approvals-gated. If a connector flaps between `ok` and
`drift` (e.g. clock skew, brief provider hiccup), the operator could be
prompted to approve reauth they did not initiate.

**Mitigation:** The reauth endpoint is **operator-initiated** (button click
on the dashboard), not automatic. The connector daemon NEVER calls reauth on
its own behalf. If the auth_status flips automatically (drift detected on
introspection), the dashboard surfaces `ReauthCallout` but the Approval is
only created when the operator clicks "Re-authorize". This is the same
pattern as `connector-lifecycle-ceremony` `rotate-token` — automatic
detection, manual initiation.

### Risk 4 — Non-OAuth connector authors forget to update the applicability matrix

**Risk:** A new non-OAuth connector (e.g. Steam) is added without an entry in
the per-connector applicability matrix; its `auth.status` is `null` and the
dashboard crashes.

**Mitigation:** The spec requires `auth.status` to be a non-null enum value
for every connector type. The implementation MUST default to `unsupported`
when a connector type is unrecognized (fail-safe to the alt-auth surface,
which is "no OAuth surface available — see connector documentation"). A
follow-up bead in this change's tasks.md adds the matrix-completeness test:
"every value of `SourceProvider` (per `connector-base-spec/spec.md:107`) has
a defined `auth.status` resolver."

### Trade-off 1 — No third-party scope catalog plugin API

**Trade-off:** Connector authors must edit the manifest in code, not via a
dashboard CRUD. This means scope-set additions require a deploy.

**Rationale:** v1 has no plugin marketplace
(`about/heart-and-soul/v1.md:154-167`). All connector authors are the same
person as the operator (the user themself). Adding CRUD UI for what is
effectively code-shaped configuration would violate the
"git-tracked-config-is-identity" principle (Non-Negotiable Rule 5).

### Trade-off 2 — `observed_scopes` is a TEXT[] on `connector_registry`, not normalized

**Trade-off:** No JOIN-able `connector_scope` table; no per-scope timestamps.

**Rationale:** The dashboard needs the current observation, not a history of
observations. History lives in the audit log. Normalization would require
per-scope rows for every connector on every introspection, which is high
write volume for a low query benefit.

## Migration Plan

This change is spec-only. No code, no migration ships with this change. The
follow-up implementation bead (see "Bead-creation handoff" in tasks.md) will:

1. Add the four additive columns to `public.connector_registry` via Alembic
   migration. Nullable defaults mean no data backfill needed.
2. Extend each OAuth connector's startup sequence to call the introspection
   helper on first successful token refresh.
3. Extend the OAuth callback handlers per provider to write `observed_scopes`
   and emit the audit entries.
4. Replace the HTTP 503 stub in `/api/ingestion/connectors/.../reauth` with
   the contract defined here.
5. Add the per-connector applicability matrix as a Python registry and a
   pytest that asserts every `SourceProvider` value has an entry.

There is no live data to migrate. Existing connectors will simply gain a
populated `auth.status` on first introspection after the deploy.

## Open Questions

1. **Should the dashboard cache `auth_status` at the request layer (e.g.
   per-page snapshot) or always re-read from `connector_registry`?**
   Recommended: always re-read. The column is updated at low frequency
   (hourly to six-hourly), so the freshness is fine. A cache would add a TTL
   knob with no benefit.

2. **Should `auth_status = degraded` (optional scope missing) be visible
   on the connector-list page, or only on the connector-detail page?**
   Recommended: connector-detail only. The connector-list page already shows
   `health = ok | degraded | error`. Stacking another "degraded" surface
   would create noise. The connector-detail page is where the operator goes
   to investigate.

3. **Should reauth state tokens be per-operator (when v2 multi-user lands)?**
   Recommended: yes, even in v1. The state token's bound tuple includes
   `requesting_operator` (always the owner in v1, but the field is reserved).
   This costs nothing now and avoids a state-token contract change in v2.

## Source References

- Non-Negotiable Rule 1 (user-federated) — `about/heart-and-soul/vision.md:60-63`
- Non-Negotiable Rule 5 (git-tracked-config is identity) —
  `about/heart-and-soul/vision.md:86-98`
- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md:110-115`
- Security model — credential authority + masking —
  `about/heart-and-soul/security.md:96-147`
- v1 scope — `about/heart-and-soul/v1.md:103-110,154-167`
- Blocking spec dependency declaration —
  `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4,17,36-40,91-101`
- UI binding for `ReauthCallout` and `ScopeList` —
  `pr/overview/ingestion-redesign/ingestion-connector-detail.jsx:70-101,216-245`
- Spotify fixture (auth.status, scopes) —
  `pr/overview/ingestion-redesign/ingestion-connectors-data.jsx:97-121`
- Design language for serif italic notes —
  `pr/overview/ingestion-redesign/DESIGN_LANGUAGE.md:30,108-140`
- Google scope-set registry precedent —
  `openspec/specs/google-multi-account-oauth/spec.md:84-145`
- `granted_scopes` precedent on `public.google_accounts` —
  `openspec/specs/google-account-registry/spec.md:22,150-162`
- Reference token introspection (`_probe_google_token`) —
  `src/butlers/api/routers/oauth.py:164,1547-1620`
- Connector base spec (`connector_registry` and `ConnectorDetail` Pydantic) —
  `openspec/specs/connector-base-spec/spec.md:319-348,381-419`
- Spotify connector OAuth scopes (manifest reference) —
  `openspec/specs/connector-spotify/spec.md:229-247`
- Spotify dashboard `needs_reauth` pattern —
  `openspec/specs/dashboard-spotify-setup/spec.md:84-99`
- Credential masking contract —
  `openspec/specs/core-credentials/spec.md:52-99`
- Non-OAuth connector references for applicability matrix —
  `openspec/specs/connector-telegram-bot/spec.md`,
  `openspec/specs/connector-telegram-user-client/spec.md:111-137`,
  `openspec/specs/connector-owntracks/spec.md:47-105`
- Reauth bead (implementation handoff target) — `bu-1f91v.11`
