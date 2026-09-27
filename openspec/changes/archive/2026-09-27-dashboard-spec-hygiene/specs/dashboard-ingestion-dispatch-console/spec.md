## MODIFIED Requirements

### Requirement: Ingestion-Originated OAuth page_of_origin Contract

Any recovery control initiated from `/ingestion/connectors` SHALL first resolve
the connector's real recovery capability through one shared typed resolver.
The resolver SHALL be an allowlist: a `connector_type` is registry data, not an
OAuth provider identifier, and SHALL NOT be interpolated into an OAuth URL.

The resolver SHALL return exactly one of the following outcomes:

- **Generic Google OAuth:** `google`, `gmail`, `google_calendar`,
  `google_drive`, and `google_health` use the registered `google` OAuth
  provider. Google Health SHALL include `scope_set=health`.
- **Connector-owned Passport:** `spotify` navigates in-app to
  `/secrets?focus=u:spotify`. Its Passport projection owns the connect or
  reconnect control and delegates that control to the Spotify connector PKCE
  endpoint; it SHALL NOT construct a generic OAuth URL.
- **Passport pairing:** `whatsapp` and `whatsapp_user_client` navigate in-app
  to `/secrets?focus=u:whatsapp`; they SHALL NOT construct an OAuth URL.
- **Unsupported:** every other or unknown connector type renders a clear
  unavailable explanation with no recovery link and no network request.

The API SHALL normalize the scope carrier's stored
`expired | rotation-needed` → `needs_reauth` before this typed recovery
resolver runs, while preserving the stored cause as `auth.recovery_reason`.
Only the normalized `needs_reauth` SHALL create an interactive recovery
control. Generic Google OAuth then follows the registered generic flow;
Spotify follows its connector-owned Passport/PKCE flow. `unsupported`
non-OAuth or unknown connector types remain unavailable with no recovery link
and no network request. Other auth states remain informational.

A generic Google OAuth outcome SHALL stamp `page_of_origin=ingestion` in the
OAuth state token by passing it as a query parameter to
`GET /api/oauth/google/start`. It SHALL preserve an available
`connector_detail_path`, and it SHALL preserve `force_consent` when the
initiating surface requests fresh consent. Spotify's connector-owned PKCE
state and return target are not generic OAuth state.

The generic Google OAuth callback routes the post-dance redirect based on
`state.page_of_origin`; that callback and its routing table are owned by
`dashboard-api` (Requirement: OAuth Per-Provider Generalisation) and
`butler-secrets` (Requirement: Cross-Page Reauth Bookkeeping). This requirement
owns only the `/ingestion/connectors` side. For the Google contract to function:

1. The ingestion reauth initiation MUST pass `page_of_origin=ingestion` as a query
   parameter to `GET /api/oauth/google/start`.
2. The OAuth state token MUST carry `page_of_origin` through the dance.
3. The callback MUST redirect to `/ingestion/connectors` when `state.page_of_origin`
   is `ingestion`.

Spotify is not a generic OAuth provider; its connector-owned recovery boundary
is owned by `dashboard-spotify-setup` (Requirement: Connector-Owned Spotify
OAuth 2.0 PKCE Authorization Flow) and bound on this surface by Requirement:
Connector-owned Spotify recovery.

#### Scenario: Ingestion Google reauth stamps page_of_origin

- **WHEN** the owner clicks the reauthorize action on a connector detail page under
  `/ingestion/connectors` for a Google-backed connector
- **THEN** the generic Google OAuth recovery outcome calls
  `GET /api/oauth/google/start?...&page_of_origin=ingestion`
- **AND** the OAuth state token carries `page_of_origin=ingestion` through the dance
- **AND** on successful OAuth callback the browser is redirected to `/ingestion/connectors`
  (NOT to `/secrets`)

## ADDED Requirements

### Requirement: Timeline Row Replay Lifecycle

A Timeline row's replay control SHALL follow the row through its replay
lifecycle, in addition to the eligibility rules of Requirement: Replay
Controls Respect Server Policy. Activating replay calls
`POST /api/ingestion/events/{id}/replay`. A `replay_failed` row offers the
same action labelled as a retry.

#### Scenario: Accepted replay updates the row optimistically

- **WHEN** the owner replays an eligible row and the API accepts the request
- **THEN** the row immediately shows `replay pending`
- **AND** its replay control is replaced by a non-interactive pending indicator
  until the server reports a new status

#### Scenario: Pending and settled rows expose no replay control

- **WHEN** a row's status is `replay_pending`
- **THEN** it shows a pending indicator and no clickable replay control
- **WHEN** a row's status is `ingested` or `replay_complete`
- **THEN** it renders no replay control

#### Scenario: Rejected replay leaves the row unchanged

- **WHEN** the replay request returns HTTP 409 or another error
- **THEN** a toast states the error message
- **AND** the row's status remains unchanged

### Requirement: Timeline Merges Ingested and Filtered Events

The Timeline ledger SHALL present events from `public.ingestion_events` and
`connectors.filtered_events` as one list ordered by `received_at` descending,
with no visual distinction by source table.

#### Scenario: Unified ordering

- **WHEN** the Timeline loads
- **THEN** events from both sources are interleaved by `received_at DESC`
- **AND** the row treatment does not reveal which table an event came from

#### Scenario: Filtered events map onto ledger fields

- **WHEN** a row comes from `connectors.filtered_events`
- **THEN** its identifier is the filtered event's id, its channel is
  `source_channel`, and its sender is `sender_identity`
- **AND** tier, tokens, and cost render as an em dash because no ingestion tier
  was assigned and no session was spawned

#### Scenario: Status filter chips select the ledger statuses

- **WHEN** the owner toggles Timeline status chips
- **THEN** the request carries the selected statuses as a comma-separated
  `statuses=` parameter (a single `status=` value is also accepted)
- **AND** the ledger resets to its first page
