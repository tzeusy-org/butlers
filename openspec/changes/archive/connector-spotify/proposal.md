## Why

Spotify is the user's primary music platform. Listening patterns are a rich, low-effort signal for situational context: focus playlists imply deep work, workout playlists imply exercise, silence late at night implies sleep. The butler framework already has a planned situational context bus (`situational-context-bus` change) that consumes exactly these kinds of signals. A Spotify connector provides the raw listening state data; the context bus interprets it. The connector can be built independently of the context bus — ingested listening events are useful for memory and session enrichment even before context signals exist — but its highest value unlocks once the bus is in place.

## What Changes

- **New Spotify connector** (`src/butlers/connectors/spotify.py`): Standalone polling connector that reads the user's current playback state and recently-played tracks via the Spotify Web API, normalizes listening events into `ingest.v1` envelopes, and submits them to the Switchboard. Polls `currently-playing` at a configurable interval (default 60s active, exponential backoff when idle). Detects state transitions: started playing, stopped, track changed, playlist/album context changed.
- **New `spotify` source channel and provider**: Register `spotify` as a `SourceChannel` and `SourceProvider` in the Switchboard routing contracts, with channel-provider validation.
- **OAuth 2.0 PKCE credential flow**: Spotify uses OAuth 2.0 with PKCE (no client secret needed for personal use). Access token + refresh token stored in `CredentialStore`. Dashboard settings page for account linking (OAuth redirect flow, modeled after Google OAuth pattern).
- **Listening session aggregation**: The connector detects logical listening sessions (contiguous playback with the same playlist/album context) and submits session-level summaries in addition to track-change events, giving butlers both granular and summarized listening data.
- **Dashboard Spotify settings section**: Account linking via OAuth redirect, connection status card, listening stats preview.

## Capabilities

### New Capabilities
- `connector-spotify`: Standalone polling connector for Spotify Web API. Playback state polling, track-change detection, listening session aggregation, ingest.v1 normalization, checkpoint durability, rate limiting, heartbeat protocol.
- `dashboard-spotify-setup`: Dashboard settings section for Spotify account linking (OAuth 2.0 PKCE redirect flow), connection status, and listening activity preview.

### Modified Capabilities
- `butler-switchboard`: Register `spotify` source channel and provider, add channel-provider validation pair.
- `core-credentials`: Store Spotify OAuth access token and refresh token, support automatic token refresh on 401.

## Impact

- **Routing contracts** (`roster/switchboard/tools/routing/contracts.py`): Extend `SourceChannel` and `SourceProvider` literals with `"spotify"`, add to `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Credential store** (`src/butlers/credential_store.py`): Spotify OAuth tokens (access + refresh) stored via existing `CredentialStore` interface. Token refresh logic needed for automatic 401 recovery.
- **Database**: No new tables required. Checkpoint via existing `cursor_store`. Credentials via existing `CredentialStore`.
- **Docker compose**: New `connector-spotify` service in connector layer, minimal resource footprint (polling-only, no sidecar binary).
- **External dependencies**: `httpx` for async Spotify API calls (already in the project), no new Python packages needed.
- **Rate limits**: Spotify Web API allows ~180 requests/minute for personal apps. Polling at 60s intervals uses ~1 request/minute — well within limits.
- **Privacy**: Listening history is personal but low-sensitivity. Same trusted-host model as all other connectors.
- **Context bus integration** (future): Once `situational-context-bus` lands, a lightweight adapter can translate listening events into context signals (`focused`, `exercising`, `sleeping`). This is NOT part of this change — the connector provides raw data only.
