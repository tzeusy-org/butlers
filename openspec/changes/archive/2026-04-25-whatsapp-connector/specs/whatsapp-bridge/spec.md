# WhatsApp Bridge (Go Sidecar)

## Purpose

The whatsapp-bridge is a Go binary wrapping the whatsmeow library to implement the WhatsApp Web multidevice protocol. It runs as a subprocess managed by the Python connector or module, exposing a local HTTP API on a Unix socket for event streaming, message sending, and session management. It handles QR code pairing, session persistence to PostgreSQL, and bidirectional message relay.

## ADDED Requirements

### Requirement: WhatsApp Web Protocol via whatsmeow

The bridge uses the whatsmeow Go library to connect to WhatsApp's servers via the multidevice protocol.

#### Scenario: Outbound-only network connections

- **WHEN** the bridge runs
- **THEN** it SHALL connect outbound to WhatsApp's WebSocket endpoint (`wss://web.whatsapp.com`) over TLS
- **AND** no inbound connections from the public internet SHALL be required
- **AND** the bridge SHALL be fully functional inside a tailnet with only outbound internet access

#### Scenario: Multidevice protocol

- **WHEN** the bridge is paired and connected
- **THEN** the user's phone does NOT need to remain online after initial pairing
- **AND** the bridge operates as an independent WhatsApp Web client device

### Requirement: QR Code Pairing Ceremony

Initial device pairing requires the user to scan a QR code with their WhatsApp mobile app.

#### Scenario: CLI pairing mode

- **WHEN** `whatsapp-bridge pair --db-dsn <postgres_dsn>` is invoked
- **THEN** the bridge SHALL display a QR code in the terminal (text-rendered)
- **AND** the QR code SHALL refresh automatically if it expires before scanning
- **AND** once the user scans the QR code with their WhatsApp mobile app, the bridge SHALL store the session to the `whatsapp_sessions` table and exit with code 0

#### Scenario: Pairing timeout

- **WHEN** the QR code is not scanned within 120 seconds
- **THEN** the bridge SHALL exit with code 1 and a message: `"Pairing timed out. Run 'whatsapp-bridge pair' again."`

#### Scenario: Session reuse

- **WHEN** the bridge starts normally (not in pair mode) and a valid session exists in `whatsapp_sessions`
- **THEN** it SHALL reconnect using the stored session without requiring a new QR scan
- **AND** session reconnection SHALL complete within 15 seconds under normal conditions

### Requirement: Session Persistence to PostgreSQL

Session keys and device state are stored in the butler's PostgreSQL database.

#### Scenario: Session table schema

- **WHEN** the bridge stores a session
- **THEN** it SHALL write to the `whatsapp_sessions` table with columns:
  - `id` (UUID, primary key)
  - `phone_number` (TEXT, UNIQUE, E.164 format)
  - `device_id` (TEXT)
  - `session_data` (JSONB, whatsmeow session keys)
  - `paired_at` (TIMESTAMPTZ)
  - `last_seen_at` (TIMESTAMPTZ, updated on each successful connection)
  - `active` (BOOLEAN, default true)

#### Scenario: Session rotation on re-pair

- **WHEN** a new QR pairing is performed for a phone number that already has a session
- **THEN** the old session SHALL be marked `active = false`
- **AND** a new session row SHALL be inserted with `active = true`

#### Scenario: Session invalidation detection

- **WHEN** WhatsApp's servers reject the stored session (user logged out from phone, account banned)
- **THEN** the bridge SHALL mark the session as `active = false`
- **AND** it SHALL emit a `session_invalidated` event on the `/events` SSE stream
- **AND** it SHALL exit with code 2 (distinct from pairing timeout) to signal re-pair needed

### Requirement: Local HTTP API on Unix Socket

The bridge exposes a minimal HTTP API for the Python connector and module to consume.

#### Scenario: Event streaming endpoint

- **WHEN** `GET /events` is called
- **THEN** the bridge SHALL return an SSE (Server-Sent Events) stream
- **AND** each event SHALL be a JSON object with fields: `type` (message type), `message_id`, `chat_jid`, `sender_jid`, `timestamp` (Unix epoch), `content` (type-specific payload), `raw` (full whatsmeow message JSON)
- **AND** the stream SHALL include a `keepalive` event every 30 seconds when no messages arrive

#### Scenario: Send message endpoint

- **WHEN** `POST /send` is called with JSON body `{"recipient": "<jid>", "text": "<message>", "reply_to": "<optional_message_id>"}`
- **THEN** the bridge SHALL send the message via whatsmeow
- **AND** it SHALL return `{"message_id": "<wa_msg_id>", "timestamp": <unix_epoch>}` on success
- **AND** it SHALL return HTTP 503 with `{"error": "not connected"}` if the session is not active

