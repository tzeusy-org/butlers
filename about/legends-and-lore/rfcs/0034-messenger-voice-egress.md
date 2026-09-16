# RFC 0034: Messenger-Owned Voice Egress

**Status:** Draft
**Date:** 2026-09-16
**Approval:** Exact-artifact independent security/spec review and owner approval pending

## Summary

This RFC defines a fail-closed, local-first in-room voice-egress path owned by
Messenger. It implements the six policy choices recorded on `bu-7exe4.13`:
Messenger owns outbound semantics; provider profiles are evidence-gated and
local-first; every attempt requires fresh room-specific owner-positive
presence; only replies and explicitly targeted sends may initiate voice;
DND/quiet hours always suppress voice; and an unsafe or unavailable attempt may
produce at most one separately keyed, resolver-selected text-only non-voice fallback.

This RFC is a candidate contract, not authority to implement or exercise it.
It permits no endpoint/presence/provider/credential/audio access, device
mutation, deployment, or canary. RFC 0003, RFC 0005, RFC 0008, RFC 0019, and
RFC 0028 remain binding.

## Ownership boundaries

| Component | Owns | Explicitly does not own |
|---|---|---|
| Switchboard | Authenticated service/origin lineage, durable route witness, fallback target resolution and one-fallback key | Endpoint registry, presence facts, physical outcome |
| Messenger | Opaque endpoint/binding versions, admissible provider selection, policy/presence orchestration, handoff truth, voice receipt/replay fence, fallback intent | Ingress capture, Home facts, direct Home schema/calls, contact/device inference |
| Home | Room-specific presence facts and categorical attestation; HA actuation under RFC 0028 if that provider is admitted | Voice target selection, message content, delivery retry |
| Live Listener | Microphone capture, VAD/ASR, normalized `ingest.v1` submission | TTS, presence authority, endpoint selection, speaker/provider calls |
| Provider adapter | One exact start/confirm/no-start/unknown interface for a server-held profile | Policy, target inference, retries, fallback |

All Messenger-to-Home communication is deterministic MCP brokered through
Switchboard. There is no direct daemon call or cross-schema read. Public
identity/contact stores are not physical endpoint registries.

## Precedence

Every attempt follows this order and stops at the first failure:

1. authenticated ownership and origin lineage;
2. provider-profile admissibility;
3. reply or one explicit endpoint initiation;
4. active binding, then unconditional DND/quiet-hours gate;
5. fresh room-specific owner-positive presence;
6. atomic physical-side-effect claim and provider handoff;
7. at most one linked text-only non-voice fallback.

Fallback never repairs unauthenticated lineage. Approval or priority never
changes this order.

## Wire contracts

### `voice_origin.v1`

Switchboard attaches this assertion on its authenticated route to Messenger:

```json
{
  "schema_version": "voice_origin.v1",
  "contract_version": "messenger-voice-egress.v1",
  "origin_request_id": "<canonical request uuid>",
  "origin_butler": "<authenticated service identity>",
  "intent": "reply|send",
  "message_digest": "<server-keyed digest>",
  "reply_lineage_ref": "<opaque durable-route ref or null>",
  "endpoint_ref": "<opaque Messenger endpoint ref or null>",
  "issued_at": "<server timestamp>"
}
```

The assertion is transport-authenticated and matched to Switchboard's durable
route record. The JSON fields alone carry no authority. A reply has
`reply_lineage_ref` and no caller-selected endpoint; a send has one explicit
`endpoint_ref`. Raw mic, room, contact, provider, or device ids are invalid.

### `voice_presence_attest.v1`

Messenger asks Home through Switchboard using an opaque room token:

```json
{
  "schema_version": "voice_presence_attest.v1",
  "room_ref": "<opaque control-plane token>",
  "binding_version": 7,
  "attempt_nonce": "<single-use random nonce>"
}
```

Home returns a transport-authenticated categorical result:

```json
{
  "schema_version": "voice_presence_attest.v1",
  "binding_version": 7,
  "attempt_nonce": "<same nonce>",
  "result": "owner_present|owner_absent|unknown|conflicting|unavailable|unconfigured",
  "freshness": "fresh|stale|unknown",
  "newest_observation_age_seconds": 12,
  "issued_at": "<server timestamp>"
}
```

Only `owner_present` with agreeing evidence at most 60 seconds old and an
attestation consumed within 5 seconds authorizes the attempt. The nonce and
binding version must match and are single-use. Messenger persists only the
result/freshness category. The attestation body and Home's raw evidence are
discarded.

### Provider adapter result

An admitted adapter exposes one call with one of five categorical results:

| Result | Meaning | Voice retry class |
|---|---|---|
| `rejected_before_start` | Provider proves the physical start boundary was not crossed | `safe_retry`; explicit replay only |
| `started` | Irreversible playback boundary crossed; intermediate only | no retry |
| `confirmed` | Exact artifact's completion proof satisfied | return receipt |
| `failed_after_start` | Start occurred, completion failed or was partial | no retry |
| `unknown_after_handoff` | Start cannot be proved or disproved | ambiguous, no retry |

Transport acceptance is not confirmation. Any timeout, reset, malformed body,
crash after dispatch, or lost settlement without no-start proof maps to unknown.

## Endpoint registry

Messenger stores random opaque endpoint references and immutable binding
versions. A binding version holds only opaque room/ingress references, a
server-held provider-profile reference, lifecycle state, and content-blind
version/audit metadata. Provider-native targets and HA entities stay in their
own adapter/configuration stores. Neither `public.entity_info` nor relationship
facts may represent speakers, rooms, or provider targets.

