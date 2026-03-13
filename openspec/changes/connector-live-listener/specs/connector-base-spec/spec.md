# Connector Base Spec — Delta for Live Listener

## MODIFIED Requirements

### Requirement: ingest.v1 Envelope Schema
The `ingest.v1` envelope is the canonical format for all messages entering the butler ecosystem. It is a Pydantic model (`IngestEnvelopeV1`) with five required sub-models validated at parse time.

#### Scenario: Source identity (IngestSourceV1)
- **WHEN** `source` is populated
- **THEN** `channel` is a `SourceChannel` enum value (`telegram`, `slack`, `email`, `api`, `mcp`, `voice`), `provider` is a `SourceProvider` enum value (`telegram`, `slack`, `gmail`, `imap`, `internal`, `live-listener`), and `endpoint_identity` is a non-empty string uniquely identifying the connector instance (e.g., `"gmail:user:alice@gmail.com"`, `"telegram:bot:mybot"`, `"live-listener:mic:kitchen"`)

#### Scenario: Channel-provider pair validation
- **WHEN** `source.channel` and `source.provider` are set
- **THEN** valid pairings are enforced: `telegram`/`telegram`, `email`/`gmail`, `email`/`imap`, `api`/`internal`, `mcp`/`internal`, `voice`/`live-listener`
- **AND** invalid pairings fail Pydantic validation
