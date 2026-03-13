## 1. Enum and Model Extensions

- [ ] 1.1 Add `voice` to `SourceChannel` enum and `live-listener` to `SourceProvider` enum in `src/butlers/ingest/`
- [ ] 1.2 Add `voice`/`live-listener` to the valid channel-provider pair validation
- [ ] 1.3 Add `mic_id` to recognized `source_key_type` values in `SourceFilterEvaluator`
- [ ] 1.4 Write tests for new enum values, pair validation, and `mic_id` key type

## 2. Audio Capture Layer

- [ ] 2.1 Add `sounddevice` dependency to `pyproject.toml`
- [ ] 2.2 Implement `MicPipeline` class: `sounddevice.InputStream` callback → per-mic lock-free ring buffer (16kHz mono 16-bit PCM)
- [ ] 2.3 Implement device enumeration and validation from `LIVE_LISTENER_DEVICES` JSON config
- [ ] 2.4 Implement device reconnection with exponential backoff on disconnect/error
- [ ] 2.5 Write tests for ring buffer overflow behavior, device validation, and reconnection logic

## 3. VAD Segmentation

- [ ] 3.1 Add Silero VAD ONNX model dependency (silero-vad or bundled ONNX)
- [ ] 3.2 Implement VAD state machine (`SILENCE` ↔ `SPEAKING`) with configurable onset/offset thresholds and frame counts
- [ ] 3.3 Implement segment duration bounds (min 300ms discard, max 30s force-split)
- [ ] 3.4 Implement streaming handoff: begin forwarding audio frames to transcription client on speech onset (not waiting for segment complete)
- [ ] 3.5 Write tests for state transitions, min/max segment enforcement, and frame timing

## 4. Transcription Client

- [ ] 4.1 Add `wyoming` Python package dependency to `pyproject.toml`
- [ ] 4.2 Define `TranscriptionClient` abstract interface (`async transcribe(audio: bytes) -> TranscriptionResult`)
- [ ] 4.3 Implement `WyomingTranscriptionClient` (default): persistent TCP connection, Wyoming protocol message flow (`transcribe` → `audio-start` → `audio-chunk`(s) → `audio-stop` → `transcript`), streaming chunks during capture, reconnect with backoff
- [ ] 4.4 Implement `WebSocketTranscriptionClient`: persistent connection, streaming audio chunks during capture, reconnect with backoff
- [ ] 4.5 Implement `HttpTranscriptionClient`: POST complete segment as `audio/wav` or `audio/raw`
- [ ] 4.6 Implement empty/low-confidence transcription discarding with metrics
- [ ] 4.7 Implement graceful degradation: drop segments on service unavailability, health → `degraded`, no buffering
- [ ] 4.8 Write tests for all three client implementations (mock service), failure handling, and confidence filtering

## 5. Discretion Layer

- [ ] 5.1 Implement per-mic sliding context window (bounded by size and time)
- [ ] 5.2 Implement discretion LLM caller: send context window + latest utterance, parse `FORWARD`/`IGNORE` verdict
- [ ] 5.3 Implement fail-open behavior: timeout (3s default) and errors default to `FORWARD`
- [ ] 5.4 Implement configurable LLM backend (`LIVE_LISTENER_DISCRETION_LLM_URL` / `LIVE_LISTENER_DISCRETION_LLM_MODEL`)
- [ ] 5.5 Write tests for window management, verdict parsing, timeout/failure handling, and fail-open logic

## 6. Envelope Construction and Submission

- [ ] 6.1 Implement `ingest.v1` envelope builder for voice utterances (field mapping per spec)
- [ ] 6.2 Implement conversation session manager: session creation on gap expiry, session ID as `external_thread_id`
- [ ] 6.3 Implement synthetic event ID minting (`utt:{device_name}:{unix_ms}`) and idempotency key construction
- [ ] 6.4 Implement checkpoint persistence via `cursor_store` (per-mic last utterance timestamp + session state)
- [ ] 6.5 Write tests for envelope construction, session gap logic, checkpoint save/restore

## 7. Source Filter Integration

- [ ] 7.1 Implement `SourceFilterEvaluator` instantiation per mic pipeline with `connector_type="live-listener"`
- [ ] 7.2 Wire filter gate into pipeline: after transcription, before discretion layer
- [ ] 7.3 Implement `mic_id` key extraction from device name config
- [ ] 7.4 Write tests for filter gate positioning and `mic_id` key extraction

## 8. Connector Process and Lifecycle

- [ ] 8.1 Implement main connector entrypoint: env var parsing, device config loading, multi-mic pipeline orchestration
- [ ] 8.2 Wire health state derivation: aggregate per-mic + transcription service + discretion LLM status
- [ ] 8.3 Wire heartbeat task with per-mic status reporting
- [ ] 8.4 Implement FastAPI health server (`/health`, `/metrics`) on `CONNECTOR_HEALTH_PORT`
- [ ] 8.5 Wire standard `ConnectorMetrics` plus voice-specific counters and histograms (per-stage latency, e2e latency, segment duration, discretion verdicts)
- [ ] 8.6 Write integration test: full pipeline from mock audio → VAD → mock transcription → discretion → ingest submission

## 9. Documentation and Configuration

- [ ] 9.1 Add connector to `src/butlers/connectors/` with module docstring documenting env vars and device config format
- [ ] 9.2 Update connectors README with live-listener quick start
- [ ] 9.3 Add example `LIVE_LISTENER_DEVICES` JSON configuration for common setups
