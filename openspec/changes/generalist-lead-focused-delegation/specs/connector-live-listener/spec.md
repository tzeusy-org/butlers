## MODIFIED Requirements

### Requirement: Connector Identity and Role

The live-listener connector SHALL bridge local microphone audio into the butler ecosystem as an ambient voice ingestion channel. The Switchboard SHALL own ingestion and goal admission for submitted utterances; no classification or routing step SHALL select a butler for them.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Connector as ambient voice interface
- **WHEN** the live-listener connector runs
- **THEN** it captures audio from one or more configured local microphones, transcribes speech via an external faster-whisper service, and submits actionable utterances to the Switchboard
- **AND** the connector owns the full pipeline from audio capture through discretion filtering; the Switchboard owns ingestion and goal admission, and no classification or routing step exists

#### Scenario: Connector identity
- **WHEN** the live-listener connector starts
- **THEN** `source.channel="voice"`, `source.provider="live-listener"`, and each configured microphone has a distinct `source.endpoint_identity` of the form `"live-listener:mic:{device_name}"`

#### Scenario: Single process, multiple microphone pipelines
- **WHEN** the connector is configured with multiple microphones
- **THEN** it runs as a single OS process with one independent asyncio pipeline per microphone
- **AND** all pipelines share the MCP client, metrics registry, health server, and heartbeat task
- **AND** each pipeline operates independently — a failure in one mic pipeline MUST NOT affect others
