# Telegram Voice-Note Transcription — Delta

## ADDED Requirements

### Requirement: Voice-Note Transcription Configuration Surface

The Telegram bot connector SHALL support an optional, disabled-by-default voice-transcription
configuration surface via new environment variables:
`CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED` (boolean, default `false`),
`CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_URL` (STT service URL, default the same Wyoming backend the
live-listener connector already depends on, `tcp://wyoming-faster-whisper.parrot-hen.ts.net:10300`),
`CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_PROTOCOL` (default `"wyoming"`), and
`CONNECTOR_TELEGRAM_VOICE_MAX_DURATION_S` (integer seconds, default `120`). This is additive to the
connector's existing `Environment Variables` requirement — none of its existing required or optional
variables change.

#### Scenario: Feature disabled by default

- **WHEN** `CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED` is unset or `false`
- **THEN** every voice note, regardless of sender, produces the existing unconditional
  `[Voice message]` descriptor, and no `getFile`, download, transcode, or STT call is made

#### Scenario: Feature enabled

- **WHEN** `CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED=true`
- **THEN** the connector evaluates each voice note against the eligibility gate and media bounds
  defined by this capability's other requirements

### Requirement: Owner-Only Eligibility Gate

The connector SHALL transcribe a voice note only when its sender resolves to the owner via a fast,
classification-pipeline-independent lookup, evaluated before any Telegram network call for that
note's file. A non-owner sender's voice note SHALL be treated identically to today's unconditional
behavior: the existing `[Voice message]` descriptor, with no `getFile`, download, transcode, or STT
call attempted.

#### Scenario: Owner-sent voice note is eligible

- **WHEN** a `voice` message's `sender.identity` resolves to the owner via the same fast lookup the
  `telegram-receipt-acknowledgement` capability's Ownership Boundary requirement already specifies
  (e.g. `identity.resolve_owner_channel_via_definer` or an equivalent single indexed lookup)
- **THEN** the note proceeds to the media-bounds check

#### Scenario: Non-owner-sent voice note is ineligible

- **WHEN** a `voice` message's sender does not resolve to the owner (including a non-owner group-chat
  participant, or an internally synthesized batch envelope with no real owner participant)
- **THEN** the connector makes no Telegram file-download or STT call for that note, and its
  `normalized_text` is the existing unconditional `[Voice message]` descriptor

### Requirement: Media Acquisition Bounds

