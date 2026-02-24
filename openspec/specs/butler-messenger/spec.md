# Messenger Butler Role

## Purpose
The Messenger (port 40104) is the outbound delivery execution plane for Telegram and Email. It does not perform classification or domain logic — it only executes delivery intents routed through Switchboard.

## ADDED Requirements

### Requirement: Messenger Butler Identity and Runtime
The messenger butler is a delivery-only execution plane with no domain logic.

#### Scenario: Identity and port
- **WHEN** the messenger butler is running
- **THEN** it operates on port 40104 with description "Outbound delivery execution plane for Telegram and Email"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `messenger` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the messenger butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `telegram` (bot-only, user disabled, token from `BUTLER_TELEGRAM_TOKEN`), and `email` (bot-only, user disabled, address from `BUTLER_EMAIL_ADDRESS`, password from `BUTLER_EMAIL_PASSWORD`)

### Requirement: Messenger Channel Ownership
The messenger butler owns all external user-channel delivery tools. No other butler may call channel send/reply tools directly.

#### Scenario: Channel tool surface
- **WHEN** the messenger butler receives a `notify.v1` delivery intent
- **THEN** it executes delivery through its owned channel tools: `telegram_send_message`, `telegram_reply_to_message`, `email_send_message`, `email_reply_to_thread`
- **AND** non-messenger butlers must never call channel send/reply tools directly

#### Scenario: Delivery validation and lineage
- **WHEN** processing a delivery request
- **THEN** the messenger validates the `notify.v1` envelope, resolves destination and channel intent (`send` vs `reply`), preserves `origin_butler` and `request_context` lineage, and returns deterministic status/error payloads
- **AND** it must not recursively call `notify` for outbound sends

### Requirement: Messenger Has No Schedules or Skills
The messenger butler is a pure delivery executor with no autonomous behavior.

#### Scenario: No scheduled tasks
- **WHEN** the messenger butler daemon is running
- **THEN** it has no `[[butler.schedule]]` entries and does not execute any cron-driven tasks

#### Scenario: No custom skills
- **WHEN** the messenger butler operates
- **THEN** it has no butler-specific skills directory; it relies solely on its core tool surface and channel modules
