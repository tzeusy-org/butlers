# Voice Module Research Draft

Status: **Draft** (Research Only — no implementation)
Last updated: 2026-02-19
Author: Research pass, butlers-962.2
Depends on: `src/butlers/modules/base.py`, `docs/connectors/interface.md`

---

## 1. Purpose

This document captures research into adding voice interaction capabilities to
butlers. Voice input/output (STT + TTS) enables hands-free butler usage: a user
can speak commands or queries, and the butler can respond with synthesized speech.

Secondary use case: automatic transcription of Telegram voice messages sent by
the user, feeding them into the normal pipeline without manual typing.

This is a **research-only** deliverable. No implementation code accompanies this
doc. The goal is to identify the best API/library approaches, map data models to
existing butler conventions, and surface hardware requirements and risk factors
for a future implementation ticket.

---

## 2. Scope and Interaction Models

Two distinct interaction models exist for a Voice module:

### 2.1 Voice Message Transcription (Passive / Async)

The butler receives a pre-recorded audio file (e.g., a Telegram voice message,
a WhatsApp PTT note, or a file upload), transcribes it, and injects the
transcript into the normal text pipeline. This is **batch STT** — no real-time
constraint.

Requirements: high accuracy, multilingual support, accepts OGG/MP3/WAV/M4A.
Latency budget: 1–30 seconds acceptable.

### 2.2 Live Voice Interaction (Active / Real-Time)

A user interacts with a butler through a microphone + speaker interface (e.g., a
dashboard web page, a mobile companion app, or a smart speaker-style device on
the tailnet). This is **streaming STT + TTS**: capture audio, detect speech end
(VAD), transcribe, send to LLM, synthesize response, play back.

Requirements: low end-to-end latency (< 2 s preferred, < 5 s acceptable for
butler-style assistant), wake word optional, VAD required.

### 2.3 Scope Decision

The butler architecture is text-first and LLM-mediated. Interaction model 2.1
(transcription of incoming voice messages) is the natural first target: it fits
the existing connector/pipeline pattern, adds no new infrastructure, and
provides immediate value.

Interaction model 2.2 (live voice) would require a WebSocket-capable frontend
channel, audio capture in the browser or a companion app, and streaming inference
infrastructure. This is a richer but higher-effort addition and is addressed in
section 9 as a future extension.

---

## 3. STT: API and Library Landscape

### 3.1 OpenAI Whisper (Local via whisper.cpp)

**What it is:** OpenAI Whisper is a general-purpose multilingual automatic speech
recognition model trained on 680,000 hours of web-sourced audio. `whisper.cpp`
is a C/C++ port using GGML, enabling CPU inference without a Python/PyTorch
runtime. The rhasspy team also wraps it for home-assistant voice pipelines.

**Repository:** `github.com/ggml-org/whisper.cpp` (MIT license)

**Model variants and memory requirements:**

| Model | Disk | RAM | Languages | Notes |
|---|---|---|---|---|
| `tiny` / `tiny.en` | 75 MiB | ~273 MB | 99 / EN only | Fastest, lowest accuracy |
| `base` / `base.en` | 142 MiB | ~388 MB | 99 / EN only | Good for short clips |
| `small` / `small.en` | 466 MiB | ~852 MB | 99 / EN only | Balanced |
| `medium` / `medium.en` | 1.5 GiB | ~2.1 GB | 99 / EN only | High accuracy |
| `large-v3` | 2.9 GiB | ~3.9 GB | 99 | Best multilingual accuracy |
| `large-v3-turbo` | ~1.6 GiB | ~2.2 GB | 99 | 8× faster than large-v3, same accuracy as large-v2 |

**Accuracy:** Whisper large-v3 achieves ~7.4% WER on mixed benchmarks, 2–5%
WER on clean English audio. The turbo variant maintains accuracy equivalent to
large-v2 (within ~0.3% WER) while reducing decoder depth from 32 to 4 layers,
cutting parameter count from 1.55 B to 809 M.

**INT8/GGUF quantization:** Q5_K_M quantization reduces model size by ~60% with
only 0.5–1.2% WER increase vs. FP16. This allows `large-v3-turbo` to run in
under 1.2 GB RAM with acceptable accuracy.

**CPU performance (representative benchmarks):**
- `tiny.en` on modern x86 (i7/Ryzen 7): ~30–50× real-time (RTF 0.02–0.03)
- `small.en` on i7-12700K: ~6–10× real-time (RTF 0.1–0.15)
- `medium.en` on i7-12700K: ~2–4× real-time (RTF 0.25–0.5)
- `large-v3-turbo` on modern CPU: ~1–2× real-time (RTF 0.5–1.0)
- `large-v3-turbo` on RTX 3060 GPU: ~50–80× real-time (RTF 0.01–0.02)

**GPU acceleration:** whisper.cpp supports CUDA, Vulkan (for AMD/Intel iGPU),
Metal (Apple Silicon), and ROCm. Version 1.8.3 added Vulkan support, delivering
a ~12× performance boost on integrated AMD/Intel graphics over CPU-only.

**Integration:** Runs as a subprocess or via Python bindings (`whispercpp` PyPI
package). The binary reads WAV/OGG/MP3/FLAC via `ffmpeg` or its own built-in
decoder.

**Privacy:** Fully offline. No audio leaves the tailnet. No API key required.

