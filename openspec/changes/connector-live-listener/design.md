## Context

The butler ecosystem ingests external events exclusively through connector processes that normalize to `ingest.v1` and submit to the Switchboard. Existing connectors (Telegram, Gmail, Discord) all process discrete, text-native events with provider-assigned IDs. A live-listener connector introduces a fundamentally different ingestion pattern: continuous audio → segmented speech → transcribed text → discretionary forwarding. The connector must bridge this continuous-to-discrete gap while conforming to the established connector contract.

The transcription service (faster-whisper) is hosted separately and is not part of this codebase. This connector owns the audio capture, VAD segmentation, transcription client, and discretion filtering layers.

## Goals / Non-Goals

**Goals:**
- Capture audio from multiple local microphones with low-latency, crash-resilient buffering
- Segment continuous audio into natural utterances using VAD
- Stream speech segments to an external faster-whisper service and receive transcriptions
- Apply LLM-based discretion filtering to decide which transcriptions warrant butler attention
- Submit passing utterances as standard `ingest.v1` envelopes to the Switchboard
- Conform fully to `connector-base-spec` (health checks, metrics, heartbeats, source filters)
- Keep raw audio and transcription entirely local; only discretion-approved text crosses to cloud LLMs

**Non-Goals:**
- Hosting or managing the faster-whisper service (external dependency)
- Speaker identification / diarization (future capability, not v1)
- Audio recording or storage (audio is ephemeral — processed and discarded)
- Outbound voice synthesis / TTS (butlers respond through existing channels)
- Real-time DSP, noise cancellation, or beamforming (defer to hardware/OS-level solutions)
- Wake word detection (replaced by discretion layer by design)

## Decisions

### 1. Single process, multiple audio pipelines

**Decision:** One connector process manages all configured microphones. Each mic runs its own independent asyncio pipeline (capture → VAD → transcribe → discretion → ingest) but shares the MCP client, metrics, and health server.

**Why not per-mic processes:** Operational complexity. N microphones would mean N connector registrations, N health ports, N heartbeat streams. A single process with per-mic `endpoint_identity` values (e.g., `live-listener:mic:kitchen`, `live-listener:mic:office`) gives location-awareness without fleet sprawl.

**Why not a single merged pipeline:** Different rooms produce independent conversations. Merging audio streams would confuse VAD boundaries and transcription context.

### 2. Audio capture via sounddevice with ring buffer handoff

**Decision:** Use `sounddevice.InputStream` (PortAudio binding) with a callback that writes raw PCM frames into a per-mic lock-free ring buffer. An asyncio consumer task drains the buffer and feeds frames to the VAD.

**Why ring buffer:** The PortAudio callback runs on a real-time audio thread — it must not block. A ring buffer decouples the real-time capture from Python's asyncio event loop. If the consumer falls behind, the oldest frames are overwritten (acceptable — stale audio has no value).

**Why sounddevice over pyaudio:** sounddevice is actively maintained, has a cleaner async-friendly API, and wraps the same PortAudio underneath. pyaudio is effectively unmaintained.

**Audio format:** 16kHz mono 16-bit PCM (the native input format for Whisper models). Resampling from device native rate happens in sounddevice/PortAudio, not in Python.

### 3. VAD-first segmentation with Silero VAD

**Decision:** Every audio frame passes through Silero VAD before any transcription. The VAD state machine detects speech onset → speech offset transitions, accumulating frames into discrete speech segments. Only complete speech segments are sent to transcription.

**State machine:**
- `SILENCE` → speech probability exceeds onset threshold → `SPEAKING`
- `SPEAKING` → speech probability drops below offset threshold for N consecutive frames → `SILENCE` (segment complete)
- Minimum segment duration (e.g., 300ms) prevents short noise bursts from triggering transcription
- Maximum segment duration (e.g., 30s) forces a segment break for very long utterances, preventing unbounded memory growth

**Why Silero VAD:** ~1MB ONNX model, runs on CPU in microseconds per 30ms frame, well-tested, no GPU needed. The alternative (WebRTC VAD) is simpler but less accurate and produces more false positives.

### 4. Transcription client: protocol-agnostic interface with Wyoming default

**Decision:** Define an abstract `TranscriptionClient` interface with `transcribe(audio_segment: bytes) -> str` semantics. Ship three implementations: Wyoming protocol (default), WebSocket streaming, and HTTP chunked POST.

**Why abstract interface:** Different faster-whisper deployments expose different protocols. An interface lets users plug in their service without modifying the connector.

