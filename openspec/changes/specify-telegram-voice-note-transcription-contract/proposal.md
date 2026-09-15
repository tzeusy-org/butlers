# Specify Telegram voice-note transcription contract

## Why

`bu-r0dsz` ("Transcribe Telegram voice notes via existing STT") asks for one concrete outcome:
Telegram voice notes currently arrive as the literal string `[Voice message]` — the audio content
is discarded before it ever reaches a butler — and the owner should instead be able to speak to
butlers naturally. Its acceptance criteria name seven bounds: transcribe-and-route, no more literal
placeholder for transcribed notes, reuse of existing STT infrastructure (no new service), voice-origin
metadata on the message, a user-visible failure signal (not a silent drop), no audio persisted after
transcription, and support for typical voice notes up to about two minutes.

The 2026-09-06 `bu-27dxl.9` shaping pass identified this outcome as REMAINING (`connector-telegram-bot`
still specifies the literal placeholder, and a reusable `TranscriptionClient` exists under
`connectors/live_listener` with no Telegram acquisition/transcoding/privacy contract authorizing its
reuse) and proposed exactly this OpenSpec-first prerequisite before `bu-r0dsz` is dispatched. The
binding coordinator review (`coordinator-review.md`,
`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/`) did not revisit this
item; it stands as proposed. This draft is that prerequisite. It defines supported owner voice-note
acquisition, media bounds, the existing STT client's reuse contract, retry/dedup/failure/provenance
behavior, and unconditional audio disposal — without implementing any of it, without any new STT
service, ambient microphone, speaker identification, saved audio, OCR, or provider/runtime execution,
and without message delivery, activation, or deployment. Only this draft-spec PR is authorized.

`bu-r0dsz` remains open and untouched by this draft; it is the eventual implementation this contract
governs.

## What Changes

- Add a new `telegram-voice-transcription` capability defining: an owner-only eligibility gate (reusing
  the existing fast, classification-independent owner lookup), media acquisition bounds (file size,
  duration, format-agnostic transcoding via the standard container/codec transcoder rather than a
  Telegram-specific decoder), reuse of the existing `connectors/live_listener/transcription.py`
  `TranscriptionClient` interface as a single-shot call (not the ambient streaming pipeline it was
  built for), a conservative disabled-by-default configuration surface, the exact failure taxonomy and
  its placeholder-preserving fallback, in-memory-only audio handling with unconditional disposal, and
  the connector-level dedup/idempotence posture inherited from the existing ingest dedup key.
- Modify `connector-telegram-bot`'s existing **Tiered Text Extraction** requirement to add one new
  scenario: for an eligible, successfully transcribed owner voice note, the transcript text supersedes
  the Tier 3 `[Voice message]` descriptor as `payload.normalized_text`. Every existing scenario
  (including the literal `[Voice message]` descriptor, which remains the exact fallback for every
  ineligible sender, oversize/overlong note, disabled configuration, or transcription failure) is
  preserved verbatim and unconditional for those cases.
- Add one new, narrowly-scoped `## ADDED Requirements` block to `connector-base-spec`'s `ingest.v1`
  envelope contract: an optional `payload.transcription` object that any transcription-capable
  connector may populate, carrying STT outcome metadata (status, confidence, language, backend) that
  is not present in a provider's own raw payload. This does not modify the existing `ingest.v1
  Envelope Schema` requirement's scenarios; it is a wholly new, independent requirement.
- No RFC: this generalizes `connector-telegram-bot`/`connector-base-spec` the same way the existing
  tiered-extraction and attachment-metadata behavior was introduced — as a capability-spec addition,
  with no RFC of its own.

## Capabilities

### New Capabilities

- `telegram-voice-transcription`: the acquisition/bounds/STT-reuse/failure/provenance/disposal
  contract for transcribing owner-sent Telegram voice notes.

### Modified Capabilities

- `connector-telegram-bot`: the Tiered Text Extraction requirement gains one additive scenario for
  transcript supersession (existing scenarios, including the `[Voice message]` fallback, unchanged).

### New Requirements on Existing Capabilities

- `connector-base-spec`: one new, independent ADDED requirement defining the optional
  `payload.transcription` envelope field. No existing requirement or scenario is modified.

## Impact

- Affected future code (not touched by this draft): `src/butlers/connectors/telegram_bot.py` (new
  async voice-acquisition/transcode/transcribe path inside `_normalize_to_ingest_v1`, which becomes
  async), a new bounded `ffmpeg` subprocess transcoding helper, and `Dockerfile.base` (adding the
  `ffmpeg` system package — not installed today, verified by inspection). No `module-telegram` change
  is needed: unlike the receipt-acknowledgement contract's outbound typing/reaction dispatch, voice
  acquisition is inbound normalization, the same category of work `connector-telegram-bot` already
  performs directly (see design.md D1).
- Affected RFCs: none. This is a capability-spec-level contract, matching how tiered text extraction
  and attachment metadata were introduced without a dedicated RFC.
- `bu-r0dsz` disposition: remains open, untouched. This draft is the prerequisite its eventual
  implementation must satisfy; all seven original acceptance criteria are preserved.
- No implementation, runtime, provider/account/credential/data access, message delivery, activation,
  deployment, or merge is performed or authorized by this change. Expected tests: `+0 ~0 -0`.
