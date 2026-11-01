## ADDED Requirements

### Requirement: Instruction-free ASR preserves only authenticated source authorship
Goal admission SHALL treat a successful transcript of the verified owner's own non-forwarded
Telegram voice note, Telegram audio file or authenticated dashboard recording as owner input
only when trusted ingress evidence establishes the source and an instruction-free ASR-class
transform. The transform SHALL receive audio and fixed decoding configuration, no user or model
instructions, conversational prompt, tools or capability access; it SHALL transcribe rather than
compose, summarize, translate or answer. The transform SHALL preserve third-party content as
third-party and SHALL NOT erase forwarding, quotation, embedding or other exposure evidence.
A tool-less general-purpose instructable model SHALL NOT qualify merely because it was asked to
transcribe or returned a backend label. Owner input SHALL retain all ordinary approval policies
and SHALL NOT receive capabilities unavailable to typed owner input.

ID: REQ-audio-authorship-001
Source: RFC 0038 D5 and D18 proposed audio amendment; owner decision bu-2utmy5
Scope: v1-mandatory

#### Scenario: The owner sends a non-forwarded voice note
- **WHEN** trusted Telegram ingress verifies the owner sender and an unmarked voice note and its qualified ASR transform succeeds
- **THEN** the exact transcript is admitted as owner input with transcribed provenance and the ordinary owner ceiling
- **AND** later untrusted reads and existing approval policies still apply

#### Scenario: The owner sends an audio file
- **WHEN** trusted Telegram ingress verifies the owner's non-forwarded audio file and qualified ASR succeeds within the configured bounds
- **THEN** its exact transcript has the same source authorship as the owner's voice note, without a distinct authority ceiling

#### Scenario: Forwarded audio has an owner sender
- **WHEN** the owner forwards someone else's audio and a transcript is available
- **THEN** that transcript remains third-party data and the enclosing owner-origin goal starts exposed
- **AND** the forwarding owner remains the envelope sender rather than being replaced with the forwarded speaker

#### Scenario: Quotation or embedding survives transformation
- **WHEN** provider evidence marks quoted or embedded content in audio, text or a photo, or its caption
- **THEN** that content remains fenced as third-party data and the goal starts exposed
- **AND** if exact transformed boundaries cannot be established the whole affected transcript is marked, without discarding independent owner text

#### Scenario: Non-owner and room sources cannot become owner input
- **WHEN** an audio source is a non-owner message or room/live-listener capture, even if its words claim to be the owner
- **THEN** transcription supplies no owner-origin authorization and the material remains third-party
- **AND** any otherwise permitted admission retains its existing source ceiling and exposure rules

#### Scenario: An instructable model claims ASR provenance
- **WHEN** an instructable, summarizing, translating or otherwise unqualified backend produces a transcript-shaped result
- **THEN** its output remains third-party model-function content and supplies no owner-authorship evidence

### Requirement: Admission uses bound ingress evidence and replay cannot upgrade authorship
Admission SHALL accept audio authorship evidence only from its authenticated ingress producer's
server-held context, bound to the original ingest deduplication identity, actual source media,
verified sender, forward/quote/embedded markers, transform identity and successful outcome, and
exact transcript digest. Request bodies, transcript words, provider-supplied metadata and
`payload.transcription` SHALL NOT establish that evidence. Missing, malformed, mismatched or
unverifiable evidence SHALL NOT yield owner-authored transcript input. The accepted evidence
and resulting origin/exposure decision SHALL remain associated with the durable accepted
submission; retries or replays SHALL use the existing deduplication/admission result without
upgrading its authorship or lowering exposure.

ID: REQ-audio-authorship-002
Source: RFC 0038 D5, D6 and D18 proposed audio amendment; REQ-capability-authorization-003
Scope: v1-mandatory

#### Scenario: The payload forges an owner or backend assertion
- **WHEN** payload text or client fields claim owner origin or a trusted ASR identity without matching server-held evidence
- **THEN** admission does not treat the transcript as owner input and no such claim changes the ceiling

#### Scenario: A valid attestation is attached to different content
- **WHEN** the original ingest identity, media binding, transform outcome or transcript digest differs from the bound evidence
- **THEN** the transcript cannot receive owner authorship from that evidence

#### Scenario: A third-party submission is replayed as owner audio
- **WHEN** a retry or operator replay of the same original ingest identity supplies a new owner label or later successful transcription
- **THEN** the existing accepted goal and origin/exposure decision are returned unchanged, with no second goal or authority upgrade

### Requirement: Failed or bounded transcription supplies no invented utterance
Unavailable, timed-out, cancelled, rejected, empty or unacceptable transcription SHALL NOT create
spoken words, owner-authorship evidence or a successful transform outcome. An independently
authenticated caption or typed message SHALL retain its own existing handling; an attachment
or closed failure status SHALL NOT masquerade as owner speech. Transcription SHALL retain
finite engineering-configured byte, decoded-duration and execution bounds and owned cleanup;
those values are resource controls, not a new owner modality limit. Failure handling SHALL
preserve existing ingest deduplication and privacy rules and SHALL NOT call an outside speech
service or silently fall back to an instructable backend.

ID: REQ-audio-authorship-003
Source: RFC 0038 D18 proposed audio amendment; owner decision bu-2utmy5; craft-and-care finite execution bounds
Scope: v1-mandatory

#### Scenario: STT is unavailable or produces no acceptable words
- **WHEN** the configured local transform fails, is unavailable, returns an empty or unacceptable result, or is cancelled
- **THEN** no transcript words or owner-audio goal are fabricated, and existing attachment/caption and failure reporting remain available
- **AND** the failure does not authorize a different backend or erase source markers

#### Scenario: An owner file exceeds an engineering bound
- **WHEN** an owner audio file exceeds the configured finite byte, duration or execution bound
- **THEN** processing ends with its explicit non-success outcome and owned cleanup rather than unbounded transcription
- **AND** changing a bound later does not change the source's authorship rules or create a second goal for an already accepted submission
