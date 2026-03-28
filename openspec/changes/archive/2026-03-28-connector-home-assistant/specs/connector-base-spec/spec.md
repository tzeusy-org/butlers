# Connector Base Spec — Home Assistant Channel Registration

## MODIFIED Requirements

### Requirement: ingest.v1 Envelope Schema
The `ingest.v1` envelope is the canonical format for all messages entering the butler ecosystem. It is a Pydantic model (`IngestEnvelopeV1`) with five required sub-models validated at parse time.

#### Scenario: Source identity (IngestSourceV1)
- **WHEN** `source` is populated
- **THEN** `channel` is a `SourceChannel` enum value (`telegram`, `slack`, `email`, `api`, `mcp`, `voice`, `home_assistant`), `provider` is a `SourceProvider` enum value (`telegram`, `slack`, `gmail`, `imap`, `internal`, `live-listener`, `home_assistant`), and `endpoint_identity` is a non-empty string uniquely identifying the connector instance (e.g., `"gmail:user:alice@gmail.com"`, `"telegram:bot:mybot"`, `"live-listener:mic:kitchen"`, `"home_assistant:ha-host:8123"`)

#### Scenario: Channel-provider pair validation
- **WHEN** `source.channel` and `source.provider` are set
- **THEN** valid pairings are enforced: `telegram`/`telegram`, `email`/`gmail`, `email`/`imap`, `api`/`internal`, `mcp`/`internal`, `voice`/`live-listener`, `home_assistant`/`home_assistant`
- **AND** invalid pairings fail Pydantic validation
