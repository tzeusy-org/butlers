# Live Listener Connector

## Purpose
The live-listener connector captures ambient audio from local microphones, segments speech using VAD, streams segments to a locally-hosted faster-whisper transcription service, applies LLM-based discretion filtering, and submits actionable utterances to the Switchboard as `ingest.v1` envelopes. It is the ambient voice ingestion pathway into the butler ecosystem. **End-to-end latency — from speech offset to Switchboard submission — is the primary design constraint.**

## ADDED Requirements

### Requirement: Latency Budget
The connector SHALL treat end-to-end latency as the #1 priority. Every pipeline stage has a latency budget, and the total speech-to-submission time MUST be minimized.

#### Scenario: Pipeline latency targets
- **WHEN** a speech segment completes (VAD detects speech offset)
- **THEN** the total time from speech offset to Switchboard `ingest.v1` submission SHALL target under 2 seconds for a typical utterance (< 5 seconds of speech) on a local network with GPU transcription
- **AND** the latency breakdown budget is: VAD finalization < 100ms, audio handoff to transcription < 50ms, transcription (faster-whisper) < 1000ms (external, best-effort), discretion LLM call < 800ms, envelope construction + MCP submission < 100ms

#### Scenario: Latency-driven design constraints
- **WHEN** choosing between throughput and latency for any pipeline component
- **THEN** the connector MUST prefer the lower-latency option
- **AND** audio MUST be streamed to the transcription service as it is captured (not buffered until segment complete) when the transcription protocol supports streaming
- **AND** the discretion layer MUST use the fastest available LLM model (Haiku-class or local) rather than a more capable but slower model
- **AND** pipeline stages MUST execute sequentially per utterance with no artificial batching or queuing delays

#### Scenario: Latency observability
- **WHEN** an utterance completes the pipeline
- **THEN** the connector MUST record per-stage timing in a Histogram metric `connector_live_listener_stage_latency_seconds` with labels `{mic, stage}` where stage is one of `vad`, `transcription`, `discretion`, `submission`
- **AND** the connector MUST record total end-to-end latency in `connector_live_listener_e2e_latency_seconds` with label `{mic}`

### Requirement: Connector Identity and Role
The live-listener connector bridges local microphone audio into the butler ecosystem as an ambient voice ingestion channel.

#### Scenario: Connector as ambient voice interface
- **WHEN** the live-listener connector runs
- **THEN** it captures audio from one or more configured local microphones, transcribes speech via an external faster-whisper service, and submits actionable utterances to the Switchboard
- **AND** the connector owns the full pipeline from audio capture through discretion filtering; the Switchboard owns routing and classification

#### Scenario: Connector identity
- **WHEN** the live-listener connector starts
- **THEN** `source.channel="voice"`, `source.provider="live-listener"`, and each configured microphone has a distinct `source.endpoint_identity` of the form `"live-listener:mic:{device_name}"`

#### Scenario: Single process, multiple microphone pipelines
- **WHEN** the connector is configured with multiple microphones
- **THEN** it runs as a single OS process with one independent asyncio pipeline per microphone
- **AND** all pipelines share the MCP client, metrics registry, health server, and heartbeat task
- **AND** each pipeline operates independently — a failure in one mic pipeline MUST NOT affect others

### Requirement: Audio Capture
The connector captures raw audio from local microphones using PortAudio via the `sounddevice` library.

