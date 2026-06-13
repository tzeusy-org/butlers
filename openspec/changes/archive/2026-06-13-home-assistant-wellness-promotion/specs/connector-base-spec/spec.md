# Connector Base — Multi-Channel Emission Delta

## ADDED Requirements

### Requirement: Per-event secondary channel emission

A connector that emits a single source event on more than one channel SHALL satisfy all of the obligations below.
Multi-channel emission is permitted when deterministic per-event
classification warrants it (e.g. domain-channel archival plus semantic-channel
promotion):

- each `(channel, provider)` pair SHALL be a registered canonical pairing;
- each emission SHALL be an independent Switchboard ingest submission,
  deduplicated by the existing channel-inclusive dedup key
  (`event:{channel}:{provider}:{endpoint_identity}:{external_event_id}`);
- the connector checkpoint SHALL remain keyed by
  `(provider, endpoint_identity)` only and SHALL advance once per source
  event, after all of that event's emissions have been submitted;
- heartbeat, health-state derivation, and transport metrics SHALL remain
  connector-level (one transport, one health state — not per-channel).

#### Scenario: Dual emission deduplicates independently per channel

- **WHEN** a connector emits one source event on two channels and later
  replays it
- **THEN** the Switchboard SHALL deduplicate each channel's re-submission
  against that channel's prior dedup key
- **AND** neither channel SHALL record a duplicate ingestion event

#### Scenario: Secondary-channel failure does not corrupt the checkpoint

- **WHEN** the primary-channel submission succeeds but the secondary-channel
  submission fails transiently
- **THEN** the connector SHALL NOT advance the checkpoint past that event
- **AND** the eventual replay SHALL re-submit both channels, with the
  already-accepted channel deduplicated
