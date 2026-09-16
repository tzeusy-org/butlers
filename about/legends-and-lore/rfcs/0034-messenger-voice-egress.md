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
| Switchboard | Cryptographically authenticated service/origin lineage, durable route witness, broker-hop verification, fallback target resolution and one-fallback key | Endpoint registry, presence facts, physical outcome |
| Messenger | Opaque endpoint/binding versions, admissible provider selection, policy/presence orchestration, handoff truth, voice receipt/replay fence, fallback intent | Ingress capture, Home facts, direct Home schema/calls, contact/device inference |
| Home | Room-specific presence facts and a cryptographically authenticated categorical attestation | Voice target selection, message content, delivery retry, or voice actuation under the current RFC 0028 contract |
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

### Voice control-plane service authentication

Voice control messages use a dedicated `voice-control.v1` compact JWS
capability, signed with Ed25519 (`alg=EdDSA`). MCP reachability, TLS/network
location, `request_context.source_endpoint_identity`, `origin_butler`, or any
other caller field is not service authentication. Every capability fixes the
algorithm and binds `kid`, exact issuer and audience, contract/action, a
canonical payload digest, a 256-bit random nonce, integer `iat`, and `exp`,
with `0 < exp - iat <= 10 seconds` and at most five seconds of clock skew. A
receiver resolves `kid` only in its immutable deployment keyring; token-selected
algorithms, dynamic key URLs, unknown issuers/audiences, wrong signatures, and
missing capabilities fail before body parsing or protected state access.

Switchboard, Messenger, and Home each have a distinct service principal backed
by an isolated signer process. The canonical deployment supervisor gives the corresponding
daemon object only a pre-connected, close-on-exec signer handle whose issuer,
audience, and allowed actions are fixed by that handle; the signing protocol
accepts only a canonical payload digest and timing/nonce material. It is not an
MCP/HTTP endpoint and the handle is never inherited by a spawned LLM/runtime
child. The operator provisions the signer's strict document as that isolated
process's only deployment secret at
`/run/secrets/voice_control_signing_key`; verifier processes receive peer
public keys at `/run/secrets/voice_control_verifiers`. Private keys never enter
the all-butlers daemon process, another signer, an LLM/runtime child,
environment variables, the generic Secrets surface, or dynamic discovery.
Each receiver atomically claims the
`(issuer, audience, nonce_digest)` in its own durable receipt store before
protected work and retains that claim beyond `exp` plus skew, so a valid
capability cannot be replayed across a process restart. Raw nonce, signature,
and signed control envelope are not persisted or emitted to telemetry.

The signer document is strict JSON with only `version: 1`, `alg: "EdDSA"`,
`issuer`, `kid`, unpadded base64url raw 32-byte `private_key_b64u`,
`sign_from`, and nullable `sign_until`. The public verifier document is strict
JSON with `version: 1` and one entry per expected issuer; each entry has one
`current` key and at most one `retiring` key containing `alg`, `kid`, unpadded
base64url raw 32-byte `public_key_b64u`, `sign_from`, and, for retiring keys,
`sign_until` and `accept_until`. Unknown fields, duplicate issuers or key IDs,
and a signer whose derived public key or time bounds do not match its verifier
entry are invalid. Private signer files must be owned by and readable only by
the service account; group/world access is permission-unsafe.

The protected header is exactly `alg`, `kid`, and
`typ: "voice-control+jws"`. The signed claims are exactly `iss`, `aud`,
`action`, `contract_version`, `payload_sha256`, `control_nonce`, `iat`, and
`exp`; `payload_sha256` is unpadded base64url SHA-256 of UTF-8 RFC 8785
canonical JSON. Allowed issuer/audience/action triples are fixed:

| Issuer | Audience | Action |
|---|---|---|
| `switchboard` | `messenger.voice-origin.v1` | `dispatch_origin` |
| `messenger` | `switchboard.voice-presence.v1` | `request_presence` |
| `switchboard` | `home.voice-presence.v1` | `broker_presence` |
| `home` | `messenger.voice-presence.v1` | `attest_presence` |
| `switchboard` | `messenger.voice-presence-relay.v1` | `relay_presence` |

The per-hop `control_nonce` is distinct from the end-to-end
`attempt_nonce`; all control nonces and the attempt nonce are independently
single-use.

The brokered presence exchange authenticates every hop:

1. Messenger signs the presence request for the Switchboard audience;
2. Switchboard verifies and consumes it, then signs the exact payload digest
   for Home;
3. Home verifies and consumes the broker capability, then signs its result for
   Messenger, bound to the request nonce, room token, and binding version;
4. Switchboard verifies Home's result and relays it byte-for-byte inside a new
   Switchboard-signed envelope whose digest covers the Home JWS; and
5. Messenger verifies and consumes both the outer Switchboard capability and
   the inner Home capability before considering the categorical result.

`voice_origin.v1` is likewise carried as a Switchboard-signed capability for
Messenger. A generic `route.execute` call whose caller asserts `switchboard`
without that capability has no voice authority.

