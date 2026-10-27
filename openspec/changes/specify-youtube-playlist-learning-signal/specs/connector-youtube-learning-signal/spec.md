## ADDED Requirements

### Requirement: Narrowed Learning-Intent Signal Scope

The capability SHALL emit an Education-lane learning-intent signal derived exclusively from
items the owner explicitly adds to one or more of the owner's own, non-Watch-Later YouTube
playlists that the owner has separately designated as learning playlists. The capability SHALL
NOT infer learning intent from watch history, likes, favorites, subscriptions, recommendations,
or any other passive or algorithmic YouTube signal, because no official YouTube Data API surface
exposes those as of this draft (see `design.md` Evidence Table). This requirement records a
principled narrowing of the original "learning intent from anything watched" outcome and does not
by itself authorize implementation (see `Requirement: Owner Approval Gate`).

#### Scenario: Signal source is explicit playlist curation only

- **WHEN** the connector detects a new item in an owner-designated learning playlist
- **THEN** it SHALL emit exactly one `ingest.v1` envelope representing "owner added `<video>` to
  `<playlist>`"
- **AND** it SHALL NOT emit any envelope representing a video watched, liked, favorited, or
  subscribed-to channel, because the API does not expose those actions

#### Scenario: Undesignated playlists and channel activity are never polled

- **WHEN** the connector runs for an eligible account
- **THEN** it SHALL poll only the playlist IDs listed in that account's
  `metadata.youtube_learning_playlist_ids`
- **AND** it SHALL NOT call `activities.list`, enumerate the account's full playlist list, or read
  uploads, subscriptions, or Watch History/Watch Later

### Requirement: Owner Approval Gate

No implementation bead, migration, connector code, dashboard route, or OAuth wiring for this
capability SHALL be created until the owner has explicitly approved this narrowed signal (see
`tasks.md` §3). Registering the `youtube` scope set in `google-multi-account-oauth` (this change's
sibling delta) SHALL NOT itself be treated as that approval.

#### Scenario: Draft merge does not authorize implementation

- **WHEN** this OpenSpec change is validated, reviewed, and archived
- **THEN** no implementation bead for `connector-youtube-learning-signal` SHALL be created as a
  consequence of archival alone
- **AND** a future coordinator or worker SHALL find and record explicit, separate owner approval
  before dispatching implementation

### Requirement: Owner Account Discovery and Playlist Configuration

The connector SHALL operate against every `public.google_accounts` row whose `status = 'active'`,
whose `granted_scopes` includes `https://www.googleapis.com/auth/youtube.readonly`, and whose
`metadata.youtube_learning_playlist_ids` is a non-empty array of playlist ID strings. The
connector SHALL maintain independent per-account, per-playlist polling state and emit one
heartbeat per account.

#### Scenario: Startup with one or more eligible accounts

- **WHEN** the connector starts and one or more `google_accounts` rows are `status = 'active'`,
  carry `youtube.readonly`, and have a non-empty `youtube_learning_playlist_ids`
- **THEN** the connector SHALL spawn one polling loop per (account, playlist ID) pair
- **AND** SHALL emit one heartbeat per account labelled `endpoint_identity =
  youtube_learning_signal:user:<email>`
- **AND** SHALL report aggregate health status `healthy`

#### Scenario: Startup with no eligible accounts

- **WHEN** no active `google_accounts` row carries both `youtube.readonly` and a non-empty
  `youtube_learning_playlist_ids`
- **THEN** the connector SHALL report aggregate health status `degraded`
- **AND** SHALL emit no envelopes
- **AND** SHALL re-check eligibility every 300 seconds (matching `connector-google-health`'s
  `scope_recheck_s` default)

#### Scenario: Account gains eligibility while running

- **WHEN** the connector is running and a `google_accounts` row transitions to eligible (scope
  granted and/or playlist IDs configured)
- **THEN** within 300 seconds the connector SHALL spawn polling loops for the newly eligible
  (account, playlist) pairs and emit a heartbeat for the account if one does not already exist

#### Scenario: Account loses eligibility mid-run

- **WHEN** the connector is running and an account loses `youtube.readonly` or has its
  `youtube_learning_playlist_ids` cleared
- **THEN** within 300 seconds the connector SHALL stop all polling loops for that account and close
  its heartbeat
- **AND** SHALL continue polling any other still-eligible accounts uninterrupted

#### Scenario: Account has no linked YouTube channel

- **WHEN** an eligible account's `channels.list(mine=true)` call returns an empty channel list
- **THEN** the connector SHALL treat that account as `degraded` (not `error`)
- **AND** SHALL emit no envelopes for it
- **AND** SHALL re-check channel linkage on the same 300-second cadence

### Requirement: OAuth Token Lifecycle via Shared Google Credential Pipeline

The connector SHALL NOT implement its own OAuth refresh logic. It SHALL delegate to the shared
Google credential pipeline (`load_google_credentials()` for app credentials,
`google_credentials._resolve_entity_refresh_token()` for the per-account refresh token, keyed by
the account's companion `entity_id`), the same pattern `connector-google-health` uses. Each access
token minted for this connector's calls MUST request only the `youtube.readonly` scope, even when
the account's stored grant is a broader union including Calendar, Drive, Gmail, or Health scopes.

#### Scenario: Access token acquisition

- **WHEN** the connector needs to call `playlistItems.list` or `channels.list`
- **THEN** it SHALL request a fresh access token from the shared Google credential helper scoped
  to exactly `youtube.readonly`
- **AND** SHALL NOT read `GOOGLE_OAUTH_REFRESH_TOKEN` from `CredentialStore` or the environment
  directly

#### Scenario: Refresh token invalid or revoked

- **WHEN** a YouTube Data API call returns HTTP 401
- **THEN** the connector SHALL invalidate its cached access token and retry once
- **AND** if the retry also returns 401, SHALL set that account's heartbeat to `error` (error
  message `token_invalid`) and stop polling it, leaving other accounts unaffected
