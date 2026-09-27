## 1. Draft the contract

- [x] 1.1 Specify the owner-only eligibility gate, reusing the sibling ack contract's
  `identity.resolve_owner_channel_via_definer` precedent, evaluated before any Telegram network call.
- [x] 1.2 Specify media acquisition bounds (20 MB `getFile` ceiling, configurable duration ceiling
  defaulting to 120s) checked from `Voice.file_size`/`Voice.duration` before any network call, plus a
  post-download actual-byte-count check as a backstop.
- [x] 1.3 Specify the `ffmpeg` subprocess transcoding seam (argument-list invocation, stdin/stdout
  pipes, no temp file, bounded timeout) grounded against `TranscriptionClient`'s documented PCM input
  contract, and name that `ffmpeg` is not installed anywhere in this repository today (live
  inspection, not assumption).
- [x] 1.4 Specify reuse of `connectors/live_listener/transcription.py::TranscriptionClient` as a
  single-shot call against one persistent, connector-owned instance, inheriting its existing
  reconnect/backoff and lock-serialization behavior rather than adding a second retry layer.
- [x] 1.5 Specify the disabled-by-default configuration surface
  (`CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED`/`_URL`/`_PROTOCOL`) and its non-regression
  rationale.
- [x] 1.6 Specify the full failure taxonomy (owner gate, disabled, oversize, overlong, download
  failure, transcode failure, STT unavailable, empty/low-confidence) and its unconditional fallback to
  the existing `[Voice message]` placeholder — never a silent drop.
- [x] 1.7 Specify that a successful transcript replaces the descriptor as real `normalized_text`
  content (no bracket-wrapping), distinct from every other synthesized Tier 3 descriptor.
- [x] 1.8 Specify provenance: `payload.raw.message.voice` already satisfies voice-origin metadata
  with zero schema change; add exactly one new, independent `payload.transcription` field for STT
  outcome metadata not present in Telegram's own payload.
- [x] 1.9 Specify unconditional in-memory-only audio disposal on every exit path (success, every
  failure row, and unexpected exceptions), including a rule against logging audio bytes.
- [x] 1.10 Draft additive-only deltas: a `## MODIFIED Requirements` block for
  `connector-telegram-bot`'s Tiered Text Extraction requirement that preserves every existing scenario
  verbatim while adding one new superseding scenario, and a `## ADDED Requirements` block for
  `connector-base-spec` defining `payload.transcription` without touching the existing `ingest.v1
  Envelope Schema` requirement.
- [x] 1.11 Confirm `bu-r0dsz`'s disposition: remains open and untouched; this draft is its
  prerequisite and performs no Beads mutation.
- [x] 1.12 Confirm no other unarchived OpenSpec change carries a same-named requirement block this
  draft would collide with (checked: no other unarchived change touches `ingest.v1 Envelope Schema`
  or `Tiered Text Extraction`).

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic review of this draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed artifact. Any
  semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Keep any implementation, migration, credential, or live Telegram/STT API call blocked until
  this contract is accepted and its own separate authorities are satisfied.

## 3. Future implementation after approval (`bu-r0dsz` or an approved successor)

- [ ] 3.1 Add `ffmpeg` to `Dockerfile.base`'s system package list.
- [ ] 3.2 Add the disabled-by-default voice-transcription config surface to
  `src/butlers/connectors/telegram_bot.py` (env vars from D2/D4) and construct one persistent
  `TranscriptionClient` instance at connector startup when enabled.
- [ ] 3.3 Make `_normalize_to_ingest_v1` async; add the owner-gate → bounds-check → download →
  transcode → transcribe → fallback/success pipeline as a new step feeding Tier 3 (superseding
  scenario from the connector-telegram-bot delta).
- [ ] 3.4 Add the bounded `ffmpeg` subprocess helper (argument-list, piped, timeout-bounded).
- [ ] 3.5 Populate `payload.transcription` per D7/D5 for every voice message this capability
  evaluates, including every fallback row.
- [ ] 3.6 Add per-outcome metrics mirroring the live-listener connector's
  `_transcription_failures_total`/`_discarded_total` pattern, labeled by the D5 status taxonomy.

## 4. Future verification after approval (`bu-r0dsz` or an approved successor)

- [ ] 4.1 Unit tests: bounds pre-checks, disabled-by-default gate, owner-only gate, and
  `payload.transcription` population for every D5 row.
- [ ] 4.2 Contract/API tests: `ffmpeg` invocation is argument-list (never `shell=True`), piped, and
  timeout-bounded; STT call reuses the shared `TranscriptionClient` instance.
- [ ] 4.3 Connector/integration tests: post-download oversize backstop; concurrent voice notes
  serialize correctly on the shared client's lock; a connector-level replay re-transcribes but
  Switchboard's existing idempotency key discards the duplicate submission.
- [ ] 4.4 Disposal tests: no audio byte buffer, hash, or excerpt appears in any log record, exception
  message, metric label, or the persisted envelope, for every D5 row.
- [ ] 4.5 Non-regression tests: every existing Tiered Text Extraction scenario (Tiers 1, 2, 4, and the
  full Tier 3 list including `[Voice message]`) passes unchanged for every non-superseding case.
- [ ] 4.6 Run targeted unit/connector tests, repo guards (`make check-guards`), strict OpenSpec and
  overwrite checks, lint/format, a fresh independent exact-head review, and terminal hosted CI. Report
  the implementation PR's actual test delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the
  `telegram-voice-transcription` capability plus the `connector-telegram-bot`/`connector-base-spec`
  deltas to their `openspec/specs/` baselines and archive this change. Archival does not authorize
  deployment or any live Telegram/STT API call.
