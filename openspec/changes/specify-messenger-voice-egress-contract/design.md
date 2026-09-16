# Design: Messenger-owned voice egress

## Authority and adoption

This is a candidate design implementing the six owner policy choices recorded
on parent `bu-7exe4.13`. The policy choices authorize drafting only. The exact
Git artifact must receive independent security/spec review and then separate
owner approval before any implementation child may begin. Review is not owner
approval, and owner approval is not authority for endpoint, presence, provider,
credential, audio, deployment, or canary access.

## Decision summary

The evaluation order is fixed:

1. Messenger is the only voice-egress owner; Switchboard authenticates origin
   and request lineage.
2. A server-held provider profile is admissible under the local-first policy.
3. The request is either a verified reply or an explicitly targeted send.
4. DND/quiet hours are inactive and Home returns a fresh, room-specific,
   owner-positive presence attestation for this attempt.
5. Messenger claims the physical-side-effect replay fence and hands off once.
6. A blocked, unavailable, failed, or uncertain voice attempt may ask
   Switchboard for exactly one separately keyed text-only non-voice fallback.

Failure at an earlier step prevents every later side effect. Fallback is not a
way around failed lineage authentication.

## D1: Dedicated voice contract, not generic delivery tracking

Voice is an additive `notify.v1` channel, but its physical-side-effect receipt,
endpoint registry, and state machine are Messenger-private and voice-specific.
They do not recreate the retired generic `delivery_requests`,
`delivery_attempts`, `delivery_receipts`, or dead-letter surfaces. Existing
Telegram, email, and WhatsApp paths do not read or write the new records.

The `voice` channel must be explicit. Omitted-channel and entity preference
resolution continue to consider only Telegram and email. `entity_info`,
`relationship.entity_facts`, free-form `recipient` strings, and inbound
microphone identifiers are not device registries.

## D2: Authenticated lineage has two initiation shapes

Switchboard attaches a server-generated `voice_origin.v1` assertion when it
dispatches a voice intent to Messenger. The assertion is bound to the routed
request, origin butler service identity, intent, message digest, and either:

- a verified inbound request lineage for `reply`, or
- an explicitly supplied opaque Messenger endpoint reference for `send`.

Messenger accepts the assertion only from the authenticated Switchboard
service path and verifies it against Switchboard's durable routing record. The
model-visible `origin_butler`, `request_context`, endpoint reference, mic id,
room id, headers, or tool arguments never authenticate themselves.

For a voice reply, the authoritative inbound lineage must identify Live
Listener as the ingress connector and resolve through the active binding
version from opaque inbound endpoint reference to opaque room and voice
endpoint references. Live Listener remains ingress-only and never calls a TTS,
speaker, Home, or provider action.

For an explicit send, the caller names exactly one opaque endpoint reference
already in Messenger's active registry. `insight`, scheduled/proactive,
recipient/entity inference, broadcast, multi-channel fanout containing voice,
and `react`/`draft` are invalid voice initiation shapes.

## D3: Versioned opaque endpoint registry

Messenger owns one versioned registry whose externally usable keys are opaque
random identifiers. An active binding version associates:

- an opaque voice endpoint reference;
- an opaque room reference shared only as a control-plane token with Home;
- an optional opaque Live Listener ingress endpoint reference for replies;
- a server-held provider profile reference; and
- lifecycle state (`active`, `disabled`, `retired`).

Provider-native target identifiers and Home Assistant entities remain inside
their owning adapter/configuration boundary. They do not enter `notify.v1`,
route prompts, model context, response bodies, audit notes, logs, metrics, or
traces. The registry is not `public.entity_info`. Mutations require the adopted
owner-authentication boundary, server-derived actor attribution, version/CAS
semantics, and content-blind audit evidence.

Each delivery is bound to the exact active binding version observed at claim
time. A concurrent disable, retire, or rebind wins before provider start or the
attempt is rejected. It cannot silently redirect an already-claimed logical
delivery.

## D4: Evidence-gated local-first provider interface

The provider order is policy, not discovery:

1. a real local Wyoming/satellite TTS-plus-speaker adapter;
2. an authenticated Home/HA path conforming to RFC 0028 where applicable; or
3. unavailable.

Ingress ASR, VAD, a Wyoming protocol socket, or a speaker entity by itself is
not egress evidence. A provider profile becomes `admissible` only after a
content-blind evidence artifact proves authentication, target binding,
start-boundary acknowledgement, confirmation semantics, timeout/reset
classification, safe-before-start rejection, latency bounds, data egress, and
credential ownership. Cloud TTS is outside the candidate set.

The adapter contract returns one of:

- `rejected_before_start`: proof that playback did not start;
- `started`: the provider crossed its irreversible start boundary;
- `confirmed`: completion proof satisfying the provider artifact;
- `failed_after_start`: start occurred but completion failed; or
- `unknown_after_handoff`: the adapter cannot prove whether start occurred.

HTTP/transport acceptance is not confirmation. A provider without an exact
start boundary and truthful unknown classification is inadmissible.

## D5: Fresh room presence is a one-attempt Home attestation

Messenger requests `voice_presence_attest.v1` from Home through Switchboard
after endpoint/binding resolution and immediately before claiming provider
handoff. The request carries an opaque room reference, binding version, and
single-use attempt nonce. Home derives facts from its own authoritative local
snapshot and returns a signed/authenticated categorical result bound to those
values.

An attestation is usable only when:

- it says `owner_present`;
- all required room evidence agrees;
- the newest source observation is at most 60 seconds old;
- Home issued the attestation at most 5 seconds before Messenger consumes it;
- the binding version and attempt nonce match; and
- it has not been consumed by another attempt.

