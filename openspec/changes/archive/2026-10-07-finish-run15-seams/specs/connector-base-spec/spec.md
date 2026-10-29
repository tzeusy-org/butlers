## MODIFIED Requirements

### Requirement: Bearer material never persists

Connectors and the Switchboard ingest boundary SHALL withhold bearer material (one-time codes,
password-reset links, magic links, and Telegram login codes) from every persisted payload. Only the
fact that an auth artifact arrived SHALL be kept, as a typed placeholder recording the artifact kind,
the provider domain, and the observation time.

FilteredEventBuffer.record SHALL also apply the shared detector to subject_or_preview before appending it for persistence, deriving provider domain and service-sender aggressive behavior from its existing source metadata. Detector failure SHALL withhold that preview without changing the drop decision or retaining its original text.

The preview seam SHALL treat provider and sender-domain labels as untrusted hints. It SHALL validate every emitted placeholder label, including historical unsafe placeholders, and use a fixed connector fallback or unknown rather than reflect URLs, paths, code-bearing numeric labels or a detected link secret. Genuine safe provider domains SHALL remain visible. This preview-only label policy SHALL NOT rewrite the shared detector's other callers or alter source metadata and replay payloads.

#### Scenario: One-time code is replaced by a placeholder
- **WHEN** an `ingest.v1` envelope whose text contains a one-time code (for example "Your code is 482913") reaches the ingest boundary
- **THEN** `message_inbox.raw_payload` and `message_inbox.normalized_text` SHALL NOT contain the code
- **AND** they SHALL contain a placeholder of the form `[auth-code withheld: <provider-domain>]`
- **AND** `raw_payload.control.bearer_scrubbed` SHALL be `true` and `raw_payload.bearer_artifacts` SHALL list `{kind, provider_domain, observed_at}` records
- **AND** the message SHALL still be routed by the unchanged policy decision

#### Scenario: Ordinary numbers survive
- **WHEN** a message contains order numbers, dates, or amounts that are not adjacent to code wording and not part of a reset or magic link
- **THEN** the text SHALL be persisted unchanged

#### Scenario: Scrubbing is idempotent
- **WHEN** already-scrubbed text is scrubbed again
- **THEN** no further change SHALL occur

#### Scenario: Detector failure fails closed
- **WHEN** the scrubber raises while processing an envelope
- **THEN** the ingest boundary SHALL persist the message as metadata-only with `payload.raw` null and SHALL NOT persist the unscrubbed text

#### Scenario: Connectors scrub before submit
- **WHEN** a connector builds an `ingest.v1` envelope
- **THEN** it SHOULD withhold bearer material from `payload.raw` and `payload.normalized_text` and set `control.bearer_scrubbed` to `true` when it did so
- **AND** the Switchboard SHALL scrub again at ingest, so a connector that omits scrubbing does not cause persistence

#### Scenario: Dropped Gmail subject is scrubbed at the shared record seam
- **WHEN** a blocked Gmail subject contains an auth code and is recorded for the filtered-event buffer
- **THEN** the actual persisted subject_or_preview SHALL contain the typed provider-domain placeholder and SHALL NOT contain the code
- **AND** the filtered row's metadata, status, reason and existing payload tier SHALL remain intact

#### Scenario: Telegram service sender preview receives aggressive scrubbing
- **WHEN** a preview from actual numeric-string sender 777000 contains a 4–8-digit login code without auth wording
- **THEN** the shared record seam SHALL apply the detector's service-sender aggressive mode before persistence
- **AND** the typed placeholder SHALL persist and the original code SHALL be absent

#### Scenario: Central preview scrubbing preserves benign text and idempotence
- **WHEN** an ordinary non-service preview contains benign numbers/dates, is null, or is already scrubbed
- **THEN** the current detector's benign-number/date and placeholder rules SHALL remain in force and null SHALL remain null
- **AND** a planted benign row SHALL still persist, so an empty query cannot satisfy the privacy assertion

#### Scenario: Central preview detector failure withholds only the preview
- **WHEN** source hint extraction or the detector raises while preparing subject_or_preview
- **THEN** the record seam SHALL withhold the preview and SHALL NOT enqueue, persist or log its original text
- **AND** the metadata row and unchanged filter decision SHALL remain available

#### Scenario: Untrusted labels cannot reintroduce withheld bearer material
- **WHEN** current or legacy source/provider or sender hints contain a code-bearing path or DNS label, a URL with a bearer token, or a detected link secret, or an old placeholder already contains such an unsafe label
- **THEN** the current record seam and historical preview command SHALL persist a typed placeholder with a fixed safe fallback label and SHALL NOT retain the code or token in that preview
- **AND** genuine safe domains, nonstring-hint fallback, benign text, metadata and payloads SHALL retain their defined behavior
- **AND** detector failure SHALL withhold the preview without logging the raw hint, preview or error message

#### Scenario: One-shot historical preview scrub is bounded and honestly reported
- **WHEN** the separately released one-shot maintenance command processes a fixed historical cutoff
- **THEN** it SHALL reuse the shared preview detector, update only subject_or_preview in locked keyset batches, preserve other columns and report content-free complete/incomplete counts
- **AND** a complete rerun SHALL change zero rows; rollback/restart/unknown acknowledgements SHALL not skip a row or fabricate completion
- **AND** source-only implementation SHALL NOT imply live execution or reversal of already redacted bearer material
