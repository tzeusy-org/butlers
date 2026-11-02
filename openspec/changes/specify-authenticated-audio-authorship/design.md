## Context

This is a large trust-boundary amendment with a settled modality choice and pending exact-text
adoption. The baseline and non-goals are in `proposal.md`. RFC 0038 D5 already derives origin
from server-held evidence, and `REQ-connector-base-spec-004` already marks provider-known
third-party spans. D18's unconditional third-party classification of model-function output is
the narrow incompatibility. Existing transcription outcome metadata carries no authorization.

## Goals / Non-Goals

**Goals:** specify source-authorship preservation, the producer/admission evidence boundary,
replay behavior and future regression seams. Treat a verified owner's speech like their typed
words only after an instruction-free transform, retaining all exposure and approval rules.

**Non-Goals:** an implementation, STT deployment/selection, a new goal origin, speaker identity
recognition, generic model trust, an extra approval process, or any separate RFC 0009/context
rendering change. No upload endpoint, UI layout or new operational permission is designed here.

## Decisions

### Keep source authorship separate from transform output

An ASR-class transform is the already named speech-to-text model function, not a new model-use
kind. It receives audio plus fixed server decoding configuration and cannot take task or
conversation instructions, use tools, summarize, translate or answer. A general-purpose model
with an empty tool list still takes instructions and is therefore not this exception. Language
or confidence labels alone do not establish the backend class. Unsupported or unproven backends
keep ordinary third-party model-output treatment; there is no instructable fallback.

### Hold provenance at ingress, not in payload text

The authenticated producer holds the following logical typed evidence; these are semantics of
server-held context, not new client-settable `ingest.v1` fields:

| Evidence | Source and meaning |
| --- | --- |
| Source binding | Existing endpoint and original dedupe/event identity, actual source kind (Telegram voice, Telegram audio, dashboard owner recording, or excluded source), and media content identity/digest |
| Sender authorization | Existing channel/sender authorization or dashboard authenticated principal and request protections; never a voiceprint, caption or claimed speaker |
| External-content markers | Actual provider forwarding/quote/embedding evidence; preserve whole affected transcript when no safe range mapping exists |
| Transform binding | Server-selected verified ASR implementation/revision, its completed successful outcome, and exact transcript digest tied to that media and submission |
| Durable decision | Accepted submission/goal association, resulting origin and monotonic exposure, used unchanged by duplicates and replay |

The admission boundary obtains this evidence through its trusted producer context, verifies
its bindings and retains the association with the existing accepted submission. Copying a
serialized evidence-shaped object into a request body is not an authenticated context. The
implementation must choose storage/transport that preserves this boundary; this contract does
not pretend the existing payload or an in-memory DTO already implements it.

`payload.transcription` retains its current optional outcome-only shape. An old envelope with
`status=success` and `stt_backend` does not become an attestation. Sender authentication of the
enclosing owner message is distinct from authorship of its forwarded material: the envelope
may remain owner-origin, but the forwarded transcript stays third-party and starts the goal
exposed. The same rule retains quoted text/photo/caption and audio markers. No model is used
to decide authorship or infer missing quotation ranges. Unmarked owner-pasted content retains
D5's existing known limit; no new semantic quotation detector is implied.

### Replay never becomes a second admission path

Bind successful transcript and provenance before authority-bearing admission. The original
Telegram message/dash submission identity remains the dedupe identity, not an STT attempt ID,
backend version or changed transcript hash. Once a submission is accepted, retries/replays
return its existing admission result; later transcription cannot promote its origin or lower
exposure. A failure before acceptance may be retried under existing finite recovery rules,
but requires fresh complete evidence and the same source identity, not a caller's new label.

### Preserve finite failure and legacy transport contracts

No transform result means no invented spoken request. Existing captions/typed text retain
their independent authentication and existing attachment/failure handling remains intact.
Finite byte, decoded-duration and execution controls, and owned cleanup, remain engineering
configuration; the owner's audio-file choice is not a demand for unbounded processing. No
numerical bound is raised here. No new error wording is dictated: existing user-visible
failure behavior must remain truthful and must not present a status/placeholder as speech.

