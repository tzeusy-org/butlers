# Design: Telegram voice-note transcription contract

## Context

`bu-r0dsz`'s design already names the shape: "the STT capability already exists in the system; this
task wires it into the Telegram connector's voice message handler." What exists today:

- `src/butlers/connectors/telegram_bot.py::_MEDIA_TYPE_LABELS` maps Telegram's `voice` message field
  to the literal descriptor `"Voice message"`, used unconditionally by
  `_extract_normalized_text`'s Tier 3 fallback whenever a message has neither `text` nor `caption`.
  This is the exact, only place Telegram voice notes are handled today — the raw audio is never
  fetched.
- `src/butlers/connectors/live_listener/transcription.py::TranscriptionClient` is a protocol-agnostic
  abstract interface (`async transcribe(audio: bytes) -> TranscriptionResult | None`) with three
  concrete backends (Wyoming/WebSocket/HTTP), built for the live-listener connector's continuous
  microphone pipeline. Its `transcribe()` contract already expects **raw 16 kHz mono 16-bit PCM**
  input (see its abstract method docstring) — never a compressed container format.
- Telegram's own `Voice` object (verified live against the official Bot API reference and, where the
  primary page's length defeated direct extraction, its verbatim aiogram mirror, fetched 2026-09-09):
  `file_id`, `file_unique_id`, `duration` (seconds, sender-reported), `mime_type` (optional,
  sender-defined), `file_size` (optional, bytes). A bot fetches the bytes via `getFile` (returns a
  `file_path`), then downloads from `https://api.telegram.org/file/bot<token>/<file_path>`.
  aiogram's `get_file` reference states verbatim: "bots can download files of up to 20MB in size" and
  "It is guaranteed that the link will be valid for at least 1 hour." Telegram's own in-app voice
  recorder always produces an OGG container with the OPUS codec (the format `sendVoice`'s own
  reference names as the primary supported voice format); this connector already accepts and stores
  the message's self-reported `mime_type` as opaque JSON, so no receive-side format assumption is
  hard-coded anywhere today — the transcoder (D3) is written to handle whatever container/codec
  arrives, not to assume OGG/Opus specifically.
- No `ffmpeg` or audio-codec Python dependency exists anywhere in this repository today (verified:
  no reference in `pyproject.toml`, `Dockerfile`, or `Dockerfile.base`; `Dockerfile.base`'s system
  package list has no audio/codec tooling). Decoding a compressed voice-note container into the PCM
  format `TranscriptionClient` requires is new transcoding responsibility this contract must assign
  (D3), not an existing capability this draft can silently assume.
- `butlers/identity.py::resolve_owner_channel_via_definer` (D6) is the exact fast,
  classification-pipeline-independent owner lookup the sibling `telegram-receipt-acknowledgement`
  contract's D7 already specified and grounded for the structurally identical problem: gating a
  pre-classification connector-side action to the owner without waiting on or depending on the
  routing pipeline's own identity resolution.

## Goals and non-goals

Goals:

- Define which Telegram voice notes are eligible for transcription, and which fall back to today's
  unconditional `[Voice message]` placeholder (D2).
- Define who decodes the compressed voice-note container into the PCM format the existing
  `TranscriptionClient` interface requires, and how (D3).
- Define how the existing `TranscriptionClient` interface is reused for a single, bounded,
  request/response call rather than the continuous streaming pipeline it was built for (D4).
- Define the exact failure taxonomy — oversize, overlong, download failure, transcode failure, STT
  unavailable, empty/low-confidence result — and its fallback behavior, honoring `bu-r0dsz`
  criterion 5 ("not a silent drop") without inventing new outbound-send authority (D5, D9).
- Define provenance: how a downstream consumer learns a message originated from a transcribed voice
  note (`bu-r0dsz` criterion 4), without modifying the existing `ingest.v1` envelope's core scenarios
  (D7).
- Define disposal: audio bytes exist only in memory, for the duration of one transcode+transcribe
  call, on every exit path including failure (`bu-r0dsz` criterion 6) (D8).
- Define the default-disabled configuration posture and its rationale (D2).
- Assign ownership so a future implementation does not re-litigate which component (connector vs.
  module vs. core wiring) performs which step (D1).

Non-goals:

- No implementation, migration, runtime process, or live Telegram/STT API call. Nothing here
  executes.
