# Butler Switchboard — WhatsApp Delta

## ADDED Requirements

### Requirement: WhatsApp Source Channel and Provider Registration

The Switchboard SHALL accept WhatsApp as a valid ingestion source.

#### Scenario: SourceChannel registration

- **WHEN** the `SourceChannel` literal type is defined
- **THEN** it SHALL include `"whatsapp_user_client"` as a valid channel value
- **AND** this channel SHALL be distinct from any future `"whatsapp_bot"` channel

#### Scenario: SourceProvider registration

- **WHEN** the `SourceProvider` literal type is defined
- **THEN** it SHALL include `"whatsapp"` as a valid provider value

#### Scenario: Channel-provider pair validation

- **WHEN** an ingest envelope arrives with `source.channel = "whatsapp_user_client"`
- **THEN** the Switchboard SHALL validate that `source.provider` is `"whatsapp"` (the only allowed provider for this channel)
- **AND** the pair SHALL be registered in `_ALLOWED_PROVIDERS_BY_CHANNEL` as `{"whatsapp_user_client": frozenset({"whatsapp"})}`

### Requirement: WhatsApp Ingest Event Shape

WhatsApp ingest envelopes follow the canonical `ingest.v1` schema with WhatsApp-specific field semantics.

#### Scenario: WhatsApp envelope acceptance

- **WHEN** a WhatsApp ingest envelope arrives at the Switchboard
- **THEN** it SHALL be accepted if it conforms to `ingest.v1` with:
  - `source.channel = "whatsapp_user_client"`
  - `source.provider = "whatsapp"`
  - `source.endpoint_identity` matching pattern `"whatsapp:<e164_phone>"`
  - `sender.identity` containing a WhatsApp JID
  - `control.idempotency_key` matching pattern `"whatsapp:<endpoint>:<message_id>"`

### Requirement: WhatsApp Interactive Lifecycle

WhatsApp is an interactive channel — messages routed from WhatsApp SHALL include interactive delivery guidance.

#### Scenario: Interactive channel recognition

- **WHEN** a message arrives from `source.channel = "whatsapp_user_client"`
- **THEN** the Switchboard SHALL recognize it as an interactive channel
- **AND** routing guidance SHALL instruct the target butler to use `notify()` for delivery responses
- **AND** the notify channel mapping SHALL resolve `"whatsapp_user_client"` to the `"whatsapp"` delivery channel on the Messenger butler

#### Scenario: WhatsApp conversation history strategy

- **WHEN** the Switchboard loads conversation history for routing context on a WhatsApp message
- **THEN** it SHALL use the `"realtime"` strategy (union of last 15 minutes OR last 30 messages, whichever is more)

### Requirement: Channel Key Alignment

The codebase has pre-wired `"whatsapp"` in `HISTORY_STRATEGY` and `_INTERACTIVE_ROUTE_CHANNELS`, but the connector uses channel `"whatsapp_user_client"`. Both keys must be present.

#### Scenario: HISTORY_STRATEGY includes both keys

- **WHEN** `HISTORY_STRATEGY` is defined in pipeline.py
- **THEN** it SHALL include both `"whatsapp": "realtime"` (existing) AND `"whatsapp_user_client": "realtime"` (new)
- **AND** the `"whatsapp"` key SHALL remain for future WhatsApp bot connector use

#### Scenario: _INTERACTIVE_ROUTE_CHANNELS includes user client channel

- **WHEN** `_INTERACTIVE_ROUTE_CHANNELS` is defined in daemon.py
- **THEN** it SHALL include `"whatsapp_user_client"` in addition to the existing `"whatsapp"` entry

### Requirement: notify() Channel Mapping for WhatsApp

The `notify()` core tool must support WhatsApp as a delivery channel.

#### Scenario: WhatsApp delivery via notify

- **WHEN** `notify(channel="whatsapp", message="...", recipient="...")` is called
- **THEN** the daemon SHALL resolve the `"whatsapp"` channel to the WhatsApp module's `whatsapp_send_message` tool on the Messenger butler
- **AND** the approval gate SHALL apply (owner auto-approve, external requires approval)