Mutations are owner-authenticated, server-attributed, atomic, version-CAS, and
content-blind in audit/telemetry. A claimed attempt pins one binding version.
A rebind either commits before claim or affects only later attempts.

## Provider policy

Candidate order is fixed: a real local Wyoming/satellite TTS-plus-speaker
adapter, then authenticated Home/HA, else unavailable. A profile is admissible
only through an exact content-blind artifact covering authentication, physical
target binding, start boundary, completion proof, failure/ambiguity mapping,
latency, data egress, and credential ownership. Working ASR/VAD or a protocol
port does not prove egress. Cloud TTS is prohibited.

Mixed versions fail closed: Switchboard dispatches provider-capable voice only
when Messenger advertises `messenger-voice-egress.v1` and the selected profile
is admissible.

## Initiation and policy gates

Voice accepts only:

- a reply whose authenticated durable lineage is a Live Listener ingress and
  whose active ingress binding resolves one room and endpoint; or
- a send naming one active opaque Messenger endpoint.

It rejects omitted-channel/default/preference selection, entity/contact target
inference, insights, schedules, proactive delivery, broadcast, multi-channel
fanout, reactions, and drafts.

Voice does not enter origin-side generic deferral. Messenger checks current
Owner Attention Policy and `dnd`/`sleeping` context immediately before presence
and provider work. Any active condition or unreadable authority suppresses
voice, including high-priority and approved requests. It never wakes, defers,
coalesces, digests, or burst-delivers later.

## State and idempotency

The monotonic state graph is:

```text
received -> lineage_verified -> binding_resolved -> presence_authorized
         -> provider_started -> confirmed

pre-start terminal:
invalid_origin | provider_unavailable | invalid_initiation | no_binding |
quiet | no_presence | safe_retry

post-handoff terminal:
confirmed | failed | ambiguous
```

The logical key is a server-keyed digest of authenticated origin request id,
intent, endpoint reference, and binding version. Messenger atomically claims
it before handoff and fences transitions by claim generation. Concurrent
duplicates cross the provider start boundary at most once.

Confirmed replay returns its receipt. Started, failed, and ambiguous replays
return existing truth and never speak again. Safe retry exists only with
definitive `rejected_before_start` evidence. It is never scheduled; a later
explicit replay creates a new generation, reruns every current gate and uses a
new presence nonce. Across generations, only one may ever reach
`provider_started`.

## One text-only non-voice fallback

Messenger emits a content-blind fallback intent to Switchboard; it never calls
`notify()` recursively. Switchboard derives a separate key from the logical
voice key and atomically selects at most one Telegram or email target using the
existing resolver. Voice, physical channels, attachments, audio, reactions,
drafts, `entity_info` device data, and multi-target fanout are excluded.

Fallback is eligible after provider unavailable, invalid initiation, no
binding, quiet, no presence, safe retry, failed, or ambiguous. Invalid origin
has no fallback. Resolver miss yields `fallback_unavailable` and stops. A
fallback cannot create another fallback. After failed or ambiguous handoff, it
uses a fixed uncertainty notice and never repeats the original content. Normal
text quiet-hours handling may defer the fallback without deferring voice.

## Privacy, retention, and telemetry

The voice path never persists or emits message text, generated audio/PCM/TTS,
provider bodies/errors, raw presence or attestations, native room/device ids,
or credentials. The receipt contains keyed logical/endpoint digests, binding
version, categorical state/reason, provider profile version, timestamps, claim
generation, and separately keyed fallback outcome.

Detailed transitions expire after 30 days. A minimal logical digest, terminal
replay class, and binding version tombstone has no automatic expiry so cleanup
does not erase at-most-once truth. Destructive tombstone removal disables voice
first and explicitly records the loss of the old replay guarantee.

Metrics use only bounded state/reason/provider-class/freshness/fallback labels.
No ids, digests, nonces, rooms, endpoints, content, timestamps, exception text,
or provider text are attributes. Existing trace context may correlate a flow,
but structured voice spans/logs carry no logical or physical identifiers.

## Compatibility and rollback

The change is additive. Telegram, email, WhatsApp, omitted-channel resolution,
and ordinary non-voice quiet-hours behavior are unchanged. An old Messenger
rejects `voice` before side effect. Voice disable/rollback stops new claims,
settles in-flight work or marks unprovable handoffs ambiguous, and retains
bindings, receipts, tombstones, and fallback links. It does not drop non-empty
evidence, revive generic delivery tracking, widen Live Listener, or change
another channel.

## Rejected alternatives

- Live Listener TTS or speaker calls: violates ingress-only ownership.
- `entity_info`, contacts, or relationship facts as a device registry: wrong
  authority and privacy boundary.
- VAD/recent speech/generic at-home as presence: not room-specific owner proof.
- Priority/approval quiet-hours bypass: contradicts the accepted policy.
- Generic retry/dead-letter tracking: unsafe after possible physical start and
  revives a retired surface.
- Direct Messenger-to-Home calls or schema reads: violates RFC 0003 and project
  doctrine.
- Cloud TTS fallback: violates local-first/data-egress scope.

## Requirement traceability

Normative behavior is in
`openspec/changes/specify-messenger-voice-egress-contract/specs/messenger-voice-egress/spec.md`,
IDs `REQ-messenger-voice-egress-001` through
`REQ-messenger-voice-egress-011`. Implementation and verification ownership is
mapped in that change's `tasks.md` to `bu-7exe4.13.2` through
`bu-7exe4.13.5`.
