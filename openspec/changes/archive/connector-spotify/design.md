## Context

The butler framework has a mature connector pattern for messaging platforms (Telegram bot, Telegram user-client, Gmail, WhatsApp). Spotify is fundamentally different: it is not a messaging platform but a **data source for user context signals**. There are no incoming messages to route to specialist butlers — instead, the connector produces listening state events that enrich butler awareness. This makes it the simplest connector to date: no discretion layer, no per-chat buffering, no message classification, no interactive routing. It is a pure polling-and-ingest connector.

Spotify Web API is free for personal use, requires OAuth 2.0 PKCE (no client secret), and has generous rate limits (~180 req/min). The connector polls one or two endpoints at configurable intervals, detects state changes, and submits normalized events to the Switchboard.

The dashboard settings page already supports Google OAuth account linking. Spotify uses the same UX pattern (settings card + OAuth redirect flow) but with PKCE instead of authorization code flow.

**Key dependency**: The highest value from Spotify data comes after the `situational-context-bus` change lands. However, the connector provides standalone value for memory enrichment and session context (e.g., "the user was listening to X during this conversation") even without the context bus.

## Goals / Non-Goals

**Goals:**
- Poll Spotify Web API for current playback state and recently-played tracks
- Detect listening state transitions (started, stopped, track changed, context changed)
- Submit listening events as `ingest.v1` envelopes to the Switchboard
- Aggregate logical listening sessions (contiguous playback in same playlist/album context)
- Dashboard settings section for Spotify account linking via OAuth 2.0 PKCE
- Store OAuth tokens (access + refresh) in CredentialStore with automatic refresh on expiry
- Minimal resource footprint: single polling loop, no sidecar, no persistent connections

