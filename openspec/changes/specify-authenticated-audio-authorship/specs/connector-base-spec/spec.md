## ADDED Requirements

### Requirement: Audio outcome metadata does not attest authorship
A transcription-capable ingress producer SHALL keep the existing `payload.transcription` outcome
fields separate from the authenticated server-held evidence of `REQ-audio-authorship-002`.
`source_media`, `status`, `confidence`, `language` and `stt_backend` SHALL remain outcome metadata,
not proof of who authored the input or which trusted transform actually ran. The producer SHALL
preserve original sender, source identity, forward/quote/embedded markers and existing dedupe
keys through transcription; it SHALL bind exact transcript content without placing credentials,
audio bytes or private authorization material in outcome fields. Unknown legacy provenance
SHALL NOT be reconstructed as owner authorship from outcome metadata.

ID: REQ-connector-base-spec-008
Source: RFC 0038 D5/D18 proposed audio amendment; REQ-audio-authorship-002; existing Transcription Outcome Payload Field
Scope: v1-mandatory

#### Scenario: Existing outcome metadata is present
- **WHEN** an envelope reports successful transcription and a backend name but lacks independently bound ingress evidence
- **THEN** those fields remain diagnostic metadata and do not authorize an owner transcript

#### Scenario: Provider media and quoted content become text
- **WHEN** the connector produces a transcript for an attachment carrying forwarded, quoted or embedded content
- **THEN** it preserves the source identity and sender, marks the affected transcript as untrusted and keeps the existing dedupe key
- **AND** unavailable exact span mapping marks the entire affected transcript rather than dropping the marker

#### Scenario: Ordinary envelopes remain compatible
- **WHEN** a message has no evaluated transcription attempt
- **THEN** its transcription outcome field remains absent and its existing text, attachment, deduplication and origin handling is unchanged