Missing, stale, unavailable, unconfigured, `owner_absent`, `unknown`, or
conflicting evidence fails closed. VAD, recent speech, request recency, a
generic `at_home` signal, and owner presence in a different room are not
substitutes. Messenger persists only the categorical authorization result and
freshness class, never the attestation body or raw presence facts.

## D6: DND and quiet hours are absolute for voice

The ordinary origin-side delivery-preferences gate must not enqueue voice.
Voice proceeds to Messenger's authoritative gate so one component can make the
physical-side-effect decision and request fallback. Immediately before
presence and provider handoff, Messenger evaluates the current Owner Attention
Policy plus active `dnd`/`sleeping` context. Any active suppression returns
`quiet` and skips presence/provider work.

Priority, reply status, endpoint, approval, provider health, or owner-positive
presence cannot bypass this gate. Voice is never deferred, queued for wake,
coalesced, burst-delivered, or replayed by a generic notification flusher.

## D7: Physical-side-effect state and replay fence

The logical delivery key is a server-keyed digest of the authenticated origin
request id, intent, selected endpoint reference, and binding version. Messenger
atomically claims one row for that key before provider handoff. The state graph
is:

```text
received
  -> lineage_verified
  -> binding_resolved
  -> presence_authorized
  -> provider_started
  -> confirmed

pre-start terminal:
  invalid_origin | provider_unavailable | invalid_initiation |
  no_binding | quiet | no_presence | safe_retry

post-handoff terminal:
  confirmed | failed | ambiguous
```

Every transition is monotonic and fenced by one claim generation. Concurrent
workers can produce at most one provider start. `confirmed` replay returns the
existing content-blind receipt. `provider_started`, `confirmed`, `failed`, and
`ambiguous` permanently forbid another automatic or caller replay for the same
logical key.

`safe_retry` is permitted only from `rejected_before_start` evidence. The
system never schedules a retry. A later explicit replay of the same logical key
may claim a new generation only from `safe_retry`, rerun all policy, binding,
and presence gates, and is still fenced to at most one eventual provider start.
A timeout, reset, malformed provider response, crash after handoff, or lost
settlement proof is `ambiguous`, never `safe_retry`.

## D8: One linked text-only non-voice fallback, outside Messenger recursion

Messenger emits a content-blind fallback intent to Switchboard after an
eligible terminal voice outcome. It never calls `notify()` recursively.
Switchboard derives a separate fallback key from the original logical delivery
key and atomically resolves at most one ordinary text-only non-voice target. The resolver
may select Telegram or email under the existing recipient/preference contract;
it may not select voice, another physical channel, a target inferred from
`entity_info`, or more than one destination.

The linked fallback is available for `provider_unavailable`,
`invalid_initiation`, `no_binding`, `quiet`, `no_presence`, `safe_retry`,
`failed`, and `ambiguous`. `invalid_origin` cannot generate fallback because
authentication failed. A resolver miss returns `fallback_unavailable` and
ends the chain. The fallback cannot itself create another fallback.

For a pre-start outcome, the text-only fallback may carry the original message
subject to its existing privacy and quiet-hours rules. For `failed` or
`ambiguous`, the fallback uses a fixed uncertainty notice and does not repeat
the original message content, because the room may already have heard some or
all of it. Fallback delivery may itself defer under the ordinary non-voice
contract; that does not defer or retry voice.

## D9: Persistence, retention, and observability

Generated audio, PCM/TTS bytes, provider request/response bodies, raw presence,
attestation payloads, native device/room ids, message text, and credentials are
memory-only and are never written to a database, blob store, audit event, log,
metric, or trace by the voice path.

The voice receipt stores only keyed logical/endpoint digests, binding version,
categorical states/reasons, provider profile version, timestamps, claim
generation, and the separately keyed fallback outcome. Detailed transition
rows expire after 30 days. A minimal replay tombstone containing the logical
digest, terminal replay class, and binding version is retained without
automatic expiry so at-most-once truth survives cleanup. Owner-authorized
destructive removal must first disable voice globally and warn that deleting a
tombstone removes its replay guarantee.

Metrics use only bounded labels: state, reason, provider profile class,
presence freshness class, and fallback outcome. Stable ids, hashes, nonces,
rooms, endpoints, content, timestamps, and provider errors are forbidden metric
attributes. Logs/traces may correlate through an existing trace id in the
telemetry backend but must not record the logical key or physical identifiers.

## D10: Compatibility and rollback

Existing non-voice `notify.v1` inputs and channel inference are unchanged.
Older Messenger instances reject the additive `voice` channel cleanly before
side effect. During mixed-version rollout, Switchboard may dispatch voice only
after Messenger advertises the exact contract version and an admissible
provider profile; otherwise the outcome is `provider_unavailable` and normal
fallback policy may run.

Rollback disables new voice claims first, waits for in-flight claims to settle
or marks unprovable handoffs `ambiguous`, and retains endpoint versions,
receipts, replay tombstones, and fallback links. Rollback never re-enables Live
Listener egress, widens generic delivery tracking, drops non-empty evidence
tables, or changes Telegram/email/WhatsApp behavior. Re-enable requires the
same contract version, provider evidence, and fresh control-plane checks.

## Rejected alternatives

- Live Listener performs TTS: violates ingress-only connector ownership.
- Use `entity_info` or contact facts for speakers: confuses people/identities
  with physical endpoint authority.
- Treat VAD or recent speech as presence: does not prove the owner is present.
- Rely on priority or approval to bypass quiet hours: contradicts the accepted
  unconditional voice suppression policy.
- Generic delivery retries/dead letters: cannot distinguish no-start from
  possible physical playback and resurrects retired tracking.
- Direct Messenger-to-Home calls or cross-schema reads: violates the MCP-only
  Switchboard boundary.
- Cloud TTS fallback: violates local-first scope and expands data egress.