**Tailnet fit:** Excellent. Runs inside the butler container as a sidecar or
directly invoked as a subprocess from `on_startup()`.

**Verdict:** Best-fit primary option for the butler voice module. Low memory
footprint for `small`/`base` models, excellent accuracy at `large-v3-turbo` for
servers with more RAM, fully local, MIT licensed, no network egress.

---

### 3.2 faster-whisper (CTranslate2 backend, Python)

**What it is:** A Python reimplementation of Whisper using CTranslate2 — an
optimized C++ inference engine. Offers up to 4× speedup and 3× smaller model
footprint compared to the original openai/whisper Python package, through weight
quantization, layer fusion, and batch reordering.

**Repository:** `github.com/SYSTRAN/faster-whisper` (MIT license)

**Performance:**
- 4× faster than openai/whisper at equivalent accuracy
- 8-bit INT8 quantization further reduces memory by ~30–50%
- On Intel i7-12700K (8 threads), transcribes 13 min of audio in ~45 s using
  `large-v2` model (approximately 17× real-time)
- `large-v3-turbo` CT2 quantized: ~50–100× real-time on NVIDIA T4/L4 GPU

**Key differentiator vs. whisper.cpp:**
- Native Python API — no subprocess or C binding required
- Better integration with Python async code via `asyncio.to_thread()`
- Supports word-level timestamps, confidence scores, language detection
- WhisperX builds on faster-whisper to add speaker diarization alignment

**Memory footprint (INT8 quantized models):**

| Model | Disk (INT8) | RAM |
|---|---|---|
| `tiny.en` | ~38 MiB | ~160 MB |
| `small.en` | ~230 MiB | ~500 MB |
| `medium.en` | ~750 MiB | ~1.2 GB |
| `large-v3-turbo` (INT8) | ~800 MiB | ~1.4 GB |

**Tailnet fit:** Excellent. Pure Python, no external binary required. Easily
importable in the butler module.

**Verdict:** Strong alternative or complement to whisper.cpp. Prefer this option
when the butler module is implemented in Python and word-level timestamps or
confidence scores are needed (e.g., for Telegram voice message transcription
with rich metadata).

---

### 3.3 Vosk (Kaldi-based, Offline)

**What it is:** An offline speech recognition toolkit based on Kaldi acoustic
models. Supports 20+ languages with model sizes ranging from 50 MB to 1.8 GB.
Designed for edge/embedded use: runs on Raspberry Pi, Android, iOS, and
low-power systems without a GPU.

**Repository:** `github.com/alphacep/vosk-api` (Apache 2.0)

**Accuracy:** 10–15% WER on standard English benchmarks — significantly worse
than Whisper. Vosk prioritizes latency and resource efficiency over accuracy.
It performs better on narrowly constrained vocabulary tasks than on open-domain
transcription.

**Performance:**
- Real-time capable on single CPU core (Raspberry Pi 3)
- Model sizes: 50 MB (tiny), 1.8 GB (large)
- Streaming support: native chunked audio processing with partial results
- Very low latency: suitable for real-time wake-word pipeline integration

**Key features:**
- Python, Java, C#, JavaScript, Go, Rust APIs
- Native streaming decoder — returns partial results as audio arrives
- Speaker identification (limited)
- Grammar-based recognition for constrained tasks

**Tailnet fit:** Excellent (embedded-grade resource use).

**Verdict:** Not recommended as primary STT for butler voice because accuracy
(10–15% WER) is substantially worse than Whisper. However, useful for wake-word
detection or simple command recognition on severely resource-constrained hardware
where Whisper is too slow. For a standard server deployment, prefer
faster-whisper or whisper.cpp.

---

### 3.4 Google Cloud Speech-to-Text (Cloud)

**What it is:** Google's managed STT API. Supports 125+ languages, real-time
streaming, speaker diarization, and word-level timestamps.

**Pricing (2025):**
- Dynamic Batch (async): $0.003/min — can hold audio up to 24 hours
- Standard (real-time): $0.006/min (same as OpenAI Whisper API)
- Medical model: $0.030/min

**Accuracy:** WER competitive with Whisper large on English; variable on other
languages. Chirp 2 model (2024) adds improved multilingual support.

**Privacy:** Audio is transmitted to Google's servers. Not tailnet-compliant
unless via a Workspace with data-residency controls. Unsuitable for
privacy-sensitive butler use without explicit user consent.

**Tailnet fit:** Poor — requires outbound internet to Google API endpoints and
sends audio data to a third party.

**Verdict:** Not recommended for butler voice module. Privacy constraint is a
hard blocker for the tailnet isolation architecture. Cloud STT should be
documented as an opt-in channel only for users who explicitly accept cloud
data processing.

---

### 3.5 OpenAI Whisper API (Cloud)

**What it is:** OpenAI's managed API exposing Whisper and the newer
gpt-4o-transcribe / gpt-4o-mini-transcribe models.

**Pricing (January 2026):** $0.006/min flat rate, no volume discounts.

**Note (March 2025):** OpenAI released `gpt-4o-mini-transcribe` and
`gpt-4o-transcribe`, which achieve lower WER than `whisper-1` and support
streaming. OpenAI recommends `gpt-4o-mini-transcribe` for most use cases.

**Limitations:** No real-time streaming for `whisper-1`; 1–2 min file minimum
billing. The API accepts files up to 25 MB.