**Why Wyoming as default:** Wyoming is the standard protocol for voice services in the Home Assistant / Rhasspy ecosystem, and `wyoming-faster-whisper` is the most common local deployment target. It uses raw TCP with JSON+binary framing (`transcribe` → `audio-start` → `audio-chunk`(s) → `audio-stop` → `transcript`), which maps directly to the VAD state machine's speech lifecycle. Wyoming supports streaming transcription (server sends `transcript-chunk` events during audio), aligning with the latency-first design. The `wyoming` Python package provides client-side primitives.

**Why WebSocket / HTTP as alternatives:** Some faster-whisper deployments (whisper-streaming, custom REST services) don't implement Wyoming. WebSocket gives true streaming for non-Wyoming services. HTTP POST is the simplest fallback for any transcription API.

**Configuration:**
```
LIVE_LISTENER_TRANSCRIPTION_URL=tcp://localhost:10300  # Wyoming default
LIVE_LISTENER_TRANSCRIPTION_PROTOCOL=wyoming  # or "websocket", "http"
```

### 5. Discretion layer: sliding window with LLM evaluation

**Decision:** Transcribed utterances accumulate in a per-mic sliding context window (last N utterances or T seconds, whichever is smaller). On each new utterance, the discretion layer evaluates the window and decides whether the latest utterance (in context) warrants butler attention.

**Why sliding window, not per-utterance:** Isolated sentences lack context. "Yes, tomorrow at 3" is meaningless alone but actionable in context of "Should I book the plumber?" The window gives the LLM conversational context for better relevance assessment.

**Why not bulk/periodic evaluation:** Latency. Batching introduces delay between speech and butler response. Per-utterance evaluation (with context window) gives near-real-time forwarding.

**Discretion prompt structure:**
```
You are a discretion filter for a home voice assistant. You hear everything
said in the house. Your job is to decide whether the latest utterance is
something a butler/assistant should act on.

Forward if: direct requests, questions, task assignments, reminders,
scheduling, actionable observations, emergencies.

Do NOT forward: casual conversation, background TV/music, rhetorical
questions, thinking aloud, private conversations not directed at the system.

Respond with only: FORWARD or IGNORE
If FORWARD, include a one-line reason.

[Context window of recent utterances]
Latest: "{utterance}"
```

**LLM backend for discretion:** Configurable. Default to the butler ecosystem's configured LLM (cloud). For privacy-sensitive deployments, support a local LLM endpoint (e.g., llama.cpp server) via environment variable override.

```
LIVE_LISTENER_DISCRETION_LLM_URL=  # empty = use default butler LLM
LIVE_LISTENER_DISCRETION_LLM_MODEL=haiku  # fast, cheap model preferred
```

**Discretion is the privacy gate:** This is the point where text either stays local or crosses to cloud. The design makes this explicit and configurable.

### 6. ingest.v1 envelope mapping

**Decision:** Map voice utterances to the existing `ingest.v1` schema:

| Field | Value |
|-------|-------|
| `source.channel` | `"voice"` (new enum value) |
| `source.provider` | `"live-listener"` (new enum value) |
| `source.endpoint_identity` | `"live-listener:mic:{device_name}"` |
| `event.external_event_id` | Synthetic: `"utt:{mic}:{unix_ms}"` — monotonic, unique per utterance |
| `event.external_thread_id` | Conversation session ID (see Decision 7) |
| `event.observed_at` | Timestamp of speech segment end (when utterance was complete) |
| `sender.identity` | `"ambient"` (no speaker ID in v1; future: diarization) |
| `payload.raw` | `{"transcript": str, "confidence": float, "duration_s": float, "mic": str, "language": str}` |
| `payload.normalized_text` | The transcribed text |
| `control.idempotency_key` | `"voice:{endpoint_identity}:{unix_ms}:{content_hash[:8]}"` |
| `control.policy_tier` | `"default"` |
| `control.ingestion_tier` | `"full"` |

**Why `sender.identity = "ambient"`:** Without speaker diarization, we can't attribute speech to a specific person. The Switchboard and downstream butlers should treat voice ingests as coming from the ambient home environment, not from a specific identified sender. This is a known limitation documented for v1.

### 7. Conversation sessions for thread context

**Decision:** The connector maintains a per-mic "conversation session" — a sliding time window that groups related utterances into a logical thread. Utterances within the same session share an `external_thread_id`, giving downstream butlers conversational context.