Signer-sidecar and verifier snapshots are validated once at process startup
and remain
immutable until restart. Missing, malformed, permission-unsafe, mismatched, or
not-yet-valid material makes only the voice control plane unavailable; it does
not fall back to caller identity, bearer tokens, or unsigned MCP. Rotation is
two phase: deploy a public keyring containing distinct current and retiring
keys to every verifier and restart them; confirm content-blind readiness for
the new `kid`; install and restart each matching signer sidecar at cutover; accept the
retiring key only for capabilities issued before cutover and only through its
bounded `accept_until`; then remove it and restart verifiers. For cutover `T`,
`current.sign_from == retiring.sign_until == T`, the retiring signer may issue
only with `iat <= T`, the current signer only with `iat >= T`, and
`retiring.accept_until` is in `[T+15s, T+60s]` so every pre-cutover capability
can finish its ten-second lifetime plus five-second skew without leaving a
long-lived overlap. Verifiers reject the retiring key after `accept_until`
even if an old immutable snapshot remains mounted. No production key
provisioning, mount activation, or rotation is authorized by this draft.

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

The assertion is carried in the consumed Switchboard-to-Messenger
`voice-control.v1` capability and matched to Switchboard's durable route
record. The JSON fields alone carry no authority. A reply has
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

Home returns a categorical result inside the authenticated nested relay above:

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
attestation no more than 5 seconds old at the provider-handoff claim authorizes
the attempt. Both signed hops, the attempt nonce, and the binding version must
match and be single-use. Messenger persists only the result/freshness category.
The attestation body, signatures, and Home's raw evidence are discarded.

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
content-blind in audit/telemetry. After the stable logical claim, a claimed
generation pins one binding version under CAS. A rebind either wins before that
pin or affects only later logical deliveries and eligible safe-retry
generations; it never changes the key or result of a confirmed, failed, or
ambiguous delivery.

## Provider policy

Candidate order is fixed: a real local Wyoming/satellite TTS-plus-speaker
adapter, then unavailable. Home/HA remains a named second candidate but is
currently inadmissible: RFC 0028 does not define how speech content reaches an
HA service without durable requested/observed content, how its protected-action
approval delay re-runs current DND and obtains a new five-second presence
attestation, or how its `succeeded`/`failed`/`unverified` receipt maps to this
RFC's five adapter results. No evidence artifact may admit a Home/HA voice
profile until a separate accepted RFC 0028 amendment defines those wire, risk,
approval, receipt, freshness, and content-blind persistence seams. Home's
presence role does not authorize Home actuation.

A local provider profile is admissible only through an exact content-blind
artifact covering authentication, physical target binding, start boundary,
completion proof, failure/ambiguity mapping, latency, data egress, and
credential ownership. Working ASR/VAD or a protocol port does not prove
egress. Cloud TTS is prohibited.

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

The stable logical key is a server-keyed digest of contract version,
authenticated canonical origin request id, and intent. Resolved endpoint and
binding version are immutable receipt fields for the claimed generation, never
logical-key inputs. Messenger looks up or atomically claims the stable key
after lineage verification and before current binding resolution; therefore a
rebind cannot route around an existing confirmed, failed, or ambiguous receipt
or tombstone. Concurrent duplicates cross the provider start boundary at most
once.

Confirmed replay returns its receipt. Started, failed, and ambiguous replays
return existing truth and never speak again. Safe retry exists only with
definitive `rejected_before_start` evidence. It is never scheduled; a later
explicit replay creates a new generation, reruns every current gate and uses a
new presence nonce. Across generations, only one may ever reach
`provider_started`.

`presence_authorized` is a persisted audit milestone, not reusable authority.
The provider-handoff claim is a distinct durable marker written immediately
before dispatch. Recovery of a row that reached `presence_authorized` but has
no provider-handoff marker proves no provider dispatch was attempted; it is not
ambiguous. Before continuing, the recovering worker keeps the pinned binding,
re-evaluates current DND/quiet policy, invalidates the old presence authority,
and obtains a newly signed Home attestation under a new nonce. The handoff
marker may be claimed only while that replacement attestation is at most five
seconds old. A crash at or after the handoff marker remains ambiguous unless
the provider supplies definitive no-start evidence.

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
or credentials. The receipt contains keyed logical/endpoint digests, the
immutable binding version selected for each generation, categorical
state/reason, provider profile version, timestamps, claim generation, and
separately keyed fallback outcome.

A control-capability replay receipt stores only issuer, audience, action,
`kid`, nonce digest, expiry, and consumed-at time, and expires after the
capability's expiry-plus-skew replay window. It never stores the raw nonce,
signature, payload, payload digest, message digest, or protected body.

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
- Home/HA voice before an accepted RFC 0028 amendment: the current contract
  cannot reconcile protected-action approval, fresh DND/presence, receipt
  categories, and content-blind persistence.
- Cloud TTS fallback: violates local-first/data-egress scope.

## Requirement traceability

Normative behavior is in
`openspec/changes/specify-messenger-voice-egress-contract/specs/messenger-voice-egress/spec.md`,
IDs `REQ-messenger-voice-egress-001` through
`REQ-messenger-voice-egress-011`. Implementation and verification ownership is
mapped in that change's `tasks.md` to `bu-7exe4.13.2` through
`bu-7exe4.13.5`.