**Privacy:** Audio sent to OpenAI's servers. Same cloud data concern as Google.

**Verdict:** Same privacy concern as Google Cloud STT. Acceptable as opt-in
for non-sensitive audio if the user is already paying for OpenAI API access.
Useful fallback for mobile scenarios where no local GPU is available.

---

### 3.6 STT Comparison Matrix

| Criterion | whisper.cpp | faster-whisper | Vosk | Google STT | OpenAI API |
|---|---|---|---|---|---|
| WER (English) | 7–10% (small); 2–5% (large) | Same as Whisper | 10–15% | ~5–7% | ~5–7% |
| Local / offline | Yes | Yes | Yes | No | No |
| Privacy | Full | Full | Full | Cloud | Cloud |
| GPU required | No (optional) | No (optional) | No | N/A | N/A |
| Min RAM (usable) | 273 MB (tiny) | 160 MB (tiny INT8) | 50 MB (tiny) | N/A | N/A |
| Streaming output | Via real-time API | Chunked | Native | Yes | No (whisper-1) |
| Python native | Via subprocess/bindings | Yes | Yes | Yes (SDK) | Yes (SDK) |
| Multilingual | Yes (99 langs) | Yes (99 langs) | 20+ langs | 125+ langs | 99+ langs |
| License | MIT | MIT | Apache 2.0 | Proprietary | Proprietary |
| Cost | Free | Free | Free | $0.003–0.006/min | $0.006/min |

---

## 4. TTS: API and Library Landscape

### 4.1 Piper TTS (Local, Neural, CPU-First)

**What it is:** Piper is a fast, local neural text-to-speech system developed by
the Rhasspy team (originally by Michael Hansen at Nabu Casa / Home Assistant).
It uses VITS (Variational Inference with adversarial learning for end-to-end
Text-to-Speech) exported to ONNX format for CPU-friendly inference. Development
has moved from `rhasspy/piper` (archived October 2025) to `OHF-Voice/piper1-gpl`.

**Repository:** `github.com/OHF-Voice/piper1-gpl` (GPL-3.0)
Original archived: `github.com/rhasspy/piper`

**Voice quality levels:**

| Quality | Sample rate | Notes |
|---|---|---|
| `x_low` | 16 kHz | Smallest models, robotic quality |
| `low` | 16 kHz | Acceptable for assistant use |
| `medium` | 22.05 kHz | Good quality, recommended default |
| `high` | 22.05 kHz | Best quality, larger models |

**Performance:**
- RTF on Threadripper 1800X CPU: **~0.02** (50× real-time) at `medium` quality
- On Raspberry Pi 4: ~0.6 RTF (1.7× real-time at `medium`)
- RAM: 30–200 MB depending on voice model
- ONNX backend: no GPU required; runs on any x86/ARM CPU

**Voice catalog:** 900+ voice models across 30+ languages hosted on Hugging Face
(`rhasspy/piper-voices`). English voices include many regional accents.

**Key differentiator:** Piper is the fastest production-quality local TTS. It
runs near-realtime on Raspberry Pi hardware (a conservative baseline), making it
suitable for any server-grade deployment with ample headroom.

**Python integration:** Python API available (`piper-tts` PyPI package).
Also runs as CLI subprocess (`piper --model voice.onnx --output_raw`).

**License concern:** GPL-3.0 applies to the new `piper1-gpl` repo. The original
`rhasspy/piper` is MIT. Check which package is installed — `piper-tts` on PyPI
currently wraps the MIT-licensed original. The GPL change affects redistribution,
not self-hosted internal use.

**Verdict:** **Primary recommendation** for local TTS. Fast, lightweight,
high-quality, runs on any hardware including Raspberry Pi. 900+ voice options.
No API key. No network egress.

---

### 4.2 Kokoro TTS (Local, Neural, 82M Parameters)

**What it is:** Kokoro-82M is a lightweight open-weight TTS model (82M params)
based on StyleTTS2 + ISTFTNet. Decoder-only architecture with no diffusion or
encoder, enabling fast inference. Released under Apache 2.0 license.

**Repository:** `github.com/hexgrad/kokoro`
**Hugging Face:** `hexgrad/Kokoro-82M`

**Performance:**
- Generates audio in < 0.3 s for typical text lengths on GPU
- RTFx of ~24× real-time average on M4 Pro MacBook CPU (range: 3.7×–25.8×)
- On NVIDIA L4 (24 GB GPU): ~96× real-time
- On free Colab T4 GPU: 36× real-time

**Quality:** Comparable to ElevenLabs for English voices in subjective evaluations.
Supports multiple voice styles (50 voices). English-primary with experimental
multilingual support.

**Python integration:** `pip install kokoro` (Apache 2.0). FastAPI wrapper
available (`remsky/Kokoro-FastAPI`) for HTTP serving with ONNX CPU support and
NVIDIA GPU PyTorch support.

**Hardware requirements:**
- CPU inference (ONNX): ~1–2 GB RAM, no GPU required
- GPU inference (PyTorch): 2–4 GB VRAM on any NVIDIA/AMD card

**Key differentiator vs. Piper:** Better naturalness and expressiveness. Slower
than Piper on constrained CPU hardware but comparable or faster on GPU. English
voice quality is noticeably higher than Piper's best voices in subjective tests.
Apache 2.0 license (no copyleft concern).

