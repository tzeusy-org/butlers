## Why

The butler ecosystem currently ingests only text-native channels (Telegram, email, Discord). Ambient voice — the most natural way humans communicate in a home — is entirely invisible to it. A live-listener connector would let butlers hear and respond to spoken requests, observations, and conversations organically, without the friction of picking up a phone or typing. The transcription service (faster-whisper) is self-hosted, keeping raw audio fully local.

## What Changes

- **New connector process** (`connector-live-listener`): a standalone long-running process that captures audio from one or more local microphones, streams chunks to a locally-hosted faster-whisper transcription service, and submits transcribed utterances to the Switchboard via the standard `ingest.v1` pipeline.
- **VAD-based segmentation**: uses Voice Activity Detection (Silero VAD) to cut the continuous audio stream into discrete speech segments at natural utterance boundaries, avoiding fixed-time chunking and wasted transcription of silence.
- **Discretion layer**: an LLM-based filter between transcription output and Switchboard submission that evaluates each utterance (or accumulated paragraph) and decides whether it warrants butler attention — replacing the traditional wake-word approach with contextual, organic relevance assessment.
- **Multi-microphone support**: manages multiple audio input devices (e.g. kitchen, office, living room), each identified as a separate `endpoint_identity`, enabling location-aware context in downstream routing.
- **New `voice` source channel**: extends `SourceChannel` / `SourceProvider` enums to support the new ingestion pathway.

## Capabilities

### New Capabilities
- `connector-live-listener`: Connector profile for ambient voice ingestion — covers audio capture, VAD segmentation, transcription client, discretion filtering, and `ingest.v1` normalization. Follows `connector-base-spec` contract.

### Modified Capabilities
- `connector-base-spec`: Add `voice` to `SourceChannel` enum and `live-listener` to `SourceProvider` enum; add channel-provider pairing validation for `voice`/`live-listener`.
- `connector-source-filter-enforcement`: Define key extraction rules for `voice` source type (likely microphone device identity or room name).

## Impact

- **External dependency**: requires a separately-hosted faster-whisper service with a streaming or chunked HTTP/WebSocket API. This repo does not own the transcription model — only the client.
- **Python dependencies**: `sounddevice` (PortAudio bindings for audio capture), `silero-vad` or equivalent (VAD model), `websockets` or `httpx` (transcription service client).
- **`ingest.v1` envelope model** (`src/butlers/ingest/`): new enum values for `SourceChannel` and `SourceProvider`, plus a new valid channel-provider pair.
- **Source filter enforcement**: new key extraction path for `voice` connector type.
- **Privacy surface**: transcription is local, but transcribed text flows to cloud LLMs via the butler pipeline. The discretion layer is the primary privacy gate — it decides what leaves the local boundary. This is a meaningful trust boundary that needs explicit design attention.
- **Resource footprint**: continuous audio capture + VAD runs permanently on the host; transcription service needs GPU. The connector itself is CPU-light (asyncio event loop + PortAudio callbacks).