- No new STT service, model, or provider account. The only backend this contract authorizes reuse of
  is the existing Wyoming faster-whisper service the live-listener connector already depends on
  (`wyoming-faster-whisper.parrot-hen.ts.net:10300`).
- No ambient/always-listening microphone behavior of any kind — this is strictly a per-message,
  explicit-user-action (the owner sending a voice note) contract.
- No speaker identification beyond the existing Telegram sender-identity gate (D6) — the contract
  never attempts to distinguish speakers within an audio segment.
- No persistence of the original audio bytes in any form: not in `payload.raw`, not as an
  `IngestAttachment.storage_ref`, not on local disk, not in a log or metric (D8).
- No photo/video/document OCR or any non-`voice` media type. Telegram's generic `audio` message type
  (music files, not voice notes) is explicitly out of scope — `_MEDIA_TYPE_LABELS` already treats
  `voice` and `audio` as distinct keys, and this contract touches only `voice`.
- No change to `connector-telegram-user-client`, `module-telegram`'s outbound tools, the approval or
  confirm inline-keyboard machinery, or any non-`telegram_bot` channel.
- No change to what triggers ingestion, checkpoint semantics, or how messages are classified/routed
  once `payload.normalized_text` is populated — this contract only changes how that one field is
  populated for one media type.

## Decisions

### D1: Ownership — the connector performs acquisition and transcoding itself; no module boundary crossing

Unlike the sibling `telegram-receipt-acknowledgement` contract (where `module-telegram` had to own the
typing/reaction API primitives because those are *outbound* pushes to Telegram happening on a
different lifecycle event, pre/post `pipeline.process()`), voice-note acquisition is *inbound*
normalization — the same category of work `connector-telegram-bot` already performs directly for
every other tier of `_extract_normalized_text`, and the same category of work `gmail.py`'s
`_process_attachments` already performs directly (fetching attachment bytes from the Gmail API as
part of building one email's `ingest.v1` envelope, entirely within the connector). `getFile` plus the
file download are both read-only Bot API calls made as part of constructing this one message's
envelope, not a side-channel outbound push; there is no architectural reason to route them through
`module-telegram` or Switchboard's core wiring, and doing so would need a new cross-process call path
this contract does not otherwise require. The connector remains the natural owner of: `getFile` +
download, transcoding (D3), and the single-shot STT call (D4).

This does not change the connector's transport-only *outbound* posture — the connector still never
calls `sendMessage`, `sendChatAction`, or `setMessageReaction` (see D9 for what this means for the
failure-signal criterion).

### D2: Eligibility gate — owner-only, size/duration-bounded, disabled by default

`bu-r0dsz`'s own outcome language (echoed verbatim in this draft's governing bead, `bu-w6acf`) names
"owner voice-note acquisition," not arbitrary-sender acquisition. Transcribing a voice note is
materially different from the existing unconditional Tier 3 descriptor or the ack contract's
reactions: it sends a third party's spoken audio to an external network service. Absent an explicit
requirement to transcribe non-owner speech, the conservative and textually-supported reading is
owner-only. `[decision] chose an owner-only eligibility gate over an unconditional (any-sender) gate:
transcribing arbitrary group-chat members' voices without consent is a real privacy exposure the bead
text does not ask for ("owner voice-note acquisition"), while today's unconditional placeholder
behavior for every non-owner sender is preserved exactly. Reversible: yes — widening to other senders
is a later, separately-authorized decision, not a breaking change to this contract.`

The gate reuses the exact mechanism the sibling `telegram-receipt-acknowledgement` contract's D7
already specified for the structurally identical problem: `identity.resolve_owner_channel_via_definer`
(or an equivalent single indexed lookup) keyed on `sender.identity`, evaluated before any network call
to Telegram for the file itself — a non-owner sender's voice note never triggers a `getFile` call at
all, let alone a download or STT call.

Media bounds, checked from the `Voice` object's own self-reported metadata *before* any network call:

- `file_size` (when reported) MUST NOT exceed the Bot API's own 20 MB `getFile` download ceiling
  (aiogram's mirror of the official limit, D3's grounding). A note with no reported `file_size` is not
  rejected on this basis alone — Telegram omits it inconsistently — but is still subject to the
  duration bound below and the post-download actual-byte-count check in D5.