**Verdict:** Strong alternative to Piper when voice quality is the priority and
a GPU or fast CPU is available. For a butler server deployment with a GPU, Kokoro
is the preferred option for English voices.

---

### 4.3 Coqui TTS / XTTS (Local, Neural, Voice Cloning)

**What it is:** Coqui TTS was an open-source TTS library (the Coqui.ai company
closed in January 2024, but the open-source codebase continues as forks).
XTTS is the flagship model: a multilingual TTS with voice cloning from a 6-second
reference clip.

**Codebase status:** The original `coqui-ai/TTS` repository is no longer
actively maintained by the company. Community forks continue development.
The `TTS` library remains installable via `pip install TTS`.

**XTTS key features:**
- 17+ languages (English, Spanish, French, German, Portuguese, Italian, etc.)
- Voice cloning from 6 seconds of reference audio
- Supports speaker-conditioned generation
- Model size: ~1.8 GB

**Performance:**
- GPU required for near-realtime generation
- RTF: ~5–10× real-time on NVIDIA T4 GPU; CPU-only is too slow for interactive use
- RAM: 4–8 GB for XTTS models

**License:** `CPML` license (non-commercial use only) for the XTTS model weights.
This is a significant restriction — unsuitable for any commercial deployment.
The base VITS models are Apache 2.0.

**Voice cloning privacy concern:** Voice cloning requires a reference audio
clip. Storing voice embeddings introduces biometric data that must be handled
carefully under GDPR.

**Verdict:** Interesting for voice cloning use cases, but the non-commercial
XTTS license and GPU requirement make it a poor fit for a self-hosted,
general-purpose butler. The project's organizational dissolution adds
maintenance risk. Not recommended as primary option.

---

### 4.4 espeak-ng (Local, Rule-Based, CPU)

**What it is:** espeak-ng is a compact, rule-based (formant synthesis) TTS
engine. No neural model — generates speech via phoneme-to-formant rules with no
GPU or significant RAM. The oldest option in this comparison.

**Repository:** `github.com/espeak-ng/espeak-ng` (GPL-3.0)

**Performance:**
- Extremely fast: < 5 ms per utterance on any hardware
- Binary: < 10 MB
- RAM: < 50 MB

**Quality:** Robotic, synthetic character. Clearly machine-generated. Acceptable
only for systems where voice quality is irrelevant (screen readers, embedded
devices in noisy environments, kiosks).

**Note:** Piper uses espeak-ng internally for the G2P (grapheme-to-phoneme)
front-end before the neural vocoder. This is not visible to the end user.

**Verdict:** Not appropriate for butler voice output where quality matters.
Include only as an ultra-low-resource emergency fallback. Piper at `x_low`
quality is almost as fast and dramatically better in naturalness.

---

### 4.5 edge-tts (Cloud, Microsoft Neural Voices)

**What it is:** An unofficial Python library that reverse-engineers Microsoft
Edge's online TTS service to access its 400+ neural voices (the same voices
used in Azure Cognitive Services Speech) without an API key.

**Repository:** `github.com/rany2/edge-tts` (GPL-3.0)

**Voice quality:** High — Microsoft's neural voices are production-grade cloud
voices with natural prosody and near-human expressiveness in 140+ languages.

**Performance:** Requires internet round-trip. Latency: ~200–800 ms per
synthesized segment depending on network conditions and response length.

**Privacy:** Audio content (text to be synthesized) is sent to Microsoft's
servers in cleartext. Not suitable for privacy-sensitive butler output.

**Stability:** Reverse-engineered unofficial API. No guarantee of continued
availability. Microsoft could revoke access or change the protocol at any time.
Not suitable for a production deployment.

**Cost:** Free — uses the same token issued to Edge browser sessions.

**Verdict:** Not recommended for butler production use. Network round-trip
breaks the tailnet-isolation principle, privacy is poor, and stability is
uncertain. Acceptable only for development/demo purposes where a user accepts
cloud data transmission.

---

### 4.6 TTS Comparison Matrix

| Criterion | Piper | Kokoro-82M | XTTS (Coqui) | espeak-ng | edge-tts |
|---|---|---|---|---|---|
| Quality (subjective) | Good (medium/high) | Excellent (English) | Excellent + voice cloning | Poor (robotic) | Excellent |
| Local / offline | Yes | Yes | Yes | Yes | No (cloud) |
| Privacy | Full | Full | Full | Full | Cloud (text sent) |
| GPU required | No (CPU ONNX) | No (ONNX CPU) | Yes (practical) | No | N/A |
| Min RAM | ~30 MB | ~1 GB | ~4 GB | < 50 MB | N/A |
| RTF (CPU) | ~50× | ~4–24× | < 1× (too slow) | ~1000× | N/A (network) |
| Multilingual | 30+ languages | English + limited | 17+ languages | 50+ languages | 140+ languages |
| Voice cloning | No | No | Yes | No | No |
| License | GPL-3.0 / MIT | Apache 2.0 | CPML (non-commercial) | GPL-3.0 | GPL-3.0 |
| Stability | High (active) | High (growing) | Medium (company closed) | High | Low (unofficial API) |

---

## 5. Benchmark Summary

### 5.1 STT Benchmark Data

The following represents collected benchmark data from multiple sources
(whisper.cpp GitHub, northflank.com benchmarks, faster-whisper documentation,
academic evaluations, and community reports as of early 2026):

