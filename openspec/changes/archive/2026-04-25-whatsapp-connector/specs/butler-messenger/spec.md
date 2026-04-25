# Butler Messenger — WhatsApp Delta

## MODIFIED Requirements

### Requirement: Messenger Butler Identity and Runtime

The Messenger butler is the centralized delivery engine for all outbound channel communication. It loads channel-specific modules for Telegram, Email, and now WhatsApp.

#### Scenario: Module profile

- **WHEN** the Messenger butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `telegram` (bot-only, user disabled, token from `BUTLER_TELEGRAM_TOKEN`), `email` (bot-only, user disabled, address from `BUTLER_EMAIL_ADDRESS`, password from `BUTLER_EMAIL_PASSWORD`), and `whatsapp` (user-scope enabled, `send_tools = true` so write tools are registered, `send_enabled = false` by default so tools refuse to execute until ban risk is assessed)

### Requirement: Messenger Channel Ownership

The Messenger butler owns and operates all delivery channel tool surfaces.

#### Scenario: Channel tool surface

- **WHEN** the Messenger butler receives a delivery request
- **THEN** it executes delivery through its owned channel tools: `telegram_send_message`, `telegram_reply_to_message`, `email_send_message`, `email_reply_to_thread`, `whatsapp_send_message`, `whatsapp_reply_to_message`
- **AND** `whatsapp_send_message` and `whatsapp_reply_to_message` SHALL be present in the tool surface but functionally disabled until `send_enabled = true`

#### Scenario: WhatsApp write exclusivity

- **WHEN** the WhatsApp module is mounted by any butler other than Messenger
- **THEN** it SHALL be configured with `send_tools = false` (the default)
- **AND** no send/reply tools SHALL be registered for that butler
- **AND** only the Messenger butler SHALL have WhatsApp write tools in its MCP schema

### Requirement: Approval-Gated Delivery

High-impact delivery tools require approval before execution. WhatsApp sending to external parties is always gated.

#### Scenario: Gated tools

- **WHEN** the Messenger butler configures its approval gates
- **THEN** `telegram_send_message`, `email_send_message`, `whatsapp_send_message`, and `notify` SHALL be gated
- **AND** `whatsapp_send_message` to the owner's self-chat SHALL bypass approval (same as Telegram DM to self)
- **AND** `whatsapp_send_message` to any external party SHALL always require approval, even when `send_enabled = true`

### Requirement: Rate Limiting

Channel-level rate limits prevent abuse and account throttling.

#### Scenario: Channel-level limits

- **WHEN** the Messenger butler enforces rate limits
- **THEN** the following limits SHALL apply:
  - `telegram.bot`: 30 messages/min
  - `email.bot`: 20 messages/min
  - `whatsapp.user`: 10 messages/min (conservative due to ban risk on unofficial protocol)
- **AND** the WhatsApp limit SHALL be deliberately lower than other channels to minimize ban risk

### Requirement: Retry with Exponential Backoff

Failed deliveries are retried with channel-specific timeouts.

#### Scenario: Per-channel timeouts

- **WHEN** a delivery attempt is made
- **THEN** channel-specific timeouts SHALL apply:
  - `telegram`: 15s
  - `email`: 45s
  - `whatsapp`: 20s (bridge IPC + WhatsApp relay)
  - default: 30s
