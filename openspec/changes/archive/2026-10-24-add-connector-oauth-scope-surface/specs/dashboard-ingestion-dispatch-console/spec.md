# Dashboard Ingestion Dispatch Console — OAuth Reauth Lifecycle Authority Delta

## ADDED Requirements

### Requirement: OAuth reauth lifecycle authority

The `/ingestion/connectors` recovery resolver SHALL assign each `reauth`
interaction to the durable authority path defined by
`connector-oauth-scope-surface/spec`. Only generic OAuth reauth SHALL invoke
the Approvals module and emit the generic reauth audit sequence. Spotify and
non-OAuth recovery SHALL remain outside that generic approval path.

Before the typed recovery resolver runs, the API SHALL normalize stored scope
status `expired | rotation-needed` → `needs_reauth` and preserve the stored
cause as `auth.recovery_reason`. The typed recovery resolver therefore retains
one interactive state: generic OAuth selects the Approvals-gated generic flow,
Spotify selects its connector-owned Passport/PKCE flow, and `unsupported`
non-OAuth connectors remain non-interactive.

#### Scenario: Generic OAuth reauth is Approvals-gated

- **WHEN** an owner invokes reauth for a generic OAuth-bound connector other
  than Spotify
- **THEN** the reauth handler SHALL submit the generic OAuth request to the
  Approvals module before it delegates to the generic OAuth flow defined by
  `connector-oauth-scope-surface/spec`
- **AND** the generic reauth audit sequence SHALL be emitted only for that
  generic OAuth request
- **AND** the production generic OAuth provider registry SHALL remain
  Google-only

#### Scenario: Spotify reauth stays connector-owned

- **WHEN** the recovery resolver identifies a Spotify connector
- **THEN** it SHALL navigate to `/secrets?focus=u:spotify`
- **AND** the Passport projection's action SHALL call
  `POST /api/connectors/spotify/oauth/start`
- **AND** Spotify access and refresh tokens SHALL remain RFC 0006 Tier 2
  credentials stored in `public.entity_info` on the owner entity and resolved
  via `resolve_owner_entity_info()`; the content-blind Passport projection is
  not a secret authority
- **AND** it SHALL NOT submit the recovery to the Approvals module, construct
  a generic OAuth URL, or create a generic OAuth Spotify state or callback

#### Scenario: Non-OAuth reauth is rejected before approval

- **WHEN** a connector's `auth_status = unsupported` reaches the recovery
  resolver
- **THEN** the handler SHALL return the structured unsupported response defined
  by `connector-oauth-scope-surface/spec`
- **AND** the response SHALL be produced before approval submission
- **AND** the request SHALL NOT pass through the Approvals module

## Source References

- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md:110-115`
- Existing `/ingestion/connectors` recovery resolver contract —
  `openspec/specs/dashboard-ingestion-dispatch-console/spec.md:331-454`
- Detailed generic OAuth, Spotify, and unsupported reauth contract —
  `openspec/changes/add-connector-oauth-scope-surface/specs/connector-oauth-scope-surface/spec.md`
- Binding Tier 2 credential authority —
  `about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md#credential-store--three-tier-authority-model`
- Spotify token authority — `openspec/specs/core-credentials/spec.md:197-241`