- `duration` (seconds, sender-reported) MUST NOT exceed a configurable ceiling, default 120 seconds —
  the exact bound `bu-r0dsz` criterion 7 names ("works for voice notes of typical length (up to 2
  minutes)"). This is also a latency/resource-exhaustion bound: transcription happens synchronously
  within the connector's per-update handling (bounded by the existing `CONNECTOR_MAX_INFLIGHT`
  semaphore), so an unbounded duration is an unbounded per-update latency and STT-cost risk.

`[decision] chose a disabled-by-default configuration posture (new env var
CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED, default false) over enabled-by-default, following the
sibling ack contract's D4 non-regression precedent but for a stronger reason here: enabling this by
default would silently start sending every deployed butler owner's voice-note audio to an external
network service the moment implementation lands, with no configuration action on the owner's part —
a materially different (and more privacy-sensitive) data flow than a local reaction-emoji dispatch.
Reversible: yes — this is a config default, not a structural constraint.`

### D3: Transcoding — a bounded `ffmpeg` subprocess, not a Python codec library

`TranscriptionClient.transcribe()` requires raw 16 kHz mono 16-bit signed PCM (verified against its
abstract method docstring and `HttpTranscriptionClient`'s own `_pcm_to_wav` helper, which assumes the
same). Telegram voice notes arrive as a compressed container (OGG/Opus for every in-app recorded
voice note; the `Voice.mime_type` field is optional and sender-reported, so the transcoder must not
hard-code an assumption about it). Decoding this is new work — no dependency in this repository
today performs it (verified: no `ffmpeg`, `pyogg`, `opuslib`, or equivalent in `pyproject.toml` or
either Dockerfile).

`[decision] chose ffmpeg (invoked as a subprocess, not a Python audio-codec library) as the
transcoder: ffmpeg is the industry-standard tool for exactly this class of container/codec transcode,
handles OGG/Opus (and any other container Telegram might report) without per-codec special-casing,
and avoids adding a fragile pure-Python decoder dependency for a single, well-bounded conversion.
Reversible: yes — this is an implementation-detail engineering allocation, not a product or
architecture commitment; a future implementation is free to substitute an equivalent decoder without
revising this contract's acquisition/bounds/failure/disposal requirements.`

The transcode step:

- Feeds the downloaded compressed bytes to `ffmpeg` via a stdin pipe and reads the PCM result from a
  stdout pipe — never a temporary file on disk (D8).
- Invokes `ffmpeg` as an argument-list subprocess (never `shell=True`, never string-interpolated
  arguments) to eliminate any command-injection surface from untrusted, sender-supplied bytes.
- Runs under a bounded timeout no larger than the duration ceiling in D2, so a malformed or
  adversarial container cannot hang a semaphore slot indefinitely.
