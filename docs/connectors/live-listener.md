# Live Listener Connector
> **Purpose:** Capture ambient audio from physical microphones, detect speech, transcribe it, and submit voice events to the Switchboard for routing.
> **Audience:** Contributors.
> **Prerequisites:** [Connector Interface](interface.md), [Connector Metrics](metrics.md).

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

## Related Pages

- [Connector Interface](interface.md) -- Shared connector contract and lifecycle
- [Connector Metrics](metrics.md) -- Standard Prometheus instrumentation for connectors
- [Attachment Handling](attachment-handling.md) -- How binary content flows through connectors
- [Heartbeat Protocol](heartbeat.md) -- Connector liveness signaling