- **AND** `public.google_accounts.status` SHALL be flipped to `revoked` only when Google's token
  endpoint itself returns `invalid_grant`, so a transient or scope-local 401 never knocks the
  sibling Google connectors (Calendar, Drive, Gmail, Health) offline

#### Scenario: Owner revokes mid-poll

- **WHEN** the owner revokes the `youtube.readonly` grant (or disconnects the account) while a poll
  is in flight
- **THEN** the in-flight call SHALL be allowed to fail naturally (401/`invalid_grant` per the
  scenario above) rather than being force-cancelled
- **AND** no partial or synthetic envelope SHALL be emitted for that poll cycle

#### Scenario: Access tokens are never persisted

- **WHEN** the connector holds an access token
- **THEN** the token SHALL live only in memory and SHALL NOT be written to the database, logs, or
  any file

### Requirement: Per-Playlist Polling and Cursor Semantics

The connector SHALL poll `playlistItems.list(playlistId=<id>)` per designated playlist and track a
durable cursor of previously-seen `playlistItem` IDs (or the equivalent stable item identifier) to
detect only additions.

#### Scenario: First-run baseline

- **WHEN** a (account, playlist) polling loop runs for the first time (no cursor exists)
- **THEN** the connector SHALL page through the full current playlist contents via `pageToken`
- **AND** SHALL record every item's ID as the initial cursor set
- **AND** SHALL emit no `ingest.v1` envelopes for this baseline pass (items present before the
  connector existed are not new learning-intent events)

#### Scenario: Steady-state polling detects additions only

- **WHEN** a subsequent poll finds item IDs not present in the recorded cursor set
- **THEN** the connector SHALL emit one `ingest.v1` envelope per newly-seen item ID
- **AND** SHALL add each newly-seen ID to the cursor set
- **AND** SHALL NOT emit an envelope for an item whose ID was already recorded, even if its
  position in the playlist changed (reordering is not a new event)

#### Scenario: Item removed from playlist

- **WHEN** a previously-recorded item ID no longer appears in the playlist
- **THEN** the connector SHALL NOT emit a removal/negative envelope (the API surface used here has
  no reliable way to distinguish "owner removed it" from "video was deleted/made private by its
  uploader")
- **AND** the connector MAY drop the ID from its cursor set or retain it; either is acceptable
  because it only affects re-detection of a since-removed-then-re-added item, not correctness of
  new-addition detection

#### Scenario: Empty playlist

- **WHEN** a designated playlist currently has zero items
- **THEN** the connector SHALL record an empty cursor set and emit no envelopes
- **AND** SHALL continue polling on the normal cadence without treating this as an error

#### Scenario: Designated playlist deleted or inaccessible

- **WHEN** `playlistItems.list` returns 404 (deleted) or 403 (no longer accessible, e.g. made
  private by another owner in a collaborative playlist) for a designated playlist ID
- **THEN** the connector SHALL mark that specific (account, playlist) polling loop `degraded`, log
  the condition, and continue polling the account's other designated playlists unaffected
- **AND** SHALL NOT remove the playlist ID from `youtube_learning_playlist_ids` (only the owner's
  own future configuration change does that)

### Requirement: Poll Cadence and Quota Behavior

The connector SHALL poll each designated (account, playlist) pair on a bounded interval that keeps
aggregate quota usage well within the default YouTube Data API daily budget of 10,000 units.

#### Scenario: Default poll interval

- **WHEN** the connector has no per-resource override configured
- **THEN** it SHALL poll each (account, playlist) pair every 1800 seconds (30 minutes), matching
  `connector-google-health`'s lowest-frequency default
- **AND** at 1 unit per `playlistItems.list` call (per live-fetched official quota documentation),
  this yields at most 48 units/day per playlist per account — supporting well over 100
  (account, playlist) pairs before approaching the default daily budget

#### Scenario: Quota exhaustion (403 quotaExceeded)

- **WHEN** a `playlistItems.list` or `channels.list` call returns HTTP 403 with reason
  `quotaExceeded`
- **THEN** the connector SHALL back off that account's polling for the remainder of the current UTC
  day (quota resets at midnight Pacific time per YouTube Data API documentation)
- **AND** SHALL set that account's heartbeat to `degraded` with `error_message =
  "quota_exceeded"`, not `error`
- **AND** SHALL NOT affect other accounts' polling schedules

### Requirement: Scope-Restricted, Content-Minimal Envelope

Each emitted `ingest.v1` envelope SHALL carry only the fields needed to record that a specific
video was added to a specific designated playlist, and SHALL NOT carry full video metadata beyond
what `playlistItems.list` returns in its default (non-`part=statistics`) response.

#### Scenario: Envelope field set

- **WHEN** the connector emits an envelope for a newly-detected playlist item
- **THEN** the envelope SHALL include the video ID, video title, playlist ID, and the timestamp the
  item was added to the playlist (`contentDetails.videoPublishedAt` or the poll-observed time,
  whichever the implementer's real-Postgres tests confirm is available and stable)
- **AND** the envelope SHALL NOT include video transcript, description body, comments, or channel
  subscriber/statistics data
- **AND** `sender.identity` SHALL resolve to the owner entity via the same `has-email` /
  `relationship_assert_fact()` pattern `connector-google-health` uses for wellness envelopes (one
  triple per Google account, asserted once the `youtube.readonly` scope is first granted)
