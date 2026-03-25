# WhatsApp User Client Connector

## Purpose

The WhatsApp User Client connector provides **readonly access to the user's personal WhatsApp account** via a Go bridge sidecar wrapping whatsmeow. Its sole purpose is contextualization: by ingesting the user's DMs, group chats, and broadcast messages, butlers gain awareness of life events, commitments, relationships, and facts flowing through WhatsApp without requiring explicit manual upload. The connector reads messages the user can see; it never sends, replies, or modifies anything on the user's WhatsApp account. All outbound messaging goes through the WhatsApp module loaded by the Messenger butler.

## ADDED Requirements

### Requirement: Readonly Contextualization Role

The user client connector exists to give butlers passive awareness of the user's WhatsApp activity. It is strictly ingestion-only and readonly.

#### Scenario: Readonly access to user's WhatsApp

- **WHEN** the WhatsApp user client connector runs
- **THEN** it SHALL ingest messages visible to the user's personal WhatsApp account
- **AND** it SHALL never send messages, reply, react, edit, delete, or modify anything on the user's account
- **AND** all ingested messages SHALL flow through Switchboard for classification and routing to specialist butlers for contextual awareness

#### Scenario: Contextualization use cases

- **WHEN** the user's WhatsApp messages are ingested
- **THEN** butlers can automatically learn about: plans mentioned in group chats, recommendations from friends, appointments shared in DMs, financial discussions, relationship events, and any other life context visible in WhatsApp
- **AND** this eliminates the need for the user to manually forward messages or tell the butler about events already visible in their WhatsApp

#### Scenario: Separation from module output tools

- **WHEN** both the connector and the WhatsApp module are active
- **THEN** the connector handles readonly ingestion of the user's WhatsApp activity
- **AND** the WhatsApp module (loaded by Messenger butler only) handles outbound messaging when enabled
- **AND** each has distinct responsibilities: the connector never writes, the module never ingests

### Requirement: Go Bridge Event Streaming

The connector consumes a real-time event stream from the whatsapp-bridge Go sidecar.

#### Scenario: Bridge subprocess management

- **WHEN** the connector starts
- **THEN** it SHALL start the `whatsapp-bridge` binary as a subprocess with `--db-dsn <postgres_dsn> --listen unix:///tmp/wa-bridge.sock`
- **AND** it SHALL wait for the bridge's `/status` endpoint to report `"connected"` before entering the event loop
- **AND** startup SHALL timeout after 60 seconds if the bridge fails to connect (longer than module timeout to allow for QR re-pair)

#### Scenario: Live event subscription via SSE

- **WHEN** the bridge is connected
- **THEN** the connector SHALL consume the bridge's `GET /events` SSE endpoint
- **AND** each SSE event SHALL contain a JSON payload with the WhatsApp message fields (message ID, chat JID, sender JID, timestamp, message type, content)
- **AND** the connector SHALL process events as they arrive in near-real-time

#### Scenario: Bridge reconnection

- **WHEN** the SSE connection to the bridge drops or the bridge process exits
- **THEN** the connector SHALL restart the bridge subprocess with jittered exponential backoff (initial 5s, max 300s)
- **AND** on reconnection, it SHALL replay from the last checkpoint

#### Scenario: Bridge binary not found

- **WHEN** the `whatsapp-bridge` binary is not found in `$PATH`
- **THEN** the connector SHALL raise `RuntimeError` with message: `"whatsapp-bridge binary not found. Build with EXTRAS=whatsapp or install manually."`

### Requirement: Scope of Ingestion

The connector ingests from all message sources visible to the user's WhatsApp account.

#### Scenario: Ingested message sources

- **WHEN** the WhatsApp user client connector is running
- **THEN** it SHALL ingest from: direct messages (1:1 chats), group chats, and broadcast messages visible to the account

#### Scenario: Message ordering

- **WHEN** messages arrive
- **THEN** per-chat message ordering SHALL be preserved where practical
- **AND** cross-chat global ordering is not guaranteed

#### Scenario: Inbound and outbound messages

- **WHEN** the user sends or receives a message on WhatsApp
- **THEN** both inbound (messages from others) and outbound (messages the user sent) SHALL be ingested
- **AND** this gives butlers full conversational context, not just one side

