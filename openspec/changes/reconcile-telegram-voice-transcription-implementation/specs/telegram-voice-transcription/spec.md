# Telegram Voice-Note Transcription — Delta

## ADDED Requirements

### Requirement: Voice-Note Transcription Configuration Surface
The connector SHALL expose the draft's disabled-by-default opt-in configuration only after exact contract acceptance. Enabling requires an explicitly configured URL for the already approved STT service; no example hostname or localhost factory default SHALL be interpreted as the deployed backend. The protocol defaults to wyoming and the duration ceiling defaults to 120 seconds. No credential lookup, deployment configuration or enabled posture SHALL change automatically.

#### Scenario: Feature disabled
- **WHEN** the feature is not explicitly enabled
- **THEN** existing voice-descriptor behavior remains and no acquisition, decoding or STT occurs

#### Scenario: Explicitly configured existing service
- **WHEN** the operator enables the approved feature
- **THEN** its configured service URL is required and one existing TranscriptionClient is used; no new service or account is created

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
- **THEN** the connector makes no `getFile` or download call, and the note produces a typed operational failure

#### Scenario: Overlong note rejected before download

- **WHEN** an eligible voice note's `Voice.duration` exceeds `CONNECTOR_TELEGRAM_VOICE_MAX_DURATION_S`
- **THEN** the connector makes no `getFile` or download call, and the note produces a typed operational failure

#### Scenario: Post-download oversize backstop

- **WHEN** `Voice.file_size` was absent or understated and the actual downloaded byte count exceeds
  20 MB
- **THEN** the connector discards the downloaded bytes immediately without transcoding, and the
  note produces a typed oversize operational failure, with no placeholder routed as owner speech

### Requirement: Transcoding to the TranscriptionClient PCM Contract
The connector SHALL decode the admitted voice bytes using a fixed argument-list ffmpeg subprocess with pipe-only input and output, restricted input protocols, no temporary file, raw 16000 Hz mono signed 16-bit little-endian PCM, a maximum decoded size of 3840000 bytes for the 120-second profile, and a proposed 90-second total operation deadline, including queue wait, and a separate five-second owned cleanup ceiling (TERM for two seconds, then KILL and reaping for three seconds). These proposed bounds require exact contract acceptance and do not constitute runtime calibration. Reported duration and file size SHALL be strict nonnegative integers, never bools or coerced strings. Decode failure, deadline, cancellation and oversize output SHALL settle the actual owned process before releasing the worker slot; unrelated processes SHALL remain untouched.

#### Scenario: Successful bounded decode
- **WHEN** an admitted clip is at most 120 seconds and within the encoded limit
- **THEN** only bounded in-memory PCM is passed once to the existing STT client

#### Scenario: Untrusted container or resistant decoder
- **WHEN** decode fails, exceeds a byte/deadline bound or is cancelled
- **THEN** the owned decoder is terminated and reaped with finite cleanup, buffers are discarded and no private stderr, audio or file handle is retained

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
- **THEN** shared connector-owned serialization protects the complete protocol lifecycle, using the existing client lock where applicable, without opening a second connection or corrupting shared connection state

#### Scenario: No usable result after the client's own bounded attempt

- **WHEN** `transcribe()` returns `None` after the client's own internal reconnect-and-retry attempt
- **THEN** the connector makes no further retry attempt, and the note produces a typed operational failure

### Requirement: Empty or Low-Confidence Result Failure
The connector SHALL treat a None, blank or invalid transcription result as an operational failure for an admitted enabled owner voice note, without fabricating a transcript, confidence or more precise backend failure reason than observed.

#### Scenario: No usable transcription result
- **WHEN** the existing STT call yields no usable result
- **THEN** the connector produces the typed failure outcome and does not route a Voice message placeholder as owner speech

### Requirement: Successful Transcript Supersedes the Descriptor as Real Content