**Non-Goals:**
- Playback control (play, pause, skip, volume) — future phase, requires separate MCP tools and approval gating
- Playlist management or creation
- Social features (friend activity, collaborative playlists)
- Audio analysis or music recommendation
- Context bus signal generation (that is the `situational-context-bus` change's responsibility)
- Podcast/audiobook ingestion (same API, future enhancement)

## Decisions

### D1: Polling-only design (no WebSocket, no webhook)

Spotify Web API does not offer webhooks or real-time push. The connector polls `GET /me/player/currently-playing` at a configurable interval.

**Adaptive polling strategy:**
- Active playback detected: poll every `SPOTIFY_POLL_ACTIVE_S` (default 60s)
- No playback / private session: exponential backoff up to `SPOTIFY_POLL_IDLE_S` (default 300s)
- Any state change resets to active polling interval

This keeps API usage well under rate limits (~1-5 req/min) while providing near-real-time detection of listening state changes.

**Alternative considered:** Webhook via Spotify Connect state change notifications.
**Rejected because:** Spotify does not offer webhooks in its Web API. Third-party webhook services exist but add external dependencies and complexity for minimal latency improvement given the polling interval is already 60s.

### D2: Two event types — track changes and session summaries

The connector emits two kinds of events:

1. **Track change events** (`spotify.track_change`): Emitted when the currently-playing track changes. Contains track metadata (name, artist, album, duration, playlist/album context). These are fine-grained, one per track.

2. **Session summary events** (`spotify.session_summary`): Emitted when a listening session ends (playback stopped, context changed to a different playlist/album, or idle timeout exceeded). Contains session duration, track count, dominant genre/mood, and the playlist/album context. These are coarse-grained, one per session.

Session summaries are the primary value for context signals. Track changes provide granularity for memory enrichment.

### D3: ingest.v1 field mapping

```
source.channel           = "spotify"
source.provider          = "spotify"
source.endpoint_identity = "spotify:<spotify_user_id>"
event.external_event_id  = "spotify:<timestamp_ms>:<track_id>" (track change)
                         | "spotify:session:<session_start_ms>" (session summary)
event.external_thread_id = "<playlist_uri|album_uri|null>"
event.observed_at        = <poll timestamp, RFC3339>
sender.identity          = "<spotify_user_id>"
payload.raw              = { full Spotify API response for currently-playing }
payload.normalized_text  = "Listening to <track> by <artist> on <playlist/album>"
                         | "Listening session: <N> tracks over <duration> from <playlist/album>"
control.idempotency_key  = "spotify:<endpoint>:<event_id>"
control.policy_tier      = "default"
control.ingestion_tier   = "full"
```

### D4: OAuth 2.0 PKCE via dashboard settings page

Spotify requires OAuth 2.0 with PKCE for user authorization. The flow:

1. User creates a Spotify Developer app at https://developer.spotify.com/dashboard and enters the `client_id` in the Butlers dashboard settings page.
2. User clicks "Connect Spotify" on the settings page.
3. Dashboard backend generates a PKCE code verifier + challenge, stores the verifier in a short-lived session, and redirects the user to Spotify's authorization endpoint.
4. User authorizes on Spotify. Spotify redirects back to the dashboard callback URL.
5. Dashboard backend exchanges the authorization code + code verifier for access token + refresh token.
6. Tokens are stored in `CredentialStore` under keys `SPOTIFY_ACCESS_TOKEN`, `SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_CLIENT_ID`.
7. Dashboard shows connection status card with Spotify display name, account type, and disconnect button.

**Required Spotify scopes:**
- `user-read-playback-state` — current playback state (track, device, context)
- `user-read-recently-played` — last 50 played tracks with timestamps
- `user-top-read` — top artists and tracks (for preference profiling)

**Redirect URI:** `https://<tailnet-host>/butlers/api/spotify/oauth/callback`

The dashboard callback endpoint is a new FastAPI route that handles the OAuth code exchange and token storage.

### D5: Automatic token refresh

Spotify access tokens expire after 1 hour. The connector handles refresh transparently:

1. Before each API call, check if token is within 5 minutes of expiry (or already expired).
2. If so, use the refresh token to obtain a new access token via `POST https://accounts.spotify.com/api/token`.
3. Store the new access token (and new refresh token, if rotated) in `CredentialStore`.
4. If refresh fails (token revoked), set connector state to `error` and emit a heartbeat with error message directing user to re-authorize via dashboard.

The connector resolves credentials from `CredentialStore` at startup, not from environment variables.

### D6: No discretion layer, no buffering

Unlike messaging connectors, Spotify events do not need discretion filtering — they are always the user's own listening activity, always relevant, and never noise. There is also no per-chat buffering since there are no "chats" — each poll cycle produces zero or one event.

This makes the Spotify connector significantly simpler than messaging connectors: poll → detect change → normalize → submit → checkpoint.

### D7: Listening session state machine

The connector maintains an in-memory state machine for session tracking:

```
idle ──(playback detected)──→ active
active ──(track changed, same context)──→ active (emit track_change event)
active ──(context changed)──→ idle (emit session_summary, then transition to active with new context)
active ──(playback stopped)──→ draining
draining ──(idle timeout, default 5min)──→ idle (emit session_summary)
draining ──(playback resumed, same context)──→ active (no event, continue session)
```

The "draining" state handles brief pauses (bathroom break, phone call) without splitting a single listening session into fragments.

### D8: Checkpoint strategy

The connector checkpoints the timestamp of the last processed poll. On restart, it resumes polling from the current state — there is no backlog to replay since Spotify's `currently-playing` endpoint always returns the current state.

For `recently-played`, the connector uses the `after` cursor parameter with the last-seen play timestamp to avoid re-processing.

### D9: Dashboard settings section

A new "Spotify" section on the settings page at `/butlers/settings`:

**Components:**
- `SpotifySetupCard` — connection status (connected/disconnected/error), Spotify display name, account type (free/premium), last sync time
- `SpotifyConnectButton` — initiates OAuth PKCE flow
- `SpotifyDisconnectButton` — revokes tokens and disconnects

**API endpoints** (FastAPI router):
- `GET /api/spotify/status` — connection state, user profile
- `POST /api/spotify/oauth/start` — generate PKCE challenge, return authorization URL
- `GET /api/spotify/oauth/callback` — handle OAuth redirect, exchange code for tokens
- `POST /api/spotify/disconnect` — delete tokens from CredentialStore

### D10: Health port allocation

Following the connector port sequence (Telegram bot: 40081, WhatsApp: 40082, live-listener: 40091), Spotify uses port 40083.

## Risks / Trade-offs

**[API deprecation]** Spotify may change or restrict their Web API.
→ **Mitigation:** The endpoints used (`currently-playing`, `recently-played`) are core, stable API surface. Spotify has a strong developer ecosystem and rarely breaks these endpoints. Pin to API version headers when available.

**[Private session mode]** Users can enable "Private Session" in Spotify, which hides currently-playing from the API.
→ **Mitigation:** The connector detects this (API returns `is_playing: false` with no device) and backs off to idle polling. No data is lost — private sessions simply produce gaps in listening history.

**[Free tier limitations]** The `currently-playing` endpoint works for all account types, but some metadata fields may be limited for free-tier accounts.
→ **Mitigation:** The connector handles missing fields gracefully. Core track/artist/album data is always available.

**[Token revocation]** Users may revoke access from Spotify settings, breaking the connector.
→ **Mitigation:** Refresh failure triggers error state with heartbeat notification. Dashboard shows "Re-connect" button. Connector does not crash — it enters error state and waits.

**[Rate limiting]** Though generous, rate limits exist.
→ **Mitigation:** Adaptive polling (60s active, 300s idle) keeps usage at ~1-5 req/min. Exponential backoff on 429 responses with `Retry-After` header respect.

## Open Questions

1. **Should `recently-played` be polled on a separate, slower interval?** It provides historical data (last 50 tracks) that doesn't need real-time polling. Could run every 15 minutes. Currently planned as part of the same poll cycle.

2. **Should podcast/audiobook episodes be ingested?** The same API surface covers podcasts. Excluded from v1 scope but trivial to add — just a payload normalization change.

3. **Context bus signal mapping vocabulary.** When the context bus lands, what signal names should listening events map to? Candidates: `focused` (study/focus playlists), `exercising` (workout playlists), `relaxing` (chill/ambient), `commuting` (travel playlists). This is explicitly NOT part of this change but worth recording for the future.
