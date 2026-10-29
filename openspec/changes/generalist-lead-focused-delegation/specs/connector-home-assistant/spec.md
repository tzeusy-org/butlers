## MODIFIED Requirements

### Requirement: Connector Identity and Role

The Home Assistant connector SHALL bridge real-time HA events into the butler ecosystem as a device-state ingestion channel and SHALL declare the channel a device feed. The Switchboard SHALL own ingestion for the events it submits; an event wakes reasoning only through a declared subscription admitted with origin `obligation` (`REQ-butler-switchboard-007`), and no classification or routing step SHALL select a butler for it. Device names, notes and other fields a person other than the owner can set SHALL be stamped untrusted.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Connector as home automation event interface
- **WHEN** the Home Assistant connector runs
- **THEN** it subscribes to the HA WebSocket event stream for real-time state change events and submits significant events to the Switchboard
- **AND** the connector owns the full pipeline from event subscription through discretion filtering; the Switchboard owns ingestion and the feed path, and no classification or routing step exists
- **AND** an event that matches a declared subscription is admitted with origin `obligation` and that subscription's capability set, never as an owner or third-party goal

#### Scenario: Connector identity
- **WHEN** the Home Assistant connector starts
- **THEN** `source.channel = "home_assistant"`, `source.provider = "home_assistant"`, and `source.endpoint_identity` SHALL be `"home_assistant:<ha_host>:<ha_port>"` derived from the configured HA base URL

#### Scenario: Single process, single HA instance
- **WHEN** the connector is configured
- **THEN** it connects to exactly one HA instance as a single OS process
- **AND** it shares the MCP client, metrics registry, health server, and heartbeat task across the WebSocket and REST fallback paths