**Whisper model accuracy (WER on LibriSpeech test-clean, approximate):**

| Model | WER (clean) | WER (noisy) |
|---|---|---|
| Whisper tiny.en | ~15% | ~30%+ |
| Whisper base.en | ~8–10% | ~20%+ |
| Whisper small.en | ~6–8% | ~15% |
| Whisper medium.en | ~4–6% | ~10% |
| Whisper large-v3 | 2–4% | 7–10% |
| Whisper large-v3-turbo | 2–5% | 7–11% |
| Vosk (large EN) | ~10–15% | ~25%+ |

**faster-whisper CPU performance (Intel Core i7-12700K, INT8 quantization):**

| Model | Audio duration | Processing time | RTF |
|---|---|---|---|
| tiny.en (INT8) | 13 min | ~6 s | ~130× |
| small.en (INT8) | 13 min | ~20 s | ~39× |
| medium.en (INT8) | 13 min | ~45 s | ~17× |
| large-v3-turbo (INT8) | 13 min | ~90 s | ~8.7× |

**whisper.cpp GPU performance (NVIDIA RTX 3060, CUDA):**
- `large-v3`: ~40–60× real-time
- `large-v3-turbo`: ~80–120× real-time
- With Vulkan (AMD/Intel iGPU): ~12× improvement over CPU baseline (per v1.8.3 release notes)

### 5.2 TTS Benchmark Data

**Piper real-time factors (ONNX CPU, medium quality voice):**

| Hardware | RTF | Notes |
|---|---|---|
| Threadripper 1800X (CPU) | ~0.02 (50× RT) | Server-class desktop |
| Raspberry Pi 4 | ~0.6 (1.7× RT) | Acceptable for local use |
| RK3588 CPU | ~0.65 (1.5× RT) | ARM SoC |

**Kokoro-82M latency (text → audio start):**

| Hardware | Avg. generation time (100 words) |
|---|---|
| M4 Pro MacBook CPU | < 0.3 s |
| NVIDIA L4 GPU (24 GB) | < 0.05 s |
| Standard x86 CPU | < 0.5–1.0 s |

---

## 6. VAD, Wake Word, and Speaker Diarization

### 6.1 Voice Activity Detection (VAD)

VAD is required to detect speech boundaries in a continuous audio stream (live
interaction model). It also filters silence from uploaded voice messages before
sending to STT.

**Options:**

**Silero VAD** (`snakers4/silero-vad`, MIT):
- DNN-based VAD trained on 6,000+ languages
- RTF: 0.004 on AMD CPU (0.43% CPU usage for real-time processing)
- Accuracy: 87.7% TPR at 5% FPR (Picovoice benchmark)
- Memory: ~10 MB model
- Python native, ONNX-exportable
- **Recommended** — best balance of accuracy and resource use for local deployment

**WebRTC VAD** (built into many toolkits):
- GMM-based, no neural model
- Extremely fast (< 1 ms per frame)
- Accuracy: only 50% TPR at 5% FPR — poor for real-world noisy audio
- Acceptable for very quiet, clean audio environments only

**Picovoice Cobra**:
- Proprietary, DNN-based
- Best accuracy (98.9% TPR at 5% FPR)
- Requires API key even for local inference
- Not recommended for open/self-hosted butler

**Recommendation:** Silero VAD for all VAD needs. It is open source, accurate,
fast, and requires no API key.

### 6.2 Wake Word Detection

Wake word detection enables hands-free activation ("Hey Butler", etc.) without
continuous recording. Not required for Telegram voice message transcription but
necessary for live voice interaction.

**Options:**

**openWakeWord** (`dscripka/openWakeWord`, Apache 2.0):
- Built on Google's audio embedding model + fine-tuned per wake-word
- Trained using Piper TTS for synthetic data generation (good synergy)
- A single Raspberry Pi 3 core can run 15–20 models simultaneously in real-time
- Custom wake words trainable with TTS-synthesized data, no real recordings required
- **Recommended** for open-source, self-hosted butler

**Picovoice Porcupine**:
- Proprietary, 97%+ accuracy with < 1 false alarm in 10 hours
- Pre-built wake words + custom training via Picovoice Console
- Best accuracy for constrained hardware but requires license key
- Free tier exists for personal use; commercial use requires paid license

**Recommendation:** openWakeWord for a fully open source, zero-cost pipeline.
Porcupine as a premium alternative if accuracy on very low-power hardware
is the priority.

### 6.3 Speaker Diarization

Speaker diarization ("who spoke when") is relevant for multi-speaker audio
(recorded meetings, group voice messages). Single-speaker butler use cases
do not require it.

**pyannote.audio** (`pyannote/speaker-diarization-3.1`, MIT):
- Best open-source diarization as of 2025–2026
- DER: ~11–19% on standard benchmarks (3.1 model)
- Community-1 (newer, open-source): ~13.3% DER
- PyannoteAI commercial: 6.6–11.2% DER
- Requires HuggingFace token to download model weights
- Hardware: CPU inference supported; GPU recommended for real-time

**WhisperX** builds on faster-whisper + pyannote to produce word-level
speaker-attributed transcripts in one pipeline.

**Recommendation:** Not required for butler v1 voice module (single-speaker
voice messages from a known user). Document as optional enhancement for
future group audio transcription.

---

## 7. Data Model and Butler Integration Points