### Requirement: ingest.v1 Field Mapping

Each WhatsApp message is normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping

- **WHEN** a WhatsApp message is normalized
- **THEN** the mapping SHALL be:
  - `source.channel` = `"whatsapp_user_client"`
  - `source.provider` = `"whatsapp"`
  - `source.endpoint_identity` = `"whatsapp:<e164_phone>"` (the user's WhatsApp phone number in E.164 format)
  - `event.external_event_id` = WhatsApp message ID (stable per-message identifier)
  - `event.external_thread_id` = chat JID (group JID or peer JID for DMs)
  - `event.observed_at` = message timestamp (RFC3339 with timezone)
  - `sender.identity` = sender's WhatsApp JID
  - `payload.raw` = full whatsmeow message JSON (serialized protobuf fields)
  - `payload.normalized_text` = extracted text or media annotation
  - `control.idempotency_key` = `"whatsapp:<endpoint_identity>:<message_id>"`
  - `control.policy_tier` = `"default"`

#### Scenario: Message type normalization

- **WHEN** a WhatsApp message is normalized to `payload.normalized_text`
- **THEN** the following strategies SHALL apply:
  - `Conversation` / `ExtendedTextMessage` → use text verbatim
  - `ImageMessage` → caption if present, else `[image]`
  - `VideoMessage` → caption if present, else `[video]`
  - `AudioMessage` / `PTTMessage` → `[voice message]` or `[audio]`
  - `DocumentMessage` → `FileName` and caption
  - `StickerMessage` → `[sticker]`
  - `LocationMessage` → `[location: lat, lon, name]`
  - `ContactMessage` → `[contact: DisplayName]`
  - `ReactionMessage` → `[reaction: emoji to message_id]`
  - `PollCreationMessage` → `[poll: question — option1, option2, ...]`
  - `ProtocolMessage` (revoke) → `[message deleted]`

### Requirement: Per-Chat Buffering

The connector accumulates messages per chat before flushing to Switchboard, reducing submission frequency for high-volume group chats.

#### Scenario: Chat buffer configuration

- **WHEN** the connector is configured
- **THEN** it SHALL maintain a `ChatBuffer` per chat JID with configurable `flush_interval_s` (default 600s) and `buffer_max_messages` (default 50)

#### Scenario: Time-based flush

- **WHEN** a chat buffer's age exceeds `flush_interval_s`
- **THEN** all buffered messages for that chat SHALL be flushed (normalized and submitted to Switchboard)
- **AND** the buffer SHALL be cleared and the checkpoint advanced

#### Scenario: Size-based flush

- **WHEN** a chat buffer reaches `buffer_max_messages`
- **THEN** the buffer SHALL be force-flushed regardless of time elapsed

### Requirement: Discretion Layer Integration

The WhatsApp user client connector uses the shared discretion layer with identity-based weight resolution to filter noise before Switchboard ingestion.

#### Scenario: Discretion gate position

- **WHEN** a message passes the ingestion policy gates (connector-scope and global-scope)
- **THEN** the discretion layer SHALL evaluate the message text before normalization and Switchboard submission
- **AND** the discretion gate SHALL be active when a `DiscretionDispatcher` is available (requires a DB pool for catalog resolution)

#### Scenario: Per-chat evaluators

- **WHEN** the connector processes messages from multiple chats
- **THEN** each chat JID SHALL get its own `DiscretionEvaluator` instance with an independent context window
- **AND** evaluators SHALL be lazily created on first message from each chat, all sharing the same `DiscretionDispatcher` instance
- **AND** the evaluator source name SHALL be `"wa:{chat_jid}"`

#### Scenario: Identity-based weight resolution

- **WHEN** a message is evaluated by the discretion layer
- **THEN** the connector SHALL resolve the sender's weight via `ContactWeightResolver` using `(type="whatsapp_jid", value=sender_jid)`
- **AND** the weight SHALL map sender roles to tiers: owner=1.0 (bypass LLM), family/close-friends=0.9, known contact=0.7, unknown sender=0.3
- **AND** if the weight resolver has no DB access or the sender JID is unknown, weight SHALL default to 1.0

#### Scenario: Discretion IGNORE handling

- **WHEN** the discretion layer returns `IGNORE`
- **THEN** the message SHALL be recorded in `FilteredEventBuffer` with `filter_reason="discretion:IGNORE"` and not submitted to Switchboard

### Requirement: Credential Resolution

WhatsApp credentials are resolved exclusively from owner entity_info (DB-only).

#### Scenario: Required credentials

- **WHEN** the WhatsApp user client connector starts
- **THEN** `whatsapp_phone` SHALL be resolvable from `resolve_owner_entity_info(pool, "whatsapp_phone")`
- **AND** the Go bridge SHALL manage its own session keys from the `whatsapp_sessions` table
- **AND** no env var fallback SHALL exist for session material (DB-only for security)

#### Scenario: Endpoint identity resolution

- **WHEN** the connector resolves its endpoint identity
- **THEN** it SHALL format as `"whatsapp:<e164_phone>"` using the resolved phone number
- **AND** this endpoint identity SHALL be used for checkpoint storage, heartbeat registration, and filtered event attribution

### Requirement: Bounded Backfill on Startup

The connector supports optional historical message replay on startup to fill gaps from downtime.

#### Scenario: Backfill window configuration

- **WHEN** `CONNECTOR_BACKFILL_WINDOW_H` is configured (e.g., 24 for last 24 hours)
- **THEN** the connector SHALL request the Go bridge to replay messages from the configured hour window before switching to live event subscription

#### Scenario: Backfill deduplication

- **WHEN** backfill processes historical messages
- **THEN** any duplicates SHALL be harmlessly caught by Switchboard's ingest dedup via the idempotency key

### Requirement: Privacy, Consent, and Data Minimization

Because this connector reads a user's personal WhatsApp messages, strict privacy safeguards are required.

#### Scenario: Explicit user consent

- **WHEN** the WhatsApp user client connector is deployed
- **THEN** explicit user consent SHALL be obtained before enabling account-wide ingestion (the QR pairing ceremony constitutes physical consent)

#### Scenario: Ingestion-only — no outbound

- **WHEN** the user client connector processes messages
- **THEN** it is strictly ingestion-only — it SHALL never send messages, reply, or perform any write action on the user's WhatsApp account
- **AND** outbound messaging goes through the WhatsApp module loaded by the Messenger butler

#### Scenario: [TARGET-STATE] Scope controls

- **WHEN** the connector is configured
- **THEN** optional chat/sender allow/deny lists SHALL be available to limit ingestion scope

### Requirement: Checkpoint and Durability

The connector persists crash-safe resume checkpoints.

#### Scenario: Checkpoint storage

- **WHEN** a flush cycle completes
- **THEN** the connector SHALL persist the latest processed message offset to `switchboard.connector_registry` keyed by `(connector_type="whatsapp_user_client", endpoint_identity="whatsapp:<phone>")`

#### Scenario: Restart-safe resume

- **WHEN** the connector restarts
- **THEN** it SHALL load the checkpoint and request the Go bridge to resume from that offset
- **AND** any duplicate messages SHALL be caught by Switchboard dedup

### Requirement: Environment Variables

#### Scenario: Required variables

- **WHEN** the WhatsApp user client connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=whatsapp`, `CONNECTOR_CHANNEL=whatsapp_user_client` SHALL be set
- **AND** `endpoint_identity` SHALL be resolved at startup from owner entity_info

#### Scenario: Optional variables

- **WHEN** the connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_BACKFILL_WINDOW_H`, `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120), and `CONNECTOR_HEALTH_PORT` (default 40082) SHALL be optionally configurable

### Requirement: Deployment Model

The user client connector runs as a dedicated Docker service, separate from butler daemons.

#### Scenario: Docker compose service

- **WHEN** the WhatsApp user client connector is deployed
- **THEN** it SHALL run as a service named `connector-whatsapp-user` in docker-compose
- **AND** it SHALL use the same image as other services, built with `EXTRAS: whatsapp` arg
- **AND** it SHALL depend on `log-init`, `migrations`, and `switchboard` (healthy)
- **AND** it SHALL be on `db` and `backend` networks

#### Scenario: Health endpoint

- **WHEN** the connector is running
- **THEN** it SHALL expose a health endpoint on `CONNECTOR_HEALTH_PORT` (default 40082) with `/health` (JSON) and `/metrics` (Prometheus)