The connector SHALL reject a voice note for transcription, before any network call, when its
self-reported metadata exceeds either of two bounds: a file-size ceiling of 20 MB (the Telegram Bot
API's own documented `getFile` download limit) and a duration ceiling of
`CONNECTOR_TELEGRAM_VOICE_MAX_DURATION_S` seconds (default 120). It SHALL also enforce the file-size
ceiling as a post-download backstop when `Voice.file_size` was absent or understated.

#### Scenario: Oversize note rejected before download

- **WHEN** an eligible voice note's `Voice.file_size` is reported and exceeds 20 MB
- **THEN** the connector makes no `getFile` or download call, and the note's `normalized_text` is the
  existing `[Voice message]` descriptor

#### Scenario: Overlong note rejected before download

- **WHEN** an eligible voice note's `Voice.duration` exceeds `CONNECTOR_TELEGRAM_VOICE_MAX_DURATION_S`
- **THEN** the connector makes no `getFile` or download call, and the note's `normalized_text` is the
  existing `[Voice message]` descriptor

#### Scenario: Post-download oversize backstop

- **WHEN** `Voice.file_size` was absent or understated and the actual downloaded byte count exceeds
  20 MB
- **THEN** the connector discards the downloaded bytes immediately without transcoding, and the
  note's `normalized_text` is the existing `[Voice message]` descriptor

### Requirement: Transcoding to the TranscriptionClient PCM Contract

The connector SHALL decode a compressed voice-note container to raw 16 kHz mono 16-bit signed PCM —
the format `connectors/live_listener/transcription.py::TranscriptionClient.transcribe()` requires —
using a bounded `ffmpeg` subprocess invoked as an argument list over stdin/stdout pipes, with no
temporary file written to disk and no dependency on any sender-reported `mime_type`.

#### Scenario: Argument-list invocation, no shell

- **WHEN** the connector transcodes a downloaded voice note
- **THEN** `ffmpeg` is invoked with an explicit argument list (never `shell=True`, never
  string-interpolated arguments), audio bytes are supplied via a stdin pipe, and the PCM result is
  read from a stdout pipe

#### Scenario: Bounded timeout

- **WHEN** the transcode subprocess runs
- **THEN** it is bounded by a timeout no larger than `CONNECTOR_TELEGRAM_VOICE_MAX_DURATION_S`, so a
  malformed or adversarial container cannot hold a connector worker slot indefinitely

#### Scenario: Transcode failure falls back

- **WHEN** `ffmpeg` exits non-zero, produces no usable output, or the timeout elapses
- **THEN** the downloaded and any partial transcoded bytes are discarded, and the note's
  `normalized_text` is the existing `[Voice message]` descriptor

### Requirement: Single-Shot Reuse of the Existing TranscriptionClient

The connector SHALL reuse `connectors/live_listener/transcription.py::TranscriptionClient` as a
single, persistent, connector-owned instance created at connector startup when the feature is
enabled, calling `transcribe(pcm_bytes)` once per eligible voice note and inheriting the class's own
internal reconnect-with-backoff and call-serialization behavior. It SHALL NOT introduce a second,
connector-specific retry layer around the STT call itself.

#### Scenario: Shared persistent instance

- **WHEN** the connector starts with voice transcription enabled
- **THEN** exactly one `TranscriptionClient` instance is constructed and connected for the life of the
  connector process, configured from `CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_URL`/`_PROTOCOL`

#### Scenario: Concurrent voice notes serialize on the shared instance

- **WHEN** more than one eligible voice note is being processed concurrently (bounded by the
  connector's existing `CONNECTOR_MAX_INFLIGHT` semaphore)
- **THEN** each `transcribe()` call queues on the client's own internal lock rather than opening a
  second connection or corrupting shared connection state

#### Scenario: STT unavailability after the client's own retry falls back

- **WHEN** `transcribe()` returns `None` after the client's own internal reconnect-and-retry attempt
- **THEN** the connector makes no further retry attempt, and the note's `normalized_text` is the
  existing `[Voice message]` descriptor

### Requirement: Empty or Low-Confidence Result Fallback

The connector SHALL fall back to the existing `[Voice message]` descriptor — never an empty or
placeholder transcript — when the STT backend returns empty text or a confidence below the client's
own configured minimum, matching `TranscriptionClient`'s existing discard semantics but, unlike the
live-listener connector's ambient pipeline, without dropping the message itself.

#### Scenario: Empty or low-confidence transcript does not become message content

- **WHEN** `transcribe()` discards its own result internally (empty text or below-threshold
  confidence) and returns `None`
- **THEN** the note is still ingested, with `normalized_text` set to the existing `[Voice message]`
  descriptor, not an empty string

### Requirement: Successful Transcript Supersedes the Descriptor as Real Content

When a voice note is eligible, within bounds, and successfully transcribed with acceptable
confidence, the connector SHALL set `normalized_text` to the transcript text itself, unwrapped by any
descriptor syntax, so downstream classification and routing treat it exactly as owner-typed text.

#### Scenario: Successful transcript is plain text, not a bracketed descriptor

- **WHEN** an eligible voice note is transcribed with non-empty text at or above the confidence
  threshold
- **THEN** `payload.normalized_text` is set to that transcript text verbatim, not to
  `[Voice message: "..."]` or any other bracket-wrapped form

### Requirement: Transcription Outcome Provenance

The connector SHALL populate the `connector-base-spec` `ingest.v1` envelope's optional
`payload.transcription` field for every voice message it evaluates under this capability (every row
of the eligibility/bounds/failure taxonomy above, including successful transcription), so a
downstream consumer can distinguish the reason a transcript is or is not present without inferring it
from `normalized_text` alone.

#### Scenario: Provenance recorded for every evaluated outcome

- **WHEN** the connector evaluates a `voice` message under this capability
- **THEN** `payload.transcription.source_media = "voice"`, `status` is set to the matching outcome
  (`"transcribed"`, `"not_owner"`, `"disabled"`, `"oversize"`, `"overlong"`, `"download_failed"`,
  `"transcode_failed"`, `"stt_unavailable"`, or `"empty_or_low_confidence"`), and `confidence`,
  `language`, and `stt_backend` are populated when `status = "transcribed"` and `null` otherwise

### Requirement: Unconditional In-Memory-Only Audio Disposal

Downloaded and transcoded audio bytes SHALL exist only as in-process byte buffers for the duration of
one acquisition/transcode/transcribe sequence, on every exit path (success, any fallback case, or an
unexpected exception), and SHALL NOT be written to disk, included in `payload.raw`, referenced by an
`IngestAttachment.storage_ref`, or included in any log record, exception message, metric label, or
trace context.

#### Scenario: Disposal on every exit path

- **WHEN** the acquisition/transcode/transcribe sequence completes, fails at any stage, or raises an
  unexpected exception
- **THEN** no audio byte buffer, temporary file, attachment reference, or log/metric/trace record
  referencing the audio content survives that sequence

#### Scenario: No attachment minted for voice audio

- **WHEN** a voice note is evaluated under this capability, regardless of outcome
- **THEN** the resulting `ingest.v1` envelope's `payload.attachments` contains no entry referencing
  the voice audio (Telegram's own `file_id` is not persisted as an `IngestAttachment.storage_ref` by
  this capability)
