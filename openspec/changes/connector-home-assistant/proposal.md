## Why

The Home butler currently has no real-time awareness of what is happening in the physical home. It can only query Home Assistant when an LLM session is already active — meaning it learns about device state changes, automation triggers, and sensor readings reactively, not proactively. A dedicated connector would stream HA events into the butler ecosystem via the Switchboard, enabling proactive awareness: the butler could notice a door left unlocked, detect an energy spike, or respond to an automation failure without waiting for a user prompt or a scheduled poll. This complements the `home-butler-enhancements` change (which improves the butler's internal processing) by providing the ingestion side.

## What Changes

- **New Home Assistant connector** (`src/butlers/connectors/home_assistant.py`): A standalone connector process that subscribes to the Home Assistant WebSocket API for real-time event streaming (`state_changed`, `automation_triggered`, `call_service`), with REST API polling as a fallback. Normalizes events to `ingest.v1` envelopes and submits to the Switchboard.
- **Aggressive event filtering (discretion layer)**: HA generates hundreds of events per minute. The connector implements domain-based filtering (only entity domains the butler cares about: `light`, `switch`, `sensor`, `climate`, `lock`, `cover`, `binary_sensor`, `automation`, `script`), significance filtering (ignore minor sensor fluctuations below configurable thresholds), and the shared LLM-based discretion layer for borderline events.
- **Switchboard routing registration**: Add `home_assistant` to `SourceChannel` and `SourceProvider`, validate the channel-provider pair in `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Authentication via CredentialStore**: Long-lived HA access token stored in the butler's credential store, resolved at connector startup.
- **Checkpoint semantics**: Last processed HA event ID persisted via `cursor_store` for crash-safe resumption.
- **Contact identity type**: Register `ha_entity_id` as a sender identity convention for HA-originated events (the "sender" is the entity that changed state).

## Capabilities

### New Capabilities
- `connector-home-assistant`: Standalone connector process bridging Home Assistant's WebSocket event stream (with REST polling fallback) into the butler ecosystem. Covers connection management, event subscription, domain filtering, significance filtering, discretion evaluation, ingest.v1 normalization, checkpoint persistence, and health/heartbeat reporting.

### Modified Capabilities
- `connector-base-spec`: Add `home_assistant` to SourceChannel and SourceProvider enums, add valid channel-provider pairing
- `butler-home`: Add connector awareness — the home butler receives routed HA events via the Switchboard and can react proactively to state changes

## Impact

- **Routing contracts** (`roster/switchboard/tools/routing/contracts.py`): Extend `SourceChannel` and `SourceProvider` literals, add to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- **New connector module** (`src/butlers/connectors/home_assistant.py`): WebSocket client, REST fallback poller, event normalization, domain/significance filtering
- **Credential store**: HA long-lived access token stored as `home_assistant:api_token` credential
- **Docker compose**: New `connector-home-assistant` service alongside butler daemons
- **Environment variables**: `HA_BASE_URL`, `HA_ACCESS_TOKEN` (or credential store lookup), `HA_WS_URL` (derived from base URL), domain allowlist, significance thresholds
- **Database**: Connector uses `connectors` schema for filtered event persistence and cursor store (no new tables needed beyond base connector infrastructure)
- **Dependency on Home Assistant**: Requires HA instance accessible over the network (already assumed by the `home_assistant` module in butler.toml)
