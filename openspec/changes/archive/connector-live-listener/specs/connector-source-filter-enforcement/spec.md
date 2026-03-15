# Connector Source Filter Enforcement — Delta for Live Listener

## ADDED Requirements

### Requirement: Voice Connector Key Extraction
The live-listener connector extracts the microphone device name as the filter key, enabling location-based source filtering.

#### Scenario: Key extraction for mic_id filters
- **WHEN** a transcribed utterance from a voice connector is evaluated against source filters
- **THEN** the connector extracts the device name from the mic pipeline configuration as the key value for `source_key_type="mic_id"`
- **AND** the key value matches the `name` field from the `LIVE_LISTENER_DEVICES` JSON configuration (e.g., `"kitchen"`, `"office"`)
- **AND** the key value is always lowercase

#### Scenario: Valid source key types for live-listener connector
- **WHEN** source filters are configured for a live-listener connector
- **THEN** the only valid `source_key_type` is `"mic_id"`
- **AND** filters with any other `source_key_type` are skipped with a one-time WARNING log per filter ID

#### Scenario: SourceFilterEvaluator instantiation for voice
- **WHEN** the live-listener connector starts
- **THEN** it instantiates `SourceFilterEvaluator(connector_type="live-listener", endpoint_identity=<configured mic endpoint identity>, db_pool=<shared switchboard pool>)` for each mic pipeline
- **AND** performs the initial filter load before beginning audio capture

#### Scenario: Filter gate position in voice pipeline
- **WHEN** a transcribed utterance is ready for discretion evaluation
- **THEN** source filter evaluation runs AFTER transcription but BEFORE the discretion layer
- **AND** this ordering ensures filtered-out utterances do not consume discretion LLM calls
- **AND** a blocked utterance is discarded with counter increment and checkpoint advance