**Session lifecycle:**
- A new session starts on the first utterance after a silence gap exceeding `LIVE_LISTENER_SESSION_GAP_S` (default: 120 seconds)
- Session ID format: `"voice:{mic}:{session_start_unix_ms}"`
- All utterances within a session share this thread ID
- Sessions have no explicit "end" — they expire via inactivity

**Why session-based threading:** Without this, every utterance would be an isolated message. The Switchboard's history loading for voice channel (configurable, similar to realtime channels) would have no thread structure to work with.

### 8. Checkpoint semantics for continuous audio

**Decision:** Checkpoint tracks the high-water-mark timestamp of the last successfully ingested utterance, per mic. Unlike discrete-event connectors, there is no "replay from checkpoint" — missed audio during downtime is gone.

**What checkpoint enables:**
- Heartbeat reporting (last activity timestamp per mic)
- Metrics continuity across restarts (monotonic counters reset, but session state is restored)
- Dedup safety: on restart, the first utterance after recovery gets a fresh timestamp, so no collision with pre-crash utterances

**What checkpoint does NOT enable:**
- Replay of missed audio (audio is ephemeral by design)
- Gap detection ("you missed 5 minutes of audio" — not trackable without recording)

### 9. Source filter key type for voice

**Decision:** The source filter key type for voice connectors is `"mic_id"` — the device name / room identifier. This allows filtering by location (e.g., "only listen to the office mic, ignore bedroom").

Filter evaluation runs after transcription but before discretion, so filtered-out utterances don't consume LLM calls.

### 10. Graceful degradation when transcription service is unavailable

**Decision:** If the faster-whisper service is unreachable, the connector continues capturing audio and running VAD, but drops speech segments with a metric increment (`connector_transcription_failures_total`). It does NOT buffer audio for later transcription (memory risk for an unbounded stream). Health state transitions to `degraded`.

When the service recovers, the next speech segment is transcribed normally. The gap is accepted — ambient voice is best-effort by nature.

## Risks / Trade-offs

**[Privacy boundary at discretion layer]** → All transcribed text reaches the discretion LLM. If using a cloud LLM, this means every spoken word in the house reaches an external service, even if the discretion layer decides to ignore it. **Mitigation:** Support local LLM for discretion (`LIVE_LISTENER_DISCRETION_LLM_URL`). Document the privacy implications clearly. The discretion prompt + model choice is the primary privacy control surface.

**[No speaker identification]** → `sender.identity = "ambient"` means butlers can't distinguish who's speaking. A guest saying "turn off the lights" is indistinguishable from the owner. **Mitigation:** Acceptable for v1. Speaker diarization is a future enhancement that can be added to the pipeline without architectural changes (it slots in after transcription, before envelope construction).

**[Discretion layer latency]** → Each utterance triggers an LLM call for discretion. With a cloud model, this adds 200-500ms+ per utterance. **Mitigation:** Use a fast/cheap model (Haiku-class). The latency is acceptable because the butler's response will take seconds anyway. For lower latency, use a local LLM.

**[Discretion layer accuracy]** → The LLM might forward irrelevant chatter or miss genuine requests. False positives waste butler cycles; false negatives frustrate users. **Mitigation:** The discretion prompt is the primary tuning surface. Start conservative (forward more, miss less) and tighten based on usage patterns. The sliding context window helps significantly vs. isolated utterance evaluation.

**[Continuous resource consumption]** → Unlike event-driven connectors that idle between messages, this connector continuously consumes CPU (audio capture + VAD) and network (heartbeats, transcription) while the host is running. **Mitigation:** VAD prevents wasted transcription of silence. Audio capture + VAD is minimal CPU (~1-2% per mic). The transcription service is the heavy resource consumer, and it's only active during speech.

**[PortAudio device management]** → Audio devices can disconnect, change sample rates, or conflict with other applications. **Mitigation:** Device enumeration at startup with validation. Automatic reconnection on device loss with configurable retry. Health state transitions to `degraded` on device failure.

## Open Questions

1. **Discretion prompt tuning:** The initial prompt is a starting point. Should the discretion layer be configurable per mic (e.g., office mic has different relevance criteria than kitchen mic)?

2. **Multi-speaker overlap:** When two people talk simultaneously, VAD will produce a single merged segment. Faster-whisper may struggle with overlapping speech. Is this acceptable for v1, or do we need segment-level handling?

3. **Transcription language:** Hardcode English, or support `LIVE_LISTENER_LANGUAGE` for multilingual homes? Faster-whisper supports language hints.

4. **Mic device specification format:** Device names vary by OS and audio subsystem. Should we use PortAudio device indices, names, or a configuration layer that maps room names to devices?
