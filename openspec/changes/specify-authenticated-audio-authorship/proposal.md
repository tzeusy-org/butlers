## Why

The owner approved treating speech-to-text of their own non-forwarded Telegram voice notes,
Telegram audio files and dashboard recordings as their input (`bu-2utmy5`, 2026-10-20
14:37Z). RFC 0038 D18 still treats every model-function output as third-party content.
This contract specifies the narrow authorship-preserving exception without trusting transcript
text, provider payload claims or a general-purpose model's assertion of who spoke.

## What Changes

- Propose exact RFC 0038 D5/D18 and `about/heart-and-soul/v1.md` amendments: an
  instruction-free ASR-class transform preserves authenticated source authorship, never upgrades it.
- Specify connector-held evidence bound to the original ingest identity, source media and exact
  transcript; keep `payload.transcription` an outcome description, never an authorization token.
- Add Telegram voice/audio and authenticated dashboard-recording ingress scenarios, including
  forwarding/quoting, non-owner and room material, unsupported transforms, outage and replay.
- Preserve finite engineering-configured size/duration/execution bounds, existing deduplication,
  attachment degradation and owner-text handling. Do not invent words when transcription fails.

## Capabilities

### New Capabilities

- `audio-authorship`: deterministic provenance and admission of an authorship-preserving audio
  transform, including conservative failure and replay behavior.

### Modified Capabilities

- `connector-base-spec`: an additive authorship-evidence boundary beside the existing transcription
  outcome contract; no rewrite of that contract or the ingest envelope's existing fields.
- `connector-telegram-bot`: preserve sender/forward/quote/media evidence for owner voice and audio.
- `dashboard-api`: bind an authenticated owner's recording to its actual request and bytes before
  transcription; client-supplied origin and backend labels confer no authority.

## Impact

This is a specification-only draft. The recorded modality choice is settled; **exact RFC and v1
wording is not adopted**. Independent normative/conformance review and explicit owner exact-text
adoption are required before closure or authority-bearing implementation. The DRAFT PR is not
that adoption. Baseline: `b725c9ff0276bd49889eb3bbbef821dc078e28a5`.

Existing `REQ-capability-authorization-003` and `REQ-connector-base-spec-004` continue governing
origin/exposure and untrusted spans; this change complements them rather than restating their
active requirements. The existing `specify-telegram-voice-note-transcription-contract` change and
`bu-r0dsz` keep their independently approved dark voice-note transport scope, including their
current engineering bounds and fallback behavior. `bu-dhloi1.20` owns the later authority-bearing
integration after adoption. The scenarios name future producer/admission regression seams, not
runtime evidence from this specification task. Tests: +0 ~0 -0.

Out of scope: production code or tests, deploying or selecting an STT service, outside speech
services, room/household voice, speaker recognition, duplex/realtime voice, and TTS or spoken
replies (separately owned). No existing forwarding or room restriction is released. The separate
RFC 0009/context-security amendment and its files are untouched.