- Requests exactly the target format `TranscriptionClient` needs: 16 kHz, mono, 16-bit signed
  little-endian PCM — matching `WyomingTranscriptionClient`'s `AudioStart(rate=16000, width=2,
  channels=1)` contract precisely, so no additional resampling occurs downstream.
- A future implementation adds `ffmpeg` to `Dockerfile.base`'s system package list; this draft names
  that requirement but does not perform it (no runtime/config change is authorized here).

### D4: STT reuse — single-shot call against a persistent, shared `TranscriptionClient` instance

The live-listener connector's `TranscriptionClient` was built for one long-lived per-microphone
pipeline calling `transcribe()` repeatedly on a persistent connection. Telegram voice notes are
sporadic, not continuous, but the interface's `connect()`/`disconnect()`/`transcribe()` contract still
fits: the Telegram bot connector creates one `TranscriptionClient` instance at connector startup
(mirroring the existing `CachedMCPClient` singleton pattern already used for the MCP connection),
`connect()`s it once, and calls `transcribe(pcm_bytes)` per eligible voice note for the life of the
process — reusing the class's own internal reconnect-with-backoff behavior rather than tearing the
connection down and rebuilding it per message. Protocol/URL are configured via new,
Telegram-bot-connector-scoped environment variables (`CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_URL`,
`CONNECTOR_TELEGRAM_VOICE_TRANSCRIPTION_PROTOCOL`, defaulting to the same Wyoming backend and URL the
live-listener connector already depends on) — a distinct config surface from
`LIVE_LISTENER_TRANSCRIPTION_URL` because these are two independently deployed connector processes,
even when they point at the same backend by default.

`WyomingTranscriptionClient._transcribe_locked` already serializes concurrent callers on an internal
`asyncio.Lock()` and already performs exactly one reconnect-and-retry attempt on a connection-level
failure before returning `None`. This contract inherits that behavior rather than adding a second
retry layer on top of it: concurrent owner voice notes (bounded by `CONNECTOR_MAX_INFLIGHT`) queue on
that lock and are transcribed one at a time; a transient connection failure gets the class's own single
retry, not a Telegram-connector-specific one.

`[decision] chose a shared, persistent TranscriptionClient instance over a fresh instance per voice
note: matches the class's own design intent (long-lived connection, internal reconnect/backoff state)
and avoids paying a fresh-connect round-trip on every voice note. Reversible: yes.`

### D5: Failure taxonomy — every disqualifying or failing case falls back to the existing placeholder unchanged

`bu-r0dsz` criterion 5 requires "a user-visible error reply (not silent drop)" on transcription
failure. This contract does not grant the connector new outbound-send authority to satisfy that (see
D9 for why not, and what a future implementation does instead); instead, every case below falls back
to exactly the unconditional `[Voice message]` descriptor the Tiered Text Extraction requirement
already specifies — the message is never silently dropped, because it is still ingested, just without
a transcript:

| Case | Detection point | Fallback |
|---|---|---|
| Non-owner sender | Before any network call (D2) | `[Voice message]`, unchanged from today |
| Feature disabled | Before any network call (D2) | `[Voice message]`, unchanged from today |
| `file_size` exceeds 20 MB | Before any network call, from `Voice.file_size` | `[Voice message]` |
| `duration` exceeds configured ceiling | Before any network call, from `Voice.duration` | `[Voice message]` |
| `getFile`/download failure (after one bounded retry) | Network call | `[Voice message]` |
| Actual downloaded byte count exceeds 20 MB despite absent/understated `file_size` | Post-download | `[Voice message]`, download discarded immediately |
| `ffmpeg` transcode failure or timeout | Subprocess call | `[Voice message]` |
| STT unavailable/timeout (after the client's own internal retry, D4) | STT call | `[Voice message]` |
| STT returns empty text, or confidence below a configured minimum | STT call | `[Voice message]` |
| Eligible and successful | — | Transcript text (D6) |

This deliberately differs from the live-listener connector's own "Empty transcription handling"
scenario, which *silently discards the entire utterance* on an empty/low-confidence result — correct
for an ambient, continuous pipeline where a low-value segment has no user-visible cost to dropping.
A Telegram voice note is a deliberate, singular owner action; dropping the whole message would
violate `bu-r0dsz` criterion 5. This contract therefore always ingests something for a voice note —
either the transcript or the pre-existing placeholder — never nothing.

Every case in the table also increments a distinct counter/reason (mirroring the live-listener
connector's own `connector_*_transcription_failures_total`/`_discarded_total` pattern) so a future
implementation's tests and dashboards can distinguish "no owner spoke," "feature off," "oversize,"
"overlong," "network failure," "transcode failure," "STT down," and "low confidence" from one another,
rather than collapsing them into one opaque fallback signal.

### D6: Successful transcript replaces the descriptor as real message content

For the "eligible and successful" row above, `payload.normalized_text` becomes the transcript text
itself — not a bracket-wrapped descriptor like `[Voice message: "...")]`. This is a deliberate contrast
with every other synthesized Tier 3 descriptor (`[Photo]`, `[Sticker: 😀]`): those describe media the
system cannot read; a successful transcript **is** the owner's message content, and bracket-wrapping it
would make classification and routing treat spoken and typed owner input differently for no reason —
directly against `bu-r0dsz`'s stated goal ("so the owner can speak to butlers naturally"). Voice
origin is instead carried out-of-band, in metadata (D7), not by decorating the text a butler acts on.

### D7: Provenance — Telegram's own payload already carries origin; only STT outcome metadata is new

`payload.raw` already contains the complete Telegram update JSON verbatim for every message,
including, for a voice note, `message.voice` in full (`file_id`, `file_unique_id`, `duration`,
`mime_type`, `file_size`) — untouched by this contract. That alone satisfies `bu-r0dsz` criterion 4
("message metadata indicates voice origin") with zero schema change: any downstream consumer can
already check for the presence of `payload.raw.message.voice` (or the `edited_message`/`channel_post`
equivalent) exactly as `_extract_normalized_text` does today.

What does not exist anywhere in the envelope today is the *outcome* of this contract's own
transcription attempt (status, STT confidence, detected language, which backend produced it) — that
information originates with this contract, not with Telegram. `[decision] chose one new, independent
ADDED requirement on connector-base-spec (an optional payload.transcription object) over inventing a
Telegram-specific side-channel, and over silently stuffing synthetic keys into payload.raw: raw is
documented as "the full provider payload dict" and any consumer relying on that as literally
provider-verbatim (e.g., for audit/replay) should not have to special-case Telegram voice messages.
A new, independent, optional field is additive by construction — no existing ingest.v1 scenario is
touched — and follows the same placement precedent the sibling notify-confirm-interaction contract set
when its new caller-facing intent was added to core-notify's own spec file rather than buried in a
narrower capability. Reversible: yes; the field is optional and absent for every other message.`

`payload.transcription` (present only when this capability's Tier 3-superseding scenario evaluates a
message at all — i.e., only for a `voice` message from any sender, whether the outcome was success or
one of the fallback rows in D5's table that reached at least the eligibility check):

- `source_media`: `"voice"` (fixed today; the field name is generic so a future connector performing
  its own transcription is not forced to invent an incompatible parallel field).
- `status`: one of `"transcribed"`, `"not_owner"`, `"disabled"`, `"oversize"`, `"overlong"`,
  `"download_failed"`, `"transcode_failed"`, `"stt_unavailable"`, `"empty_or_low_confidence"` —
  matching D5's table exactly, so a downstream consumer never has to infer the reason from
  `normalized_text` alone.
- `confidence`: float in `[0, 1]`, or `null` when `status != "transcribed"`.
- `language`: BCP-47 code as reported by the STT backend, or `null` when `status != "transcribed"`.
- `stt_backend`: the configured protocol identifier (e.g. `"wyoming"`), or `null` when no STT call was
  attempted (`not_owner`, `disabled`, `oversize`, `overlong`).

### D8: Disposal — audio bytes exist only in memory, on every exit path, never logged

The downloaded compressed bytes and the transcoded PCM bytes both exist only as in-process byte
buffers — never written to a temporary file, never included in `payload.raw` (which never contains
byte-level audio; `message.voice` is small JSON metadata, not the audio itself), never attached via an
`IngestAttachment.storage_ref` (this contract deliberately mints no attachment at all for voice audio;
see "Rejected alternatives"), and never logged or included in any exception message, metric label, or
trace context. The acquisition/transcode/transcribe sequence runs inside a `try`/`finally` so every
exit path — success, any D5 failure row, or an unexpected exception — reaches the same disposal point;
Python's own reference-counted garbage collection then reclaims the buffers with no explicit
"delete" step required, because nothing was ever written to persistent storage in the first place.

### D9: Failure-signal delivery is a future implementation's job, using the existing outbound boundary; this contract does not grant new send authority

`bu-r0dsz` criterion 5 wants a user-visible reply on failure, but `connector-telegram-bot` remains
transport-only (D1) — it does not call `sendMessage`, and this contract does not change that boundary,
matching the sibling ack contract's own D1 (which kept `connector-telegram-bot` transport-only even
for the narrower `sendChatAction`/reaction case). Instead: every failure row in D5's table still
ingests the pre-existing `[Voice message]` placeholder text and the new `payload.transcription.status`
metadata (D7) through the normal pipeline, exactly as any other message does. A future implementation
of `bu-r0dsz`'s failure-reply criterion is expected to have the *responding butler* (which already has
legitimate reply authority via the Messenger butler's outbound tools) notice
`payload.transcription.status != "transcribed"` and phrase an appropriate reply — no new connector or
module send-path authority is required, and the new metadata field (D7) is exactly what makes that
possible without the connector itself sending anything. This draft does not implement that reply
behavior; it only ensures the future implementation has the metadata needed to write it without
re-opening this contract.

## Rejected alternatives

- **Mint an `IngestAttachment` referencing the voice note's Telegram `file_id` for later on-demand
  refetch** — rejected (D8): even though the bytes would not be duplicated into Butlers' own storage,
  handing downstream code a durable handle to re-fetch and re-listen to the original audio is exactly
  the kind of standing audio-retention surface `bu-r0dsz` criterion 6 and this draft's non-goals rule
  out; the transcript is the durable artifact, not a pointer back to the audio.
- **Gate eligibility on the full classification-time identity resolution instead of the fast
  definer-backed lookup** — rejected (D2), for the same reason the sibling ack contract's D7 rejected
  it: it would require a network-bound classification pass before the connector can even decide
  whether to call `getFile`, defeating the latency/cost rationale for gating at all.
- **Default the feature to enabled** — rejected (D2): silently starts a new, more privacy-sensitive
  data flow (audio leaving the process to an external STT service) for every existing deployment with
  no owner action, unlike a purely local reaction-emoji default.
- **Have `module-telegram` own acquisition/transcoding, mirroring the ack contract's D1** — rejected
  (D1): that split existed because typing/reactions are outbound pushes on a different lifecycle
  event; voice acquisition is inbound normalization the connector already owns for every other tier,
  and routing it through the module would add a cross-process call path with no offsetting benefit.
- **Decode audio with a pure-Python Opus/Ogg library instead of `ffmpeg`** — rejected (D3): narrower
  codec coverage, an extra fragile dependency, for a well-bounded, one-shot conversion `ffmpeg`
  already handles as a mature, widely-deployed tool.
- **Have the connector itself call `sendMessage` on failure to satisfy criterion 5 directly** —
  rejected (D9): grants the transport-only connector new outbound authority it does not otherwise have
  for a case (voice-note failure) that is not more urgent than any other reply the responding butler
  already handles through its existing, legitimate reply path.

## Test Strategy (future implementation only)

Named seams a future implementation PR's tests must cover (none exist yet; this draft adds no tests,
`+0 ~0 -0`):

- Unit: media-bounds pre-checks (`file_size`, `duration`) reject before any network call; the
  disabled-by-default config gate; owner-only gate using the same fast lookup precedent as the ack
  contract's D7 tests.
- Unit: `payload.transcription` status/confidence/language/backend population for every row of D5's
  table, including the `null` fields for non-`"transcribed"` statuses.
- Contract/API: the `ffmpeg` subprocess is invoked as an argument list (never `shell=True`), reads via
  stdin/writes via stdout, and is bounded by a timeout derived from the duration ceiling.
- Connector/integration: a downloaded byte count exceeding 20 MB despite an absent or understated
  `Voice.file_size` still triggers the oversize fallback post-download, not just the pre-check.
- Disposal: a fixture asserting no audio byte buffer, hash, or excerpt appears in any log record,
  exception message, metric label, or the persisted `ingest.v1` envelope for any D5 row.
- Concurrency: two owner voice notes arriving within `CONNECTOR_MAX_INFLIGHT` of each other serialize
  correctly on the shared `TranscriptionClient`'s internal lock without corrupting either result.
- Idempotence/dedup: a connector-level replay of an already-ingested voice update (crash-safe resume)
  re-transcribes but Switchboard's existing `tg:<chat_id>:<message_id>` idempotency key still discards
  the resulting duplicate submission — named explicitly as an accepted redundant-cost tradeoff, not a
  correctness gap this contract needs to close.
- Non-regression: every existing Tiered Text Extraction scenario (Tiers 1, 2, 4, and the full Tier 3
  media-type list including `[Voice message]`) still passes unchanged for every non-superseding case.

## Delivery gates

1. Land this draft only after independent exact-head semantic review returns GO or corrections are
   applied and re-reviewed.
2. Obtain separate owner approval naming the exact reviewed commit before any implementation claims
   this contract as authority.
3. Implementation happens under `bu-r0dsz` (unmodified by this draft) or an explicitly
   coordinator-approved successor, plus the tests in `tasks.md`/Test Strategy above.
4. Treat any live Telegram API call, STT call, `ffmpeg` invocation, `Dockerfile.base` change, or
   deployment as a separate, later-authorized act this draft does not perform or authorize.

## Open questions

None are silently decided here. Whether a future implementation stores the persistent
`TranscriptionClient` instance as a connector attribute or a small dedicated helper class (mirroring
the open question the sibling ack contract left for its own per-chat state) is left to that
implementation PR — both satisfy this contract's single-shared-instance requirement (D4) equally, and
choosing between them is ordinary engineering allocation, not a decision this draft needs to pin.
