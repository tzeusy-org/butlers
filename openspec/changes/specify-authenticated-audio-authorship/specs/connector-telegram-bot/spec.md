## ADDED Requirements

### Requirement: Telegram audio authorship comes from verified update provenance
Telegram voice notes and audio files SHALL follow `REQ-audio-authorship-001` through
`REQ-audio-authorship-003`. The trusted connector SHALL derive sender and forward/quote/embedded
markers from the actual provider update and owner-channel authorization, never from caption,
filename, transcript text or sender-provided assertions. Audio processing SHALL preserve the
existing Telegram message identity, conversation identity, reply target and cross-connector
idempotency key. This authorship contract SHALL NOT silently expand the separately approved
dark voice-note transport's enabled media types, configuration or engineering bounds.

ID: REQ-connector-telegram-bot-002
Source: RFC 0038 D5/D18 proposed audio amendment; RFC 0017; owner decision bu-2utmy5
Scope: v1-mandatory

#### Scenario: Native voice and audio have the same authorization boundary
- **WHEN** an owner-authorized non-forwarded Telegram voice note or audio file reaches a supported qualified transform
- **THEN** successful text is eligible for owner-input admission with the original Telegram dedupe identity and reply target
- **AND** a caption supplies neither transform identity nor evidence of who authored the audio

#### Scenario: An owner forwards media or quotes another message
- **WHEN** actual Telegram update metadata identifies forwarded or quoted audio, text, photo or caption content
- **THEN** the corresponding material remains third-party with preserved untrusted markers and exposed admission
- **AND** the enclosing owner sender does not confer owner authorship on that content

#### Scenario: Legacy or unsupported media has no qualified transform
- **WHEN** the dark transport does not support the media type or returns a failed or unavailable transform
- **THEN** its existing attachment, caption and explicit failure/fallback behavior remains intact, without a fabricated owner transcript
