# Live Listener Connector
> **Purpose:** Capture ambient audio from physical microphones, detect speech, transcribe it, and submit voice events to the Switchboard for routing.
> **Audience:** Contributors.
> **Prerequisites:** [Connector Interface](overview.md), [Connector Metrics](metrics.md).

## Overview

The live-listener connector (`src/butlers/connectors/live_listener/`) is an always-on audio ingestion pipeline. It captures raw PCM audio from one or more PortAudio microphone devices, runs Voice Activity Detection (VAD) to isolate speech segments, transcribes them via a configurable speech-to-text backend, applies a multi-stage filtering pipeline (filter gate, pre-filter heuristics, LLM-based discretion), and submits accepted utterances to the Switchboard via the MCP `ingest` tool. Each microphone runs as an independent asyncio pipeline within a single process, sharing the MCP client, connector metrics, health server, and heartbeat task.

## Pipeline Architecture

Each microphone pipeline executes the following stages:

```
Audio Capture -> VAD -> Transcription -> Filter Gate -> Pre-filter -> Discretion -> Envelope -> Ingest
```

1. **Audio Capture** (`audio.py`): Opens a PortAudio stream via `sounddevice`, feeding 30ms PCM frames (16 kHz, 16-bit mono) to a callback that dispatches to the asyncio loop.
2. **VAD** (`vad.py`): Silero ONNX model (< 2 MB, CPU-only) in a two-state machine (SILENCE/SPEAKING) with configurable thresholds. Short segments are discarded; long segments are force-split.
3. **Transcription** (`transcription.py`): Three backends -- Wyoming (default, persistent TCP), WebSocket, and HTTP. Low-confidence results are discarded. Unreachable services cause drops with exponential backoff (max 30s).
4. **Filter Gate** (`filter_gate.py`): DB-driven ingestion rules scoped to `mic_id`. Fails open without a DB pool.
5. **Pre-filter** (`prefilter.py`): Heuristic gate before the expensive discretion LLM call.
6. **Discretion** (shared `discretion.py`): LLM-based FORWARD/IGNORE filter with a sliding context window. Fails open on errors.
7. **Envelope** (`envelope.py`): Builds voice event envelope with device, timestamp, session ID, transcript, confidence, and discretion reason.
8. **Ingest**: Calls the Switchboard `ingest` MCP tool and persists a voice checkpoint on success.

## Session Tracking

The `ConversationSession` class (`session.py`) groups utterances from the same microphone into sessions. When silence exceeds `LIVE_LISTENER_SESSION_GAP_S`, a new session ID is generated. Session state is persisted via voice checkpoints (`checkpoint.py`) and restored on startup so sessions survive connector restarts.

## Configuration

All settings are loaded from environment variables. Required:

| Variable | Description |
|---|---|
| `SWITCHBOARD_MCP_URL` | SSE endpoint URL for Switchboard MCP server |
| `LIVE_LISTENER_DEVICES` | JSON list of mic device specs: `[{"name": "kitchen", "device": "hw:0,0"}]` |
| `LIVE_LISTENER_TRANSCRIPTION_URL` | Transcription service URL |

Key optional variables include VAD thresholds (`LIVE_LISTENER_VAD_ONSET_THRESHOLD` default 0.5, `_OFFSET_THRESHOLD` default 0.3), segment bounds (`_MIN_SEGMENT_MS` default 300, `_MAX_SEGMENT_MS` default 30000), transcription protocol (`wyoming`/`websocket`/`http`), language hint, confidence threshold, discretion timeout and window parameters, session gap duration, and health port (default 40091).

## Health and Observability

The connector exposes a FastAPI health server (default port 40091):

- **`GET /health`**: Returns derived health state with per-mic details and uptime.
- **`GET /metrics`**: Prometheus-format metrics from `prometheus_client`.

Health state derivation:
- **healthy**: All mic pipelines active and all services responsive.
- **degraded**: Any mic has a failed device, or transcription/discretion is unhealthy.
- **error**: No audio devices are capturing (all failed or no devices configured).

Per-mic `LiveListenerMetrics` track segment counts by outcome, stage latency histograms, end-to-end latency, failure counts by error type, and pre-filter decisions.

## Resilience

Each mic pipeline runs in a retry loop with exponential backoff. A single mic failure never takes down the whole connector. Transcription services fail by dropping segments (never buffering). Discretion and filter gate fail open. Backoff resets on successful reconnection.

## Verification

To confirm the live-listener connector is operating as described:

```bash
# 1. Health endpoint reports all mic pipelines active
curl -s http://localhost:40091/health | python3 -m json.tool
# Expected: {"state": "healthy", ...} with per-mic pipeline status;
#           "degraded" if a mic or transcription service has failed

# 2. VAD segments are being detected and reaching the transcription stage
curl -s http://localhost:40091/metrics | grep live_listener_segment
# Expected: live_listener_segment_total counter increasing as speech is detected;
#           outcome labels: forwarded, prefilter_rejected, discretion_ignored, error

# 3. Accepted utterances appear in ingestion_events as voice channel events
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_channel, source_provider, received_at
   FROM switchboard.ingestion_events
   WHERE source_channel='voice'
   ORDER BY received_at DESC LIMIT 5;"
# Expected: rows with source_channel='voice' and source_provider='live_listener'
#           appear after speech is captured and passes the discretion filter

# 4. Session continuity: utterances within the gap window share a session ID
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_endpoint_identity, source_thread_identity AS session_id, COUNT(*) AS utterances
   FROM switchboard.ingestion_events
   WHERE source_channel='voice'
   GROUP BY source_endpoint_identity, source_thread_identity
   ORDER BY MAX(received_at) DESC LIMIT 5;"
# Expected: consecutive utterances within the session gap window share the same session_id

# 5. Single-mic failure does not bring down the whole connector
# (Observable via health endpoint — other mics should remain healthy)
curl -s http://localhost:40091/health | python3 -m json.tool | grep -E "mic_id|state|error"
# Expected: failed mic shows state=error; others remain active; top-level state=degraded (not error)
```

## Related Pages

- [Connector Interface](overview.md) -- Shared connector contract and lifecycle
- [Connector Metrics](metrics.md) -- Standard Prometheus instrumentation for connectors
- [Attachment Handling](attachment-handling.md) -- How binary content flows through connectors
- [Heartbeat Protocol](heartbeat.md) -- Connector liveness signaling