### 7.1 Voice Module in Butler Architecture

The Voice module implements the `Module` ABC (`src/butlers/modules/base.py`):

```
class VoiceModule(Module):
    name = "voice"
    dependencies = []  # or ["pipeline"] if Telegram integration is wired

    async def register_tools(self, mcp, config, db) -> None:
        # MCP tools exposed to the LLM CLI instance:
        # - bot_voice_transcribe_file (transcribes audio file → text)
        # - bot_voice_speak (synthesizes text → audio file)
        # - bot_voice_play (plays synthesized audio — local output device)

    async def migrations(self) -> list[Migration]: ...
    async def on_startup(self, config, db) -> None: ...
    async def on_shutdown(self) -> None: ...
```

### 7.2 Telegram Voice Message Transcription Pipeline

The primary integration point: when a Telegram bot receives a voice message
(`message.voice`), the butler module should:

1. Download the OGG/OPUS audio from Telegram's file server
2. Convert to WAV (via `ffmpeg`) if needed by the STT backend
3. Run STT (faster-whisper or whisper.cpp subprocess)
4. Inject the transcript text into the normal pipeline as a `text` message
5. Store the transcript and audio file reference in the butler's PostgreSQL DB

This flow is synchronous within the existing message handler; no new MCP
tools are required. The module adds the transcription step between raw message
receipt and pipeline entry.

**Format:** Telegram voice messages are OGG/OPUS. faster-whisper supports
WAV natively; `ffmpeg` converts OGG → WAV in < 100 ms.

### 7.3 Database Schema

```sql
-- Voice message transcription log
CREATE TABLE voice_transcriptions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_channel   TEXT NOT NULL,        -- 'telegram', 'whatsapp', 'upload'
    source_message_id TEXT,               -- External message ID if applicable
    audio_format     TEXT NOT NULL,       -- 'ogg', 'mp3', 'wav', 'flac'
    audio_duration_s FLOAT,               -- Duration in seconds
    audio_size_bytes INTEGER,
    transcript       TEXT NOT NULL,        -- Whisper output
    language_detected TEXT,               -- ISO 639-1 code
    wer_confidence   FLOAT,               -- If available from model
    model_used       TEXT NOT NULL,        -- e.g. 'faster-whisper:large-v3-turbo'
    transcribed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    pipeline_request_id UUID              -- FK to pipeline if wired
);

-- TTS synthesis cache (avoid re-synthesizing identical text)
CREATE TABLE voice_tts_cache (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text_hash        TEXT NOT NULL UNIQUE, -- SHA-256 of (text + voice_id)
    text_content     TEXT NOT NULL,
    voice_id         TEXT NOT NULL,        -- Piper voice model name or Kokoro style
    audio_data       BYTEA,               -- Raw PCM or WAV (if small enough)
    audio_path       TEXT,               -- File path (if too large for BYTEA)
    synthesized_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Alembic migration: `voice_001_create_voice_tables.py` under
`alembic/versions/voice/`.

### 7.4 Live Voice Interaction Architecture (Future Extension)

For real-time voice interaction, a WebSocket-based pipeline would be required:

```
Browser / Client
    |-- WebSocket (audio chunks PCM 16kHz) -->
    |                                           Butler WebSocket handler
    |                                               |-- VAD (Silero)
    |                                               |-- STT (faster-whisper streaming)
    |                                               |-- LLM CLI invocation
    |                                               |-- TTS (Piper/Kokoro)
    |<-- WebSocket (PCM audio response) ------------|
