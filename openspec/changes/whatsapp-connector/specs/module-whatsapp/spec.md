# WhatsApp Module

## Purpose

The WhatsApp module provides MCP tools for sending and replying to WhatsApp messages via the Go bridge sidecar. It uses a **two-layer gating model**: `send_tools` (registration-time, following the email module's existing pattern) controls whether send tools are registered at all, and `send_enabled` (runtime) controls whether registered tools actually execute. Only the Messenger butler sets `send_tools = true`; all other butlers omit send tools entirely. When sending is enabled, messages to external parties are gated by the existing Approvals module (owner auto-approve, external requires approval — the standard `gate.py` pattern used by Telegram and email).

## ADDED Requirements

### Requirement: Two-Layer Send Gating

The WhatsApp module uses config-driven conditional tool registration (following the email module's `send_tools` pattern) and a runtime execution gate.

#### Scenario: No send tools registered (default)

- **WHEN** the WhatsApp module starts with default configuration (`send_tools = false`)
- **THEN** no send/reply tools SHALL be registered in the MCP schema
- **AND** the module provides no outbound capability — pure context access via pipeline only

#### Scenario: Send tools registered but disabled (Messenger default)

- **WHEN** the WhatsApp module starts with `send_tools = true` and `send_enabled = false`
- **THEN** `whatsapp_send_message` and `whatsapp_reply_to_message` SHALL be registered in the MCP schema
- **AND** invoking any send/reply tool SHALL return `{"error": "WhatsApp sending is disabled. Set modules.whatsapp.send_enabled=true in butler.toml to enable. WARNING: Sending via unofficial WhatsApp clients carries ban risk."}`
- **AND** the module SHALL NOT initiate any outbound WhatsApp communication

#### Scenario: Send tools registered and enabled

- **WHEN** the WhatsApp module starts with `send_tools = true` and `send_enabled = true`
- **THEN** send/reply tools SHALL execute normally via the Go bridge sidecar
- **AND** all sends SHALL pass through the standard approval gate (`gate.py` wrapper)

### Requirement: WhatsApp Send/Reply Tools

The module registers MCP tools for send and reply operations when `send_tools = true`.

#### Scenario: Tool registration

- **WHEN** the WhatsApp module registers tools with `send_tools = true`
- **THEN** the following tools SHALL be available in the MCP schema:
  - `whatsapp_send_message` (send a message to a chat by JID or phone number)
  - `whatsapp_reply_to_message` (reply to a specific message in a chat)

#### Scenario: Send message via Go bridge

- **WHEN** `whatsapp_send_message` is invoked with `recipient` (phone or JID) and `text`, and `send_enabled = true`
- **THEN** the module SHALL POST to the Go bridge's `/send` endpoint on the Unix socket
- **AND** the bridge SHALL relay the message to WhatsApp via whatsmeow
- **AND** the response SHALL include the WhatsApp message ID and delivery status

#### Scenario: Reply to message via Go bridge

- **WHEN** `whatsapp_reply_to_message` is invoked with `chat_jid`, `message_id`, and `text`, and `send_enabled = true`
- **THEN** the module SHALL POST to the Go bridge's `/send` endpoint with the `reply_to` field set
- **AND** the bridge SHALL send a quoted reply in the target chat

### Requirement: Approval Gating via Standard Gate (No Custom Logic)

WhatsApp send tools are gated by the existing approval module — the same `gate.py` wrapper used by Telegram and email. The module does NOT implement its own recipient checking.

#### Scenario: Owner auto-approve (handled by gate.py)

- **WHEN** `whatsapp_send_message` is invoked and the approval gate resolves the recipient to a contact with the `owner` role
- **THEN** the gate SHALL auto-approve the action and execute the tool immediately
- **AND** this includes the owner's self-chat ("Message Yourself")
- **AND** no WhatsApp-module-specific approval logic is needed — `gate.py` already handles owner detection via `_resolve_target_contact()` → role check

#### Scenario: External party approval (handled by gate.py)

- **WHEN** `whatsapp_send_message` is invoked and the recipient is NOT the owner
- **THEN** the approval gate SHALL intercept the call and create a `PendingAction` with status `pending`
- **AND** the tool SHALL return `{"status": "pending_approval", "action_id": "...", "message": "..."}`
- **AND** the message SHALL NOT be sent until explicitly approved via the dashboard

#### Scenario: Standing approval rules for trusted contacts

- **WHEN** a standing approval rule exists for `whatsapp_send_message` matching a specific recipient
- **THEN** the gate SHALL auto-approve and execute immediately
- **AND** the rule's `use_count` SHALL be incremented

### Requirement: Butler Mount Modes via send_tools Config

The WhatsApp module supports readonly vs write modes via `send_tools` config flag (following the email module's existing pattern — no base-class change needed).

#### Scenario: Messenger butler mounts with write capability

- **WHEN** the Messenger butler loads the WhatsApp module with `send_tools = true`
- **THEN** send/reply tools SHALL be registered (subject to `send_enabled` runtime gate)
- **AND** the Messenger butler is the ONLY butler that SHALL set `send_tools = true`

#### Scenario: Other butlers mount without send tools

- **WHEN** any non-Messenger butler loads the WhatsApp module with default config (`send_tools = false`)
- **THEN** no send/reply tools SHALL be registered for that butler
- **AND** the butler can still access WhatsApp conversation history via the pipeline (ingested by the connector)

### Requirement: WhatsAppConfig with Credential Scoping

Configuration supports send gating, conditional tool registration, and credential scoping.

#### Scenario: Config structure

- **WHEN** `[modules.whatsapp]` is configured in butler.toml
- **THEN** it SHALL include:
  - `send_tools` (bool, default `false`) — controls whether send tools are registered at all (registration-time)
  - `send_enabled` (bool, default `false`) — controls whether registered send tools actually execute (runtime)
  - `bridge_socket` (str, default `/tmp/wa-bridge.sock`) — Unix socket path to Go bridge
- **AND** `[modules.whatsapp.user]` with `enabled` (default `true`) and `session_env` (default `"WHATSAPP_USER_SESSION"`)

#### Scenario: Config validation

- **WHEN** `send_enabled = true` and `send_tools = false` are both set
- **THEN** the module SHALL raise a configuration error at startup: `"Cannot enable sending without send_tools=true. Set send_tools=true to register send tools."`

### Requirement: Credential Resolution

WhatsApp credentials are resolved exclusively from owner entity_info (DB-only, no env fallback for session material).

#### Scenario: Startup credential resolution

- **WHEN** `on_startup` is called with a database connection
- **THEN** the module SHALL resolve `whatsapp_phone` from `resolve_owner_entity_info(pool, "whatsapp_phone")` for endpoint identity
- **AND** session keys SHALL NOT be resolved by the module — the Go bridge manages its own session from the `whatsapp_sessions` table

#### Scenario: Missing credentials

- **WHEN** `whatsapp_phone` cannot be resolved from entity_info
- **THEN** the module SHALL log a warning and continue startup in degraded mode
- **AND** send/reply tools SHALL return an error explaining that WhatsApp credentials are not configured

### Requirement: Go Bridge Sidecar Lifecycle

The module manages the whatsapp-bridge Go binary as a subprocess via `BridgeSubprocessManager`.

#### Scenario: Bridge startup

- **WHEN** the WhatsApp module's `on_startup` is called
- **THEN** it SHALL start the `whatsapp-bridge` binary via `BridgeSubprocessManager` with `--db-dsn` and `--listen unix://<bridge_socket>`
- **AND** it SHALL wait for the bridge's `/status` endpoint to report `"connected"` before completing startup
- **AND** startup SHALL timeout after 30 seconds if the bridge fails to connect

#### Scenario: Bridge health monitoring

- **WHEN** the module is running
- **THEN** the `BridgeSubprocessManager` SHALL periodically poll the bridge's `/status` endpoint (every 30s)
- **AND** if the bridge reports `"disconnected"` or fails to respond, the module SHALL log an error and set itself to degraded mode

#### Scenario: Bridge crash and restart

- **WHEN** the bridge subprocess exits unexpectedly
- **THEN** the `BridgeSubprocessManager` SHALL restart it with jittered exponential backoff (initial 5s, max 300s)
- **AND** exit code 2 (session invalidated) SHALL trigger a log warning and set degraded mode without restart (re-pair needed)

#### Scenario: Bridge shutdown

- **WHEN** `on_shutdown` is called
- **THEN** the module SHALL POST to the bridge's `/disconnect` endpoint for graceful shutdown
- **AND** if the bridge does not exit within 5 seconds, it SHALL be terminated via SIGTERM

#### Scenario: Bridge binary not found

- **WHEN** the `whatsapp-bridge` binary is not found in `$PATH`
- **THEN** the module SHALL raise `RuntimeError` with message: `"whatsapp-bridge binary not found. Build with EXTRAS=whatsapp or install manually."`

### Requirement: No Custom Database Tables

The WhatsApp module does not own database tables. Session persistence is managed by the Go bridge; message storage is managed by the connector.

#### Scenario: Migration revisions

- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `None`
