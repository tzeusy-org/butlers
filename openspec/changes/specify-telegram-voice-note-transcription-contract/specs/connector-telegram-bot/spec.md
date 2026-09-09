# Telegram Bot Connector — Delta

## MODIFIED Requirements

### Requirement: Tiered Text Extraction
The connector SHALL extract human-readable text from Telegram messages using a four-tier fallback strategy.

#### Scenario: Tier 1 — text field
- **WHEN** a Telegram message has a `text` field
- **THEN** it is used as `payload.normalized_text`

#### Scenario: Tier 2 — caption field
- **WHEN** a message has no `text` but has a `caption` (media with caption)
- **THEN** the caption is used as `payload.normalized_text`

#### Scenario: Tier 3 — media type descriptor
- **WHEN** a message has neither text nor caption but contains media
- **THEN** a synthesized descriptor is generated from `_MEDIA_TYPE_LABELS`:
  - `[Photo]`, `[Video]`, `[Document]`, `[Audio]`, `[GIF]`, `[Location]`, `[Dice]`
  - `[Voice message]`, `[Video message]`
  - `[Sticker: <emoji>]` (includes the sticker's emoji)
  - `[Contact: <name>]` (includes the contact's display name)
  - `[Poll: <question>]` (includes the poll question text)

#### Scenario: Tier 3 supersession — voice-note transcription
- **WHEN** a message's only extractable content is a `voice` field, and the
  `telegram-voice-transcription` capability's eligibility gate, media bounds, and STT call all
  succeed with acceptable confidence for that note
- **THEN** the transcript text supersedes the `[Voice message]` descriptor as `payload.normalized_text`
  (per that capability's Successful Transcript requirement)
- **AND** every other case — an ineligible sender, a disabled feature, an oversize or overlong note,
  a download/transcode/STT failure, or an empty/low-confidence result — leaves this Tier 3 scenario's
  `[Voice message]` descriptor exactly as specified above, unconditional and unchanged

#### Scenario: Tier 4 — service messages (skipped)
- **WHEN** a message has no extractable content (service messages, non-message updates)
- **THEN** the update is silently skipped — no ingest submission
