# Connector Base Spec — Delta

## ADDED Requirements

### Requirement: Transcription Outcome Payload Field

The `ingest.v1` envelope's `payload` SHALL support an optional `transcription` object that a
transcription-capable connector populates to record the outcome of its own speech-to-text attempt.
This field is independent of, and never derived from, a provider's own raw payload — it exists only
for outcome metadata the provider itself does not report. This requirement is additive: the existing
`ingest.v1 Envelope Schema` requirement and its scenarios (including `payload.raw`, `payload.
normalized_text`, and `payload.attachments`) are unchanged, and `payload.transcription` is absent for
every message no connector-side transcription attempt was made against.

#### Scenario: Field absent by default

- **WHEN** an `ingest.v1` envelope is constructed for a message no connector-side transcription
  attempt was evaluated against
- **THEN** `payload.transcription` is absent (or `None`), and existing consumers reading only
  `payload.raw`, `payload.normalized_text`, and `payload.attachments` are unaffected

#### Scenario: Field populated for an evaluated transcription attempt

- **WHEN** a connector evaluates a message for its own transcription capability (for example, the
  `telegram-voice-transcription` capability evaluating a Telegram `voice` message)
- **THEN** `payload.transcription` is populated with `source_media` (a string naming the media kind,
  e.g. `"voice"`), `status` (a connector-defined outcome string), `confidence` (float in `[0, 1]` or
  `null`), `language` (BCP-47 string or `null`), and `stt_backend` (string or `null`)

#### Scenario: Field never carries audio bytes

- **WHEN** `payload.transcription` is populated
- **THEN** it contains only text/numeric/string outcome metadata — never raw or encoded audio bytes,
  and never a reference (storage or otherwise) to the original audio