The existing dark voice-note contract's fallback descriptors and bounds are left byte-for-byte
unchanged. This contract adds **no authority** to those descriptors and does not widen that
transport's implemented media eligibility. `bu-dhloi1.20` implements the newly adopted authority
and audio/dashboard integration after the separate `bu-r0dsz` transport. TTS is not amended.

## Risks / Trade-offs

- [An outcome field is mistaken for proof] -> keep server-held evidence and outcome schema
  separate; test forged/mismatched/replayed producer-shaped input at the admission seam.
- [ASR returns plausible but wrong words] -> no inference of words after failure; retain exact
  transcribed provenance, configured acceptability controls and original media binding. This
  contract does not promise semantic accuracy or confer approval on the transcript.
- [Forwarding markers are lost during media conversion] -> retain provider evidence before
  transform; conservative whole-transcript marking when range correspondence is unavailable.
- [An independently approved dark transport is silently expanded] -> preserve its entire
  changeset and implementation; enumerate the new cutover scenarios separately below.

## Verification and handoff

| Future executing seam | Required added/extended species after adoption |
| --- | --- |
| `tests/connectors/test_telegram_bot_untrusted_spans.py` | Real provider forward/quote/text/photo/audio markers survive normalization; no fabricated sender or missing-span upgrade |
| `tests/connectors/test_telegram_bot_connector.py` | Real voice/audio normalization with qualified transform success and finite failure; unchanged cross-connector dedupe, attachment/caption and reply identities |
| `tests/switchboard/test_goal_admission.py` | Trusted producer evidence vs forged payload and unsupported backend; exact source/transcript mismatch, same-identity replay, owner/exposed ceilings |
| Existing dashboard owner-auth and chat/API ingress owners | Actual authenticated recording request vs unauthenticated/client labels/room upload, cancellation/outage, same submission retry |

These are future implementation obligations, not tests run or added in this spec-only packet.
Existing runtime tests and fixtures are untouched. The spec author runs strict OpenSpec,
requirement trace, referenced-path checks and applicable repository statics; a different reviewer
checks normative consistency and preservation before exact owner adoption. Tests: +0 ~0 -0.

## Migration Plan

No runtime migration or activation is performed. Merge/adoption of the exact contract does not
make existing outcome metadata authoritative. Authority integration remains unavailable until
its actual producer/admission binding is implemented and verified. A rollback to the previous
contract must not reinterpret stored third-party records as owner-authored; preserve already
accepted identities/exposure and stop admitting unproved owner-audio input.

## Funnel and adoption status

- Baseline: `b725c9ff0276bd49889eb3bbbef821dc078e28a5`.
- Motif: verified owner speech should carry the same source authorship as typed input.
- Doctrine/topology: deterministic connector/dashboard ingress -> existing Switchboard goal
  admission, with no additional reasoning or authority in a domain daemon.
- Contract/spec: RFC D5/D18 and v1 exact proposed text plus four delta-spec capabilities.
- Engineering bar: source authentication, immutable binding, monotonic exposure, dedupe,
  finite cleanup, privacy and truthful non-success; no transport implementation in this task.
- Modality decision: owner `bu-2utmy5`, 2026-10-20 14:37Z. Exact text adoption remains pending;
  neither this author nor an independent conformance review substitutes for it.

## Relocated specification presentation notes

These unchanged notes are retained here outside the delta-operation specification files.

### Audio authorship purpose

Preserve authenticated source authorship through a narrowly qualified speech-to-text transform,
without letting transcript content or transform output grant authority to itself.

### audio-authorship source references

- RFC 0038 D5/D6/D18; RFC 0017 owner-channel authorization
- Owner decision `bu-2utmy5`, 2026-10-20 14:37Z; exact amendment adoption remains pending
- `about/heart-and-soul/vision.md`: deterministic infrastructure and runtime-enforced authority

### connector-base-spec source references

- RFC 0038 D5/D6/D18; REQ-connector-base-spec-004
- `specify-telegram-voice-note-transcription-contract`, Transcription Outcome Payload Field

### connector-telegram-bot source references

- RFC 0017; RFC 0038 D5/D6/D18
- Existing `specify-telegram-voice-note-transcription-contract` transport scope; `bu-r0dsz`

### dashboard-api source references

- RFC 0038 D5/D6/D18; `docs/identity_and_secrets/dashboard-owner-auth.md`
- Owner decision `bu-2utmy5`, dashboard recording scope only