#### Scenario: Status endpoint

- **WHEN** `GET /status` is called
- **THEN** the bridge SHALL return JSON with:
  - `state`: one of `"connected"`, `"connecting"`, `"disconnected"`, `"pair_required"`
  - `phone`: connected phone number (E.164) or null
  - `uptime_s`: seconds since process start
  - `last_event_at`: timestamp of last received WhatsApp event or null

#### Scenario: Disconnect endpoint

- **WHEN** `POST /disconnect` is called
- **THEN** the bridge SHALL gracefully disconnect from WhatsApp (clean logout from multidevice session without invalidating the session keys)
- **AND** it SHALL exit with code 0 within 5 seconds

#### Scenario: Programmatic pairing endpoint

- **WHEN** `POST /pair/start` is called on the HTTP API
- **THEN** the bridge SHALL generate a new WhatsApp QR code via whatsmeow
- **AND** return `{"qr_data_uri": "data:image/png;base64,...", "expires_at": "<RFC3339>"}`
- **AND** the QR code SHALL be rendered as a PNG image encoded in base64
- **AND** return HTTP 409 if an active session already exists

#### Scenario: Pairing poll endpoint

- **WHEN** `GET /pair/poll` is called after a pairing has been started
- **THEN** it SHALL return `{"status": "waiting"}` while QR is active but not scanned
- **AND** return `{"status": "paired", "phone": "<e164>"}` when pairing succeeds
- **AND** return `{"status": "expired"}` if the QR expired without being scanned
- **AND** return HTTP 400 if no pairing is in progress

#### Scenario: Unix socket binding

- **WHEN** the bridge starts with `--listen unix:///tmp/wa-bridge.sock`
- **THEN** it SHALL bind to the specified Unix socket path
- **AND** it SHALL remove any stale socket file before binding
- **AND** the socket file SHALL have mode 0600 (owner-only access)

### Requirement: Message Type Support

The bridge SHALL relay all message types that whatsmeow supports.

#### Scenario: Supported message types

- **WHEN** a WhatsApp message arrives
- **THEN** the bridge SHALL emit events for: text (Conversation, ExtendedTextMessage), images (ImageMessage), video (VideoMessage), audio (AudioMessage), voice notes (PTTMessage), documents (DocumentMessage), stickers (StickerMessage), locations (LocationMessage), contacts (ContactMessage), reactions (ReactionMessage), polls (PollCreationMessage), message deletions (ProtocolMessage revoke), group invites (GroupInviteMessage)

#### Scenario: Media metadata without download

- **WHEN** a media message arrives (image, video, audio, document, sticker)
- **THEN** the bridge SHALL include media metadata in the event (MIME type, file size, filename, caption)
- **AND** the bridge SHALL NOT download media content in v1 (media download is a future phase)
- **AND** the `content` field SHALL include a `media_available: true` flag to indicate downloadable media exists

### Requirement: Dockerfile Multi-Stage Build

The bridge binary is compiled in a Go builder stage and copied into the final Python image.

#### Scenario: Go builder stage

- **WHEN** the Docker image is built
- **THEN** a `golang:1.22-bookworm` builder stage SHALL compile `whatsapp-bridge/cmd/bridge` with `go build -ldflags="-s -w"` (stripped, no debug symbols)
- **AND** the resulting binary SHALL be copied to `/usr/local/bin/whatsapp-bridge` in the final image

#### Scenario: Static binary

- **WHEN** the Go binary is compiled
- **THEN** it SHALL be statically linked (CGO_ENABLED=0) so it runs on the python:3.12-slim base without Go runtime dependencies
- **AND** the binary size SHALL be approximately 15-20 MB

#### Scenario: Build caching

- **WHEN** only Python source code changes (not `whatsapp-bridge/`)
- **THEN** the Go builder stage SHALL be cached and not rebuilt
- **AND** only the Python stages SHALL re-execute

### Requirement: CLI Interface

The bridge binary supports subcommands for different operational modes.

#### Scenario: Run mode (default)

- **WHEN** `whatsapp-bridge --db-dsn <dsn> --listen unix:///tmp/wa-bridge.sock` is invoked
- **THEN** the bridge SHALL connect using the stored session, start the event stream, and run until terminated

#### Scenario: Pair mode

- **WHEN** `whatsapp-bridge pair --db-dsn <dsn>` is invoked
- **THEN** the bridge SHALL enter interactive QR pairing mode and exit after successful pairing

#### Scenario: Status mode

- **WHEN** `whatsapp-bridge status --db-dsn <dsn>` is invoked
- **THEN** the bridge SHALL print the current session state (paired phone, last seen, active) and exit