```

Audio format for streaming: PCM 16-bit, 16 kHz mono (raw PCM is the only format
that supports progressive streaming without container overhead). WebSocket framing:
8 KB chunks for STT, 4 KB chunks for TTS output.

This is not in scope for v1 but documented here for architectural alignment.

---

## 8. Privacy Considerations

### 8.1 Data Classification

Voice audio contains biometric data (speaker identity, voice patterns) in
addition to conversation content. Under GDPR and similar regulations:

- **Audio files are sensitive personal data.** Storage must be justified by
  legitimate interest or explicit consent.
- **Transcripts** inherit the sensitivity of their content (personal
  conversations, commands, information shared verbally).
- **Speaker voice embeddings** (for diarization/identification) constitute
  biometric data — highest protection tier under GDPR.

### 8.2 Data Minimization

The butler should apply data minimization:
- Do not store raw audio files beyond the time needed for transcription.
- Store transcripts only if the user has opted in to transcription logging.
- Purge audio from disk immediately after STT completes.
- Provide a `bot_voice_delete_transcription` MCP tool for user-initiated
  removal.

### 8.3 On-Device Processing Requirement

All STT and TTS processing **must** run locally for the butler's default
configuration. Cloud STT/TTS (Google, OpenAI API) should be available only
as opt-in configuration with explicit documentation that audio leaves the
tailnet.

Configuration flag:
```toml
[modules.voice]
stt_backend = "faster-whisper"   # or "whisper.cpp", "openai-api", "google-api"
tts_backend = "piper"            # or "kokoro", "edge-tts", "openai-api"
cloud_allowed = false            # Must be true to enable any cloud backend
```

---

## 9. Hardware Requirements

### 9.1 Minimum Configuration (CPU-only)

Target: Telegram voice message transcription (async batch STT)

| Component | Minimum | Notes |
|---|---|---|
| CPU | Any modern x86/ARM | Whisper tiny or small |
| RAM | 2 GB available | For `small.en` INT8 via faster-whisper |
| Disk | 1 GB | Model storage |
| OS | Linux (Debian/Ubuntu 22.04+) | Docker container |
| GPU | None required | |

Suitable for: short voice messages (< 60 s), non-realtime transcription,
Telegram bot transcription pipeline.

### 9.2 Recommended Configuration (CPU-only, High Accuracy)

Target: Best-quality async transcription without GPU

| Component | Recommended | Notes |
|---|---|---|
| CPU | 4+ core x86 at 3 GHz+ (e.g., Ryzen 5, i5-12th gen) | |
| RAM | 4 GB available | For `large-v3-turbo` INT8 via faster-whisper |
| Disk | 4 GB | Model + audio cache |
| OS | Linux | Docker container |
| GPU | None (optional) | |

Suitable for: all voice message transcription, multilingual support, long
recordings.

### 9.3 Recommended Configuration (with GPU, Live Voice)

Target: Near-realtime interactive voice with low latency TTS

| Component | Recommended | Notes |
|---|---|---|
| CPU | Any modern 4-core | |
| RAM | 8 GB system | |
| GPU | NVIDIA RTX 3060 (12 GB VRAM) or equivalent | Or AMD RX 6600 with ROCm |
| VRAM | 6 GB+ | Fits large-v3-turbo + Kokoro simultaneously |
| Disk | 8 GB | Models + TTS cache |

Suitable for: real-time voice interaction, TTS response synthesis, simultaneous
STT + TTS operation.

### 9.4 Model Combination RAM Summary

| Use case | STT model | TTS model | Total RAM | GPU needed |
|---|---|---|---|---|
| Telegram transcription (basic) | faster-whisper small.en INT8 | None | ~800 MB | No |
| Telegram transcription (accurate) | faster-whisper large-v3-turbo INT8 | None | ~2.5 GB | No |
| Live voice (budget) | whisper.cpp base.en | Piper medium | ~600 MB | No |
| Live voice (quality) | faster-whisper medium.en INT8 | Kokoro-82M ONNX | ~3.5 GB | No |
| Live voice (best, GPU) | faster-whisper large-v3-turbo | Kokoro-82M | ~4 GB VRAM | Yes |

---

## 10. Recommendation for Fully-Local Pipeline

### Primary Recommendation

**STT: faster-whisper with `large-v3-turbo` (INT8 quantized)**
- Best accuracy for multilingual voice messages at reasonable resource cost
- Python-native, asyncio-friendly via `asyncio.to_thread()`
- Word-level timestamps available for Telegram message linking
- Falls back to `small.en` on memory-constrained deployments
- Apache 2.0 license (no copyleft concern)

**TTS: Piper (medium or high quality English voice)**
- Fastest CPU inference (50× real-time on server hardware)
- 900+ voice models, 30+ languages
- ONNX backend requires no GPU
- GPL-3.0 applies to `piper1-gpl`; original `piper-tts` PyPI package is MIT —
  use `pip install piper-tts` to stay MIT for internal self-hosted use

**VAD: Silero VAD**
- Best open-source accuracy (87.7% TPR at 5% FPR)
- MIT license, Python native
- 0.43% CPU load for real-time processing

**Wake word (future, live voice only): openWakeWord**
- Apache 2.0, fully open source
- Custom wake words trainable with TTS synthetic data

### Optional Premium: Kokoro-82M for TTS

Replace Piper with Kokoro-82M when:
- English-primary deployment
- Voice quality is the top priority
- Server has a modern CPU (x86_64 with AVX2) or any GPU
- Apache 2.0 license is preferred over GPL-3.0

### Rationale Against Cloud

- Google Cloud STT and OpenAI Whisper API both transmit audio to third-party
  servers — incompatible with butler's tailnet isolation and privacy principles
- edge-tts is an unofficial, unstable API that transmits synthesized text
  to Microsoft servers
- Cloud options add recurring cost ($0.006/min for a user who speaks to their
  butler for 60 min/week = ~$22/year minimum)
- Local models are free after initial download and improve over time

---

## 11. Open Questions for Implementation

1. **Audio capture for live voice:** What is the input channel for live voice?
   Browser microphone via dashboard WebSocket? A dedicated Tailscale-accessible
   audio device? The answer determines whether a streaming STT pipeline is
   needed.

2. **Telegram voice processing:** Should transcription happen synchronously in
   the Telegram message handler (blocking until STT completes), or as a
   background task that updates the message object asynchronously? Async is
   cleaner but requires tracking "transcription pending" state.

3. **TTS output channel:** Where does TTS output go? Back to Telegram as a voice
   message? Dashboard audio element? Local speaker? Each requires different
   delivery plumbing.

4. **Language auto-detection:** Whisper performs language detection automatically
   on the first 30 s of audio. Should the module record the detected language per
   user and use it to skip detection on subsequent messages?

5. **Audio storage policy:** Raw audio files are potentially large (WhatsApp
   voice notes, long recordings). Should the module store audio in PostgreSQL
   BYTEA (OK for < 1 MB clips), on disk (Docker volume), or in an S3-compatible
   object store for larger files?

6. **Transcription logging consent:** Transcripts of voice messages are a
   privacy-sensitive record of spoken conversations. Should the module default
   to discarding transcripts after pipeline injection, or log them with
   explicit user opt-in?

7. **Multi-language support:** Should the module maintain one STT model per
   language or use the multilingual `large-v3-turbo` for all languages? The
   latter is simpler; the former allows deploying a smaller `small.en` model
   for English-only users.

8. **Piper license in piper1-gpl:** The active development repo moved to GPL-3.0.
   The `piper-tts` PyPI package (still MIT) should be audited before implementation
   to confirm it does not incorporate GPL code from the new repo.

---

## 12. Implementation Checklist (for future ticket)

When the implementation ticket is created:

1. Add `faster-whisper` and `silero-vad` to `pyproject.toml` dependencies.
2. Add `piper-tts` (MIT) or `kokoro` (Apache 2.0) for TTS.
3. Add `ffmpeg` as a system dependency in `Dockerfile`.
4. Create `src/butlers/modules/voice.py` implementing `VoiceModule`.
5. Write Alembic migration `voice_001_create_voice_tables.py`.
6. Wire Telegram voice message handler to call `VoiceModule.transcribe()`.
7. Expose `bot_voice_transcribe_file` and `bot_voice_speak` MCP tools.
8. Add `[modules.voice]` config section to butler TOML schema.
9. Write unit tests: transcription pipeline, TTS synthesis, VAD filtering.
10. Document live voice WebSocket protocol in `docs/modules/voice_websocket.md`
    when live voice feature is scoped.

---

## 13. References

**STT**
- [whisper.cpp GitHub (ggml-org)](https://github.com/ggml-org/whisper.cpp)
- [faster-whisper GitHub (SYSTRAN)](https://github.com/SYSTRAN/faster-whisper)
- [Vosk API GitHub (alphacep)](https://github.com/alphacep/vosk-api)
- [Best Open-Source STT Models 2026 — Northflank](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Whisper large-v3-turbo on HuggingFace](https://huggingface.co/openai/whisper-large-v3-turbo)
- [Whisper Large V3 Turbo: 5× Faster, Same Accuracy — WhisperNotes](https://whispernotes.app/blog/introducing-whisper-large-v3-turbo)
- [Choosing between Whisper variants — Modal](https://modal.com/blog/choosing-whisper-variants)
- [Whisper API Pricing 2026 — BrassTranscripts](https://brasstranscripts.com/blog/openai-whisper-api-pricing-2025-self-hosted-vs-managed)
- [Speech-to-Text API Pricing Breakdown 2025 — Deepgram](https://deepgram.com/learn/speech-to-text-api-pricing-breakdown-2025)
- [Quantization for Whisper Models (arXiv 2025)](https://arxiv.org/html/2503.09905v1)
- [Whisper.cpp 1.8.3 12x Perf Boost — Phoronix](https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf)
- [Benchmarking Open-Source STT 2025 — Graphlogic AI](https://graphlogic.ai/blog/ai-trends-insights/voice-technology-trends/benchmarking-top-open-source-speech-recognition-models-whisper-facebook-wav2vec2-and-kaldi/)

**TTS**
- [Piper TTS (OHF-Voice/piper1-gpl)](https://github.com/OHF-Voice/piper1-gpl)
- [Piper original (rhasspy/piper, MIT, archived)](https://github.com/rhasspy/piper)
- [Kokoro-82M HuggingFace](https://huggingface.co/hexgrad/Kokoro-82M)
- [Kokoro GitHub (hexgrad)](https://github.com/hexgrad/kokoro)
- [Kokoro FastAPI Docker wrapper](https://github.com/remsky/Kokoro-FastAPI)
- [12 Best Open-Source TTS Models 2025 — Inferless](https://www.inferless.com/learn/comparing-different-text-to-speech---tts--models-part-2)
- [Best Open-Source TTS 2026 — BentoML](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models)
- [edge-tts GitHub (rany2)](https://github.com/rany2/edge-tts)
- [Local TTS Guide 2026 — LocalClaw](https://localclaw.io/blog/local-tts-guide-2026)
- [Best ElevenLabs Alternatives 2026 — ocdevel](https://ocdevel.com/blog/20250720-tts)

**VAD and Wake Word**
- [Silero VAD GitHub (snakers4)](https://github.com/snakers4/silero-vad)
- [Choosing the Best VAD 2025 — Picovoice](https://picovoice.ai/blog/best-voice-activity-detection-vad-2025/)
- [openWakeWord GitHub (dscripka)](https://github.com/dscripka/openWakeWord)
- [Porcupine Wake Word (Picovoice)](https://picovoice.ai/platform/porcupine/)
- [Home Assistant wake word approach](https://www.home-assistant.io/voice_control/about_wake_word/)

**Diarization**
- [pyannote.audio GitHub](https://github.com/pyannote/pyannote-audio)
- [Best Speaker Diarization Models 2026 — BrassTranscripts](https://brasstranscripts.com/blog/speaker-diarization-models-comparison)

**Architecture and Integration**
- [Real-Time vs Turn-Based Voice Agent Architecture — Softcery](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture)
- [MCP TTS Server (blacktop)](https://github.com/blacktop/mcp-tts)
- [local-stt-mcp (SmartLittleApps)](https://github.com/SmartLittleApps/local-stt-mcp)
- [MCP voice agent with OpenAI and LiveKit — AssemblyAI](https://www.assemblyai.com/blog/mcp-voice-agent-openai-livekit)