#### Scenario: Audio stream configuration
- **WHEN** an audio stream is opened for a microphone
- **THEN** the capture format MUST be 16kHz mono 16-bit signed PCM (Whisper's native input format)
- **AND** sample rate conversion from the device's native rate is handled by PortAudio, not by Python code
- **AND** the stream uses a callback-based `sounddevice.InputStream` that writes frames to a ring buffer

#### Scenario: Ring buffer handoff
- **WHEN** the PortAudio callback fires with new audio frames
- **THEN** frames are written to a per-mic lock-free ring buffer without blocking
- **AND** an asyncio consumer task drains the ring buffer and feeds frames to the VAD
- **AND** if the consumer falls behind, the oldest unread frames are overwritten (stale audio has no value)

#### Scenario: Device enumeration and configuration
- **WHEN** the connector starts
- **THEN** it reads microphone configuration from `LIVE_LISTENER_DEVICES` (JSON list of device specs: `[{"name": "kitchen", "device": "<portaudio_device_name_or_index>"}]`)
- **AND** each configured device is validated against PortAudio's device list
- **AND** if a configured device is not found, the connector logs an ERROR for that device and starts pipelines for remaining valid devices
- **AND** if no valid devices are found, the connector exits with a non-zero status

#### Scenario: Device reconnection
- **WHEN** an active audio device disconnects or produces errors
- **THEN** the connector MUST attempt reconnection with exponential backoff (1s, 2s, 4s, ... capped at 60s)
- **AND** during reconnection, that mic's pipeline is paused — no audio is captured or processed
- **AND** the mic's health contribution transitions to `degraded`
- **AND** on successful reconnection, the pipeline resumes from the live audio stream (no replay of missed audio)

### Requirement: Voice Activity Detection
The connector uses Silero VAD to segment continuous audio into discrete speech segments at natural utterance boundaries.

#### Scenario: VAD model and performance
- **WHEN** the VAD processes audio frames
- **THEN** it uses the Silero VAD ONNX model (< 2MB, CPU-only)
- **AND** each 30ms frame MUST be processed in under 1ms (no GPU required)
- **AND** the VAD runs synchronously within the asyncio consumer task (fast enough to not block the event loop)

#### Scenario: Speech segment state machine
- **WHEN** the VAD evaluates frame-level speech probabilities
- **THEN** it implements a two-state machine: `SILENCE` and `SPEAKING`
- **AND** transition `SILENCE → SPEAKING` occurs when speech probability exceeds `LIVE_LISTENER_VAD_ONSET_THRESHOLD` (default: 0.5) for `LIVE_LISTENER_VAD_ONSET_FRAMES` consecutive frames (default: 3, ~90ms)
- **AND** transition `SPEAKING → SILENCE` occurs when speech probability drops below `LIVE_LISTENER_VAD_OFFSET_THRESHOLD` (default: 0.3) for `LIVE_LISTENER_VAD_OFFSET_FRAMES` consecutive frames (default: 10, ~300ms)

#### Scenario: Segment duration bounds
- **WHEN** a speech segment is being accumulated
- **THEN** segments shorter than `LIVE_LISTENER_MIN_SEGMENT_MS` (default: 300ms) are discarded as noise
- **AND** segments exceeding `LIVE_LISTENER_MAX_SEGMENT_MS` (default: 30000ms) are force-split at the boundary and both halves are sent for transcription independently

#### Scenario: Streaming audio to transcription during speech
- **WHEN** the VAD transitions to `SPEAKING`
- **THEN** audio frames MUST begin streaming to the transcription service immediately (if the transcription protocol supports streaming)
- **AND** the connector MUST NOT wait for the full segment to complete before starting transcription
- **AND** for non-streaming transcription protocols (HTTP POST), frames are accumulated in memory and sent as a batch on speech offset

### Requirement: Transcription Client
The connector sends speech audio to an external faster-whisper service and receives transcribed text.

#### Scenario: Protocol-agnostic interface
- **WHEN** the connector transcribes a speech segment
- **THEN** it uses an abstract `TranscriptionClient` interface with `async transcribe(audio: bytes) -> TranscriptionResult` where `TranscriptionResult` contains `text: str`, `confidence: float`, `language: str`, `duration_s: float`
- **AND** the concrete implementation is selected by `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL` (default: `"wyoming"`)

#### Scenario: Wyoming protocol client (default)
- **WHEN** `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL=wyoming`
- **THEN** the client connects via TCP to `LIVE_LISTENER_TRANSCRIPTION_URL` (default: `tcp://wyoming-faster-whisper.parrot-hen.ts.net:10300` — the `wyoming` namespace on the tailnet)
- **AND** the client uses the Wyoming wire protocol: JSON header line + binary payload framing
- **AND** the ASR message flow is: `transcribe` event (with optional language hint from `LIVE_LISTENER_LANGUAGE`) → `audio-start` event (rate=16000, width=2, channels=1) → one or more `audio-chunk` events with raw PCM payload → `audio-stop` event → server responds with `transcript` event containing recognized text
- **AND** audio chunks are sent as they arrive from the VAD during speech (streaming), not buffered until segment complete
- **AND** the TCP connection is persistent per mic pipeline (reconnect on failure with backoff)
- **AND** the client uses the `wyoming` Python package for message construction and parsing

#### Scenario: Wyoming streaming transcription
- **WHEN** the Wyoming server supports streaming mode
- **THEN** the client reads `transcript-chunk` events as they arrive during audio streaming (for future use in early discretion evaluation)
- **AND** the final `transcript` event after `audio-stop` is used as the authoritative transcription result

#### Scenario: WebSocket streaming client
- **WHEN** `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL=websocket`
- **THEN** the client connects to `LIVE_LISTENER_TRANSCRIPTION_URL` (e.g., `ws://localhost:8765/transcribe`)
- **AND** audio chunks are streamed over the WebSocket as they arrive from the VAD (not buffered until segment complete)
- **AND** the connection is persistent per mic pipeline (reconnect on failure with backoff)

#### Scenario: HTTP POST fallback client
- **WHEN** `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL=http`
- **THEN** the client POSTs the complete audio segment to `LIVE_LISTENER_TRANSCRIPTION_URL` as `audio/wav` or `audio/raw` with sample rate headers
- **AND** each segment is a separate HTTP request

#### Scenario: Transcription service unavailability
- **WHEN** the transcription service is unreachable or returns errors
- **THEN** the connector drops the speech segment and increments `connector_live_listener_transcription_failures_total{mic, error_type}`
- **AND** the connector MUST NOT buffer audio segments for later transcription (memory risk for unbounded streams)
- **AND** the connector's health state transitions to `degraded` while the service is down
- **AND** reconnection uses exponential backoff (1s, 2s, 4s, ... capped at 30s)

#### Scenario: Empty transcription handling
- **WHEN** the transcription service returns empty text or text below a confidence threshold (`LIVE_LISTENER_MIN_CONFIDENCE`, default: 0.3)
- **THEN** the segment is silently discarded (no ingest submission, no discretion evaluation)
- **AND** `connector_live_listener_transcription_discarded_total{mic, reason}` is incremented (reason: `"empty"` or `"low_confidence"`)

### Requirement: Discretion Layer
The live-listener uses the shared discretion layer (`butlers.connectors.discretion`) to evaluate transcribed utterances in context and decide whether they warrant butler attention. See `connector-base-spec` for the full shared discretion contract.

#### Scenario: Shared discretion module
- **WHEN** the live-listener connector uses the discretion layer
- **THEN** it imports from `butlers.connectors.discretion` (shared module)
- **AND** it uses `DiscretionConfig(env_prefix="LIVE_LISTENER_")` for per-connector env var resolution

#### Scenario: Sliding context window
- **WHEN** an utterance is transcribed
- **THEN** it is appended to a per-mic sliding context window
- **AND** the window retains the last `LIVE_LISTENER_DISCRETION_WINDOW_SIZE` utterances (default: 10) or utterances within the last `LIVE_LISTENER_DISCRETION_WINDOW_SECONDS` (default: 300), whichever produces fewer entries
- **AND** each entry in the window includes: transcribed text, timestamp, and source name (mic name)

#### Scenario: Discretion evaluation with weight
- **WHEN** a new utterance is added to the context window
- **THEN** the discretion layer sends the context window plus the latest utterance to the configured LLM
- **AND** the LLM MUST respond with a structured verdict: `FORWARD` (with a one-line reason) or `IGNORE`
- **AND** the LLM call uses `LIVE_LISTENER_DISCRETION_LLM_MODEL` (default: `gemma3:12b`) and `LIVE_LISTENER_DISCRETION_LLM_URL`
- **AND** all voice utterances use `weight=1.0` (no sender identity available for ambient audio), meaning the LLM is always called and failures always fail-open

#### Scenario: Discretion verdicts
- **WHEN** the discretion LLM responds `FORWARD`
- **THEN** the utterance proceeds to `ingest.v1` envelope construction and Switchboard submission
- **AND** the discretion reason is included in `payload.raw.discretion_reason`
- **WHEN** the discretion LLM responds `IGNORE`
- **THEN** the utterance is discarded and `connector_live_listener_discretion_total{mic, verdict="ignore"}` is incremented

#### Scenario: Discretion layer failure
- **WHEN** the discretion LLM call fails (timeout, connection error, malformed response)
- **THEN** the connector defaults to `FORWARD` (fail-open, because voice weight=1.0 >= weight_fail_open threshold)
- **AND** `connector_live_listener_discretion_failures_total{mic, error_type}` is incremented
- **AND** the failed evaluation is logged at WARNING level

#### Scenario: Discretion latency constraint
- **WHEN** the discretion LLM is called
- **THEN** the call MUST use a timeout of `LIVE_LISTENER_DISCRETION_TIMEOUT_S` (default: 3 seconds)
- **AND** if the timeout is exceeded, the utterance is treated as `FORWARD` (fail-open) and the timeout is logged

#### Scenario: Privacy boundary
- **WHEN** `LIVE_LISTENER_DISCRETION_LLM_URL` points to a local LLM endpoint
- **THEN** transcribed text does not leave the local network until an utterance is forwarded to the Switchboard
- **WHEN** `LIVE_LISTENER_DISCRETION_LLM_URL` is empty or points to a cloud endpoint
- **THEN** ALL transcribed text (including ignored utterances) is sent to the cloud LLM for evaluation

### Requirement: ingest.v1 Field Mapping
Each forwarded utterance is normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping
- **WHEN** a forwarded utterance is constructed as an `ingest.v1` envelope
- **THEN** the mapping is:
  - `source.channel` = `"voice"`
  - `source.provider` = `"live-listener"`
  - `source.endpoint_identity` = `"live-listener:mic:{device_name}"` (from mic config)
  - `event.external_event_id` = `"utt:{device_name}:{unix_ms}"` (synthetic, monotonic)
  - `event.external_thread_id` = conversation session ID (see Conversation Sessions requirement)
  - `event.observed_at` = timestamp of speech segment offset (when the utterance was fully spoken)
  - `sender.identity` = `"ambient"` (no speaker identification in v1)
  - `payload.raw` = `{"transcript": str, "confidence": float, "duration_s": float, "mic": str, "language": str, "discretion_reason": str}`
  - `payload.normalized_text` = the transcribed text
  - `control.idempotency_key` = `"voice:{endpoint_identity}:{unix_ms}:{content_hash[:8]}"`
  - `control.policy_tier` = `"default"`
  - `control.ingestion_tier` = `"full"`

#### Scenario: Synthetic event ID uniqueness
- **WHEN** `event.external_event_id` is constructed
- **THEN** the `unix_ms` component is the millisecond-precision timestamp of speech segment offset
- **AND** combined with `device_name`, this is unique across microphones and monotonically increasing per mic
- **AND** the dedup key uses both timestamp and content hash for safety against clock skew

### Requirement: Conversation Sessions
The connector groups temporally related utterances into conversation sessions for thread context.

#### Scenario: Session creation
- **WHEN** an utterance is forwarded and no active session exists for that mic, or the silence gap since the last forwarded utterance exceeds `LIVE_LISTENER_SESSION_GAP_S` (default: 120 seconds)
- **THEN** a new session is created with ID `"voice:{device_name}:{session_start_unix_ms}"`
- **AND** the session ID is used as `event.external_thread_id` for all utterances in the session

#### Scenario: Session continuity
- **WHEN** an utterance is forwarded within `LIVE_LISTENER_SESSION_GAP_S` of the previous forwarded utterance on the same mic
- **THEN** it uses the existing session's `external_thread_id`

#### Scenario: Session expiry
- **WHEN** no utterance is forwarded on a mic for longer than `LIVE_LISTENER_SESSION_GAP_S`
- **THEN** the session is considered expired (no explicit close action)
- **AND** the next forwarded utterance starts a new session

### Requirement: Checkpoint Semantics
The connector persists checkpoint state for operational continuity, not for replay.

#### Scenario: Checkpoint contents
- **WHEN** the connector saves a checkpoint
- **THEN** it writes per-mic state to `cursor_store`: `{"last_utterance_ts": unix_ms, "session_id": str | null, "session_last_ts": unix_ms | null}`
- **AND** the checkpoint is keyed by `(provider="live-listener", endpoint_identity=<mic endpoint>)`

#### Scenario: Checkpoint timing
- **WHEN** an utterance is successfully submitted to the Switchboard (accepted or duplicate)
- **THEN** the checkpoint for that mic is updated with the utterance's timestamp

#### Scenario: No replay on restart
- **WHEN** the connector restarts
- **THEN** it loads the checkpoint to restore session state (resuming an active session if within the gap window)
- **AND** it does NOT attempt to replay missed audio — audio is ephemeral by design

### Requirement: Health State Derivation
The connector reports health based on device and service availability.

#### Scenario: Health states
- **WHEN** the connector's health is queried
- **THEN** `error` when no audio devices are capturing (all failed)
- **AND** `degraded` when any mic pipeline has a failed device, the transcription service is unreachable, or the discretion LLM is unreachable
- **AND** `healthy` when all mic pipelines are active and both transcription and discretion services are responsive

#### Scenario: Per-mic health in heartbeat
- **WHEN** a heartbeat is assembled
- **THEN** the `status.error_message` includes per-mic status when degraded (e.g., `"mic:kitchen=healthy, mic:bedroom=device_disconnected"`)

### Requirement: Prometheus Metrics
The connector exports voice-specific metrics in addition to the standard `ConnectorMetrics`.

#### Scenario: Voice-specific counters
- **WHEN** the connector processes audio
- **THEN** it exports: `connector_live_listener_segments_total{mic, outcome}` (outcome: `transcribed`, `discarded_noise`, `discarded_silence`, `transcription_failed`), `connector_live_listener_discretion_total{mic, verdict}` (verdict: `forward`, `ignore`, `error_forward`), `connector_live_listener_transcription_failures_total{mic, error_type}`, `connector_live_listener_discretion_failures_total{mic, error_type}`, `connector_live_listener_transcription_discarded_total{mic, reason}`

#### Scenario: Voice-specific histograms
- **WHEN** the connector completes pipeline stages
- **THEN** it exports: `connector_live_listener_stage_latency_seconds{mic, stage}` (stage: `vad`, `transcription`, `discretion`, `submission`), `connector_live_listener_e2e_latency_seconds{mic}`, `connector_live_listener_segment_duration_seconds{mic}`

### Requirement: Environment Variables
Configuration via environment variables extending the base connector variables.

#### Scenario: Required variables
- **WHEN** the live-listener connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=live-listener`, `CONNECTOR_CHANNEL=voice`, and `LIVE_LISTENER_DEVICES` (JSON device list) MUST be set
- **AND** `LIVE_LISTENER_TRANSCRIPTION_URL` MUST be set (default: `tcp://localhost:10300` for Wyoming; no universal default — the transcription service location is deployment-specific)

#### Scenario: Optional audio variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_VAD_ONSET_THRESHOLD` (default: 0.5), `LIVE_LISTENER_VAD_OFFSET_THRESHOLD` (default: 0.3), `LIVE_LISTENER_VAD_ONSET_FRAMES` (default: 3), `LIVE_LISTENER_VAD_OFFSET_FRAMES` (default: 10), `LIVE_LISTENER_MIN_SEGMENT_MS` (default: 300), `LIVE_LISTENER_MAX_SEGMENT_MS` (default: 30000), `LIVE_LISTENER_MIN_CONFIDENCE` (default: 0.3) are optionally configurable

#### Scenario: Optional transcription variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL` (default: `"wyoming"`), `LIVE_LISTENER_LANGUAGE` (default: `"en"`) are optionally configurable

#### Scenario: Optional discretion variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_DISCRETION_LLM_URL` (default: empty = butler ecosystem default), `LIVE_LISTENER_DISCRETION_LLM_MODEL` (default: fastest available), `LIVE_LISTENER_DISCRETION_TIMEOUT_S` (default: 3), `LIVE_LISTENER_DISCRETION_WINDOW_SIZE` (default: 10), `LIVE_LISTENER_DISCRETION_WINDOW_SECONDS` (default: 300) are optionally configurable

#### Scenario: Optional session variables
- **WHEN** the connector starts
- **THEN** `LIVE_LISTENER_SESSION_GAP_S` (default: 120) is optionally configurable

### Requirement: Idempotency and Safety
The connector guarantees at-least-once delivery with synthetic event IDs.

#### Scenario: Dedup identity
- **WHEN** a voice utterance is submitted
- **THEN** the idempotency key is `"voice:{endpoint_identity}:{unix_ms}:{content_hash[:8]}"` combining timestamp and content hash
- **AND** duplicate accepted ingest responses are treated as success, not failures

#### Scenario: No replay guarantee
- **WHEN** the connector restarts after a crash
- **THEN** audio captured during downtime is lost (audio is ephemeral)
- **AND** the first utterance after recovery uses a fresh timestamp, avoiding collision with pre-crash utterances
- **AND** checkpoint is loaded to restore session state only