When a voice note is eligible, within bounds, and successfully transcribed with acceptable
confidence under the existing client policy, the connector SHALL set `normalized_text` to the transcript text itself, unwrapped by any
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
  `"transcode_failed"`, `"stt_unavailable"`, `"empty_or_low_confidence"`, `"stt_no_result"`, `"invalid_metadata"`, `"deadline"`, or `"cancelled"`), and `confidence`,
  `language`, and `stt_backend` are populated when `status = "transcribed"` and `null` otherwise

### Requirement: Unconditional In-Memory-Only Audio Disposal

Downloaded and transcoded audio bytes SHALL exist only in bounded ephemeral process buffers and owned decoder pipes for the duration of
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
### Requirement: Admission Before Voice Audio Effects
The connector SHALL evaluate existing connector and global policy before any voice getFile, download, decoder or STT effect. A block, global skip, metadata-only decision, unresolved owner or disabled feature SHALL not initiate audio effects. Sender ownership SHALL come from the existing definer lookup on the real provider sender, never from chat identity or a caller owner flag. Existing nonvoice processing and filtered-content privacy SHALL remain unchanged.

#### Scenario: Denied or metadata-only voice
- **WHEN** admission denies full voice content
- **THEN** no acquisition, decode or STT occurs and the existing filtered or metadata-only outcome is preserved

#### Scenario: Admitted owner voice
- **WHEN** existing policy permits full content and the real sender resolves uniquely to the owner
- **THEN** strict bounds are checked before any acquisition effect

### Requirement: Bounded Acquisition and Single Operation Ownership
The connector SHALL stream at most 20000000 encoded bytes, enforce the same bound after omitted or understated provider metadata, and close the response immediately on overflow. It SHALL apply one finite operation deadline including queue wait, acquisition, decoding and STT, preserve the existing concurrency limit, and add no STT retry layer. Cancellation SHALL settle decoder ownership and invalidate a partial STT session before another caller can consume it.

#### Scenario: Streaming backstop
- **WHEN** the response exceeds the encoded limit despite metadata
- **THEN** reading stops, response and buffers are released, and a typed oversize failure is produced

#### Scenario: Interrupted persistent STT session
- **WHEN** an admitted operation is cancelled with an incomplete protocol response
- **THEN** that session is settled or disconnected under shared ownership before the next operation; responses cannot cross voice-note identities

### Requirement: Deterministic Voice Failure Reply
For enabled owner voice notes admitted to full-content processing, a bounds, download, decode or STT failure SHALL produce a typed operational failure, never a fabricated owner transcript or a Voice message placeholder routed for classification. Switchboard SHALL validate accepted connector/source/sender lineage and dispatch the fixed text Could not transcribe voice message through the existing notify reply and Messenger boundary. The connector SHALL not call sendMessage or grant itself outbound authority. An LLM noticing metadata SHALL not be the sole failure-delivery mechanism. The normal sender, routing and content-authority path SHALL remain unchanged for successful transcripts.

#### Scenario: Admitted voice failure
- **WHEN** a strict typed failure arrives from an admitted owner voice source
- **THEN** deterministic existing-boundary reply dispatch occurs using server-derived original message lineage, without owner-content capture or LLM classification of the operational error

#### Scenario: Failed or duplicate dispatch
- **WHEN** submission is duplicate or reply delivery is unavailable
- **THEN** duplicate acceptance does not schedule a second effect, delivery failure is retained for bounded existing recovery, and accepted dispatch is not claimed as observed user receipt

### Requirement: Diagnostic and Audio Privacy
The voice path SHALL retain no encoded or PCM audio, audio hash/excerpt, new attachment or media_refs entry, temporary file, private exception detail, decoder stderr or token-bearing URL in durable storage, logs, traces or metrics. Reusing the existing STT client SHALL not expose its discarded transcript excerpt logs for this path. Provider metadata retention SHALL be explicitly covered by the reviewed privacy contract; this proposal retains existing provider metadata, not audio bytes, and creates no additional refetch handle.

#### Scenario: Failure and cancellation disposal
- **WHEN** any stage succeeds, fails or is cancelled
- **THEN** audio buffers and owned resources are released and only closed diagnostic reasons are recorded
