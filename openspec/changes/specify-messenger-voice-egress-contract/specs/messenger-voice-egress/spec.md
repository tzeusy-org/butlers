## ADDED Requirements

### Requirement: Messenger owns authenticated voice egress

Messenger SHALL be the sole owner of outbound in-room voice semantics,
endpoint binding, provider handoff truth, physical-side-effect state, and
fallback intent. Switchboard SHALL be the sole authority that authenticates
the requesting service and attaches `voice_origin.v1` lineage. Messenger SHALL
accept voice only over that authenticated Switchboard route and SHALL verify
the assertion against Switchboard's durable route record before any endpoint,
presence, provider, or fallback work.

`voice_origin.v1` SHALL bind the canonical origin request id, origin butler
service identity, intent, message digest, and either verified inbound request
lineage (`reply`) or one explicit opaque Messenger endpoint reference (`send`).
No caller field, `origin_butler` string, `request_context`, header, microphone
id, room id, endpoint id, or model-visible value SHALL authenticate itself.
Messenger and Home SHALL exchange deterministic control-plane messages only by
MCP through Switchboard; neither SHALL call the other directly or read the
other's schema. Live Listener SHALL remain ingress-only.

Every voice control-plane hop SHALL use a dedicated `voice-control.v1` compact
JWS signed by the sending service's distinct server-held Ed25519 principal. The
capability SHALL fix `alg=EdDSA` and bind `kid`, exact issuer and audience,
contract/action, canonical payload digest, a 256-bit random nonce, integer
`iat`, and `exp` with `0 < exp - iat <= 10 seconds` and at most five seconds of
clock skew. Receivers SHALL resolve the key only from an immutable startup
keyring, atomically consume `(issuer, audience, nonce_digest)` in a durable
receipt before protected work, and retain it beyond expiry plus skew so replay
fails across restart. Token-selected algorithms, dynamic key URLs, unsigned or
wrong-key messages, caller-asserted identity, and bearer fallback SHALL NOT
confer authority.

The protected header SHALL contain exactly `alg`, `kid`, and
`typ: "voice-control+jws"`. Signed claims SHALL contain exactly `iss`, `aud`,
`action`, `contract_version`, `payload_sha256`, `control_nonce`, `iat`, and
`exp`; `payload_sha256` SHALL be unpadded base64url SHA-256 of UTF-8 RFC 8785
canonical JSON. The only allowed issuer/audience/action triples SHALL be
`switchboard`/`messenger.voice-origin.v1`/`dispatch_origin`,
`messenger`/`switchboard.voice-presence.v1`/`request_presence`,
`switchboard`/`home.voice-presence.v1`/`broker_presence`,
`home`/`messenger.voice-presence.v1`/`attest_presence`, and
`switchboard`/`messenger.voice-presence-relay.v1`/`relay_presence`. The
per-hop `control_nonce` SHALL be distinct from the end-to-end
`attempt_nonce`; each SHALL be independently single-use.

Each principal SHALL run in an isolated signer process. The canonical
deployment supervisor SHALL give only the matching daemon object a
pre-connected, close-on-exec
signer handle with fixed issuer/audience/action authority; the signer protocol
SHALL accept only canonical payload digest and timing/nonce material, SHALL NOT
be an MCP/HTTP endpoint, and SHALL NOT be inherited by an LLM/runtime child.
Only the signer process SHALL receive its strict document at
`/run/secrets/voice_control_signing_key`; verifier processes SHALL receive peer
public keys at `/run/secrets/voice_control_verifiers`. No private key SHALL
enter the all-butlers daemon process, another signer, an LLM/runtime child, an
environment variable, dynamic discovery, or the generic Secrets surface.
Missing, malformed, permission-unsafe,
mismatched, or not-yet-valid material SHALL leave voice unavailable. Rotation
SHALL install current-plus-retiring verifier keyrings and restart/verify every
receiver before signer-sidecar cutover, bound retiring-key signing to cutover
and
acceptance to `accept_until`, and remove the retiring verifier on a later
restart. For cutover `T`, `current.sign_from` SHALL equal
`retiring.sign_until == T`; the retiring signer SHALL issue only with
`iat <= T`, the current signer only with `iat >= T`, and
`retiring.accept_until` SHALL be within `[T+15s, T+60s]`. A verifier SHALL
reject the retiring key after `accept_until` even before restart. This
candidate SHALL NOT authorize production key provisioning or activation.

The signer document SHALL be strict JSON containing only `version: 1`,
`alg: "EdDSA"`, `issuer`, `kid`, unpadded base64url raw 32-byte
`private_key_b64u`, `sign_from`, and nullable `sign_until`. The verifier
document SHALL be strict JSON with `version: 1` and one entry per expected
issuer, each with one `current` key and at most one `retiring` key carrying
`alg`, `kid`, unpadded base64url raw 32-byte `public_key_b64u`, `sign_from`,
and, for a retiring key, `sign_until` and `accept_until`. Unknown fields,
duplicate issuers/key IDs, signer/verifier public-key or time-bound mismatch,
or a private signer file readable by group/world SHALL fail voice closed. A
public verifier file writable by group/world SHALL also fail voice closed.

For presence, Messenger SHALL sign the request to Switchboard; Switchboard
SHALL verify/consume it and sign its exact digest to Home; Home SHALL
verify/consume that request and sign the nonce/version-bound result to
Messenger; and Switchboard SHALL verify Home then relay the unchanged Home JWS
inside a Switchboard-signed envelope whose digest covers it. Messenger SHALL
verify and consume both signatures before using the result.

ID: REQ-messenger-voice-egress-001
Source: bu-7exe4.13 owner policies; RFC 0034 D1-D2
Scope: owner-approval-required

#### Scenario: Authenticated Switchboard lineage is accepted

- **WHEN** Switchboard presents a `voice_origin.v1` assertion whose service identity and durable route record match every bound field
- **THEN** Messenger SHALL mark the attempt `lineage_verified`
- **AND** it MAY proceed to endpoint and policy validation

#### Scenario: Caller-asserted lineage fails before protected work

- **WHEN** a caller reaches Messenger without authenticated Switchboard service identity, or any asserted field disagrees with the durable route record
- **THEN** Messenger SHALL return terminal `invalid_origin`
- **AND** it SHALL NOT read endpoint or presence state, contact Home, contact a provider, or create a fallback

#### Scenario: Missing or wrong service key fails closed

- **WHEN** `voice_origin.v1` or any presence-control hop has no capability, an unknown `kid`, a wrong issuer/audience/action/digest, or an invalid signature
- **THEN** the receiver SHALL reject it before body parsing, durable route lookup, endpoint/presence state, provider access, or fallback
- **AND** no caller-asserted service identity or unsigned MCP path SHALL substitute

#### Scenario: Consumed capability cannot replay across restart

- **WHEN** a previously consumed valid capability is presented again before or after the receiver restarts
- **THEN** the durable nonce receipt SHALL reject it before protected work
- **AND** the receiver SHALL NOT repeat route, presence, provider, or fallback effects

#### Scenario: Restart and key rotation fail voice closed

- **WHEN** a signer sidecar or verifier restarts with missing, unsafe, mismatched, not-yet-valid, or expired key material
- **THEN** voice control SHALL remain unavailable without unsigned or bearer fallback while existing non-voice channels remain unchanged
- **AND** during rotation the new signer SHALL issue only after every receiver reports the new `kid` issuable, while the retiring key SHALL be rejected after `accept_until`

#### Scenario: Live Listener cannot perform egress

- **WHEN** Live Listener captures, detects, or transcribes audio
- **THEN** its authority SHALL end at normalized `ingest.v1` submission to Switchboard
- **AND** it SHALL NOT synthesize speech, select a speaker, query room presence, or invoke a provider/Home action

### Requirement: Voice providers are evidence-gated and local-first

Messenger SHALL consider provider profiles in this order only: a real local
Wyoming/satellite TTS-plus-speaker adapter, then unavailable. Home/HA SHALL
remain inadmissible under the current RFC 0028 because it does not define the
speech-content handoff, the protected-action approval delay followed by fresh
DND/presence evaluation, the mapping from Home's receipt outcomes to the five
voice adapter outcomes, or this contract's content-blind persistence boundary.
No Home/HA evidence artifact SHALL become admissible until a separate accepted
RFC 0028 amendment defines those wire, risk, approval, receipt, freshness, and
persistence seams. Home presence attestation SHALL NOT authorize Home
actuation. Cloud TTS SHALL NOT be admissible. ASR, VAD, a protocol listener, or
a speaker entity alone SHALL NOT constitute voice-egress evidence.

A server-held provider profile SHALL be `admissible` only when an exact,
content-blind evidence artifact proves authentication and target binding,
provider start boundary, completion confirmation, timeout/reset/parse failure
classification, definitive rejection before start, latency bounds, data
egress, and credential ownership. The adapter SHALL distinguish
`rejected_before_start`, `started`, `confirmed`, `failed_after_start`, and
`unknown_after_handoff`. Transport acceptance or HTTP success alone SHALL NOT
mean `confirmed`. With no admissible profile, voice SHALL remain unavailable.

ID: REQ-messenger-voice-egress-002
Source: bu-7exe4.13 owner policies; RFC 0034 D4
Scope: owner-approval-required

#### Scenario: Local provider is considered first

- **WHEN** a local Wyoming/satellite profile has an evidence artifact
- **THEN** Messenger SHALL evaluate that local profile
- **AND** it SHALL remain unavailable if the local profile is inadmissible

#### Scenario: Current RFC 0028 cannot admit Home voice

- **WHEN** a Home/HA TTS service or evidence artifact is proposed under the current RFC 0028
- **THEN** Messenger SHALL reject that provider profile as inadmissible
- **AND** Home SHALL receive no speech content or actuation request from this voice path

#### Scenario: Ingress evidence is insufficient

- **WHEN** the system has working Live Listener ASR or VAD but no proven TTS-plus-speaker start and confirmation contract
- **THEN** no local voice provider SHALL be marked admissible

#### Scenario: No provider passes

- **WHEN** neither local-first candidate satisfies every evidence field
- **THEN** the attempt SHALL terminate `provider_unavailable` before presence or provider access
- **AND** the system SHALL NOT fall through to cloud TTS

#### Scenario: Acceptance is not confirmation

- **WHEN** a provider accepts a request but the adapter cannot prove playback completion
- **THEN** Messenger SHALL NOT classify the delivery `confirmed`
- **AND** it SHALL classify the post-handoff result `failed` or `ambiguous` according to the provider evidence contract

### Requirement: Voice initiation is reply or one explicit endpoint send only

A voice intent SHALL be valid only as (a) `reply` to authenticated inbound Live
Listener lineage that resolves through an active mic-to-room-to-endpoint
binding, or (b) `send` naming exactly one active opaque Messenger endpoint
reference. Voice SHALL NOT be selected by omitted-channel defaults,
`prefers-channel`, contact/entity resolution, free-form recipient lookup,
schedule, insight/proactive delivery, broadcast, multi-channel fanout,
`react`, or `draft`. Raw mic, room, provider, or device identifiers SHALL NOT
be accepted as targets.

ID: REQ-messenger-voice-egress-003
Source: bu-7exe4.13 owner policies; RFC 0034 D2
Scope: owner-approval-required

#### Scenario: Verified Live Listener reply is eligible

- **WHEN** a `reply` carries authenticated Switchboard lineage for a Live Listener request and its active binding resolves one endpoint
- **THEN** Messenger MAY continue with the resolved opaque endpoint and exact binding version

#### Scenario: Explicit endpoint send is eligible

- **WHEN** a `send` carries authenticated origin lineage and names exactly one active opaque endpoint reference
- **THEN** Messenger MAY continue without inferring a target from a person, contact, room name, or device id

#### Scenario: Proactive voice is rejected

- **WHEN** voice is requested by an insight, schedule, omitted channel, automatic preference, broadcast, multi-channel fanout, `react`, or `draft`
- **THEN** Messenger SHALL terminate `invalid_initiation` before presence or provider access
- **AND** no priority or approval SHALL convert the request into an eligible voice attempt

#### Scenario: Caller-asserted physical identifier is rejected

- **WHEN** a request supplies a raw mic, room, Home Assistant entity, provider target, or free-form recipient in place of an opaque endpoint reference
- **THEN** Messenger SHALL reject the voice target
- **AND** it SHALL NOT store that value in `public.entity_info` or any voice receipt

### Requirement: Messenger endpoint bindings are opaque and versioned

Messenger SHALL own a dedicated registry of random opaque endpoint references.
Each immutable binding version SHALL associate one endpoint reference with one
opaque room reference, optional opaque Live Listener ingress reference,
server-held provider profile reference, and lifecycle state `active`,
`disabled`, or `retired`. Provider-native targets and Home Assistant entity ids
SHALL remain inside their owning adapter/configuration boundary and SHALL NOT
enter `notify.v1`, prompts, model context, response bodies, audit notes, logs,
metrics, or traces. `public.entity_info` and relationship facts SHALL NOT be
used as voice device registries.

Registry mutations SHALL require the adopted fail-closed owner-authentication
boundary, server-derived actor attribution, atomic version/CAS semantics, and
content-blind audit evidence. After the stable logical claim is created, its
generation SHALL pin the exact active binding version observed under CAS. A
concurrent disable, retirement, or rebind SHALL either win before provider
start or leave the claimed delivery on its original version; it SHALL NOT
redirect it silently.

ID: REQ-messenger-voice-egress-004
Source: bu-7exe4.13 owner policies; RFC 0034 D3
Scope: owner-approval-required

#### Scenario: Active binding resolves without physical identifiers

- **WHEN** Messenger resolves an active opaque endpoint reference
- **THEN** it SHALL obtain an opaque room reference, provider profile reference, and binding version
- **AND** no model-visible or client response SHALL receive the underlying physical target

#### Scenario: Stale binding version fails closed

- **WHEN** lineage names a binding version that is disabled, retired, replaced, or not the active version required by the attempt
- **THEN** Messenger SHALL terminate `no_binding` before presence or provider access

#### Scenario: Concurrent rebind cannot redirect an attempt

- **WHEN** an owner rebind races a delivery claim
- **THEN** the delivery SHALL either observe the new version before claim or remain bound to the prior claimed version
- **AND** it SHALL never use a mixture of versions

#### Scenario: Unauthenticated mutation changes nothing

- **WHEN** registry owner authentication is unavailable or invalid
- **THEN** the mutation SHALL fail before protected body/state access
- **AND** no binding, version, or audit record SHALL change

### Requirement: Every attempt requires fresh room-specific owner-positive presence

After binding resolution and before provider claim, Messenger SHALL request one
`voice_presence_attest.v1` from Home through Switchboard. The request SHALL
carry only the opaque room reference, binding version, and a single-use attempt
nonce. Home SHALL derive the result from its own authoritative local snapshot
and return the nested Home-and-Switchboard signed categorical attestation
required by REQ-messenger-voice-egress-001, bound to those values.

Messenger SHALL authorize presence only when the result is `owner_present`,
all required room evidence agrees, the newest source observation is no more
than 60 seconds old, Home issued the attestation no more than 5 seconds before
the provider-handoff claim, both received service capabilities verify, binding
version and attempt nonce match, and the outer/inner control nonces and attempt
nonce have not been used.
Missing, stale, unavailable, unconfigured, `owner_absent`, `unknown`, or
conflicting evidence SHALL fail closed. VAD, recent speech, request recency,
generic `at_home`, or presence in another room SHALL NOT substitute. Messenger
SHALL persist only categorical authorization and freshness class, never raw
presence or the attestation payload.

ID: REQ-messenger-voice-egress-005
Source: bu-7exe4.13 owner policies; RFC 0034 D5
Scope: owner-approval-required

#### Scenario: Fresh matching owner-positive attestation authorizes presence

- **WHEN** Home returns `owner_present` for the exact room, binding version, and nonce with agreeing evidence observed within 60 seconds and an attestation age within 5 seconds
- **THEN** Messenger SHALL mark the attempt `presence_authorized`
- **AND** it SHALL consume the nonce exactly once

#### Scenario: Unknown or conflicting presence fails closed

- **WHEN** Home returns missing, unavailable, unconfigured, `owner_absent`, `unknown`, or conflicting room evidence
- **THEN** Messenger SHALL terminate `no_presence`
- **AND** it SHALL NOT claim provider start

#### Scenario: Stale evidence fails closed

- **WHEN** the newest required observation is older than 60 seconds or the attestation is older than 5 seconds at the provider-handoff claim
- **THEN** Messenger SHALL terminate `no_presence` even if the last known value was `owner_present`

#### Scenario: Presence cannot be replayed or inferred

- **WHEN** an attestation nonce is reused, its binding version differs, or only VAD/recent speech/generic home presence is available
- **THEN** Messenger SHALL reject the attestation
- **AND** it SHALL request no provider handoff

#### Scenario: Caller-forged Home attestation fails closed

- **WHEN** a categorical presence result lacks either the valid Home signature or the valid Switchboard relay signature, or either signed digest differs
- **THEN** Messenger SHALL terminate `no_presence` before provider handoff
- **AND** a caller-asserted `owner_present` value SHALL carry no authority

### Requirement: DND and quiet hours always suppress voice

Messenger SHALL evaluate the current Owner Attention Policy and active
`dnd`/`sleeping` context immediately before requesting presence or provider
handoff. Any active quiet-hours or DND condition SHALL terminate the attempt as
`quiet`. Voice suppression SHALL apply regardless of priority, reply status,
endpoint, approval, provider health, or presence. Voice SHALL never be
deferred, queued for wake, coalesced, digest-composed, burst-delivered, or
processed by the generic deferred-notification flusher.

The originating `notify()` delivery-preferences gate SHALL pass explicit voice
to Messenger without enqueueing it; that is routing to the authoritative gate,
not a quiet-hours bypass. A policy/context read failure at the authoritative
gate SHALL fail closed for voice.

ID: REQ-messenger-voice-egress-006
Source: bu-7exe4.13 owner policies; RFC 0034 D6
Scope: owner-approval-required

#### Scenario: Quiet hours suppress high-priority voice

- **WHEN** a voice reply or explicit send arrives during an active quiet-hours interval with any priority, including `high`
- **THEN** Messenger SHALL terminate `quiet`
- **AND** it SHALL NOT request presence or provider handoff

#### Scenario: DND suppresses otherwise valid voice

- **WHEN** active `dnd` or `sleeping` context exists for an otherwise valid voice attempt
- **THEN** Messenger SHALL terminate `quiet` even if room presence would be owner-positive

#### Scenario: Voice is never deferred

- **WHEN** an origin-side delivery preference, Owner Attention Policy, or DND condition applies to voice
- **THEN** no `deferred_notifications` row or coalescing group SHALL be created for voice
- **AND** the original voice attempt SHALL never play later after the condition clears

#### Scenario: Policy uncertainty fails closed

- **WHEN** Messenger cannot obtain authoritative quiet-hours or DND state
- **THEN** it SHALL terminate `quiet` with a content-blind policy-unavailable reason
- **AND** it SHALL NOT interpret missing policy evidence as permission

### Requirement: Provider handoff follows a monotonic physical-side-effect state machine

Messenger SHALL use the following monotonic states: `received`,
`lineage_verified`, `binding_resolved`, `presence_authorized`,
`provider_started`, and `confirmed`; pre-start terminals `invalid_origin`,
`provider_unavailable`, `invalid_initiation`, `no_binding`, `quiet`,
`no_presence`, and `safe_retry`; post-handoff terminals `confirmed`, `failed`,
and `ambiguous`. Each transition SHALL be fenced by the current claim
generation and SHALL NOT move backward.

Only `rejected_before_start` proof SHALL produce `safe_retry`. `started` SHALL
be recorded before accepting a confirmation. `failed_after_start` SHALL produce
`failed`. Timeout, reset, malformed response, crash after handoff, lost
settlement proof, or `unknown_after_handoff` SHALL produce `ambiguous`. Neither
`failed` nor `ambiguous` SHALL be automatically or caller-retried for the same
logical key.

`presence_authorized` SHALL be persisted only as an audit milestone and SHALL
NOT be reusable authorization. Messenger SHALL persist a distinct fenced
provider-handoff marker immediately before dispatch. Recovery of
`presence_authorized` without that marker SHALL prove that no provider dispatch
was attempted and SHALL NOT become `ambiguous`; before continuing on the pinned
binding, recovery SHALL re-read current DND/quiet authority and obtain a newly
signed Home attestation under a new nonce. The provider-handoff marker SHALL be
claimable only while that replacement attestation is no more than five seconds
old. Recovery at or after the marker SHALL be `ambiguous` absent definitive
`rejected_before_start` evidence.

ID: REQ-messenger-voice-egress-007
Source: bu-7exe4.13 owner policies; RFC 0034 D7
Scope: owner-approval-required

#### Scenario: Confirmed playback follows the complete path

- **WHEN** every gate passes, the provider reports its start boundary, and exact confirmation evidence arrives
- **THEN** Messenger SHALL transition through `provider_started` to `confirmed`
- **AND** it SHALL return a content-blind confirmed receipt

#### Scenario: Definitive pre-start rejection is safe retry

- **WHEN** the provider proves `rejected_before_start`
- **THEN** Messenger SHALL settle `safe_retry`
- **AND** it SHALL NOT claim that playback started or succeeded

#### Scenario: Possible start is ambiguous

- **WHEN** a timeout, reset, malformed response, process crash, or unknown result occurs after handoff and no no-start proof exists
- **THEN** Messenger SHALL settle or recover the attempt as `ambiguous`
- **AND** it SHALL NOT issue another voice handoff for the logical key

#### Scenario: Failure after start is not retryable

- **WHEN** the provider proves start but reports failed completion
- **THEN** Messenger SHALL settle `failed`
- **AND** it SHALL NOT retry speech because partial or complete playback may already have occurred

#### Scenario: Pre-handoff crash requires fresh authorization

- **WHEN** recovery finds persisted `presence_authorized` with no provider-handoff marker
- **THEN** it SHALL keep the pinned binding but re-evaluate current DND/quiet state and request a newly signed Home attestation with a new nonce
- **AND** it SHALL claim no handoff from the old attestation and SHALL terminate `quiet` or `no_presence` if either fresh gate fails

### Requirement: Logical delivery idempotency prevents duplicate speech

After lineage verification and before current binding resolution, Messenger
SHALL look up or atomically claim a stable logical key that is a server-keyed
digest of contract version, authenticated canonical origin request id, and
intent. Selected endpoint reference and binding version SHALL be immutable
receipt fields for each claimed generation and SHALL NOT be logical-key inputs.
Concurrent workers SHALL produce at most one provider start for that key.
Replay of `confirmed` SHALL return the existing receipt. Replay of
`provider_started`, `failed`, or `ambiguous` SHALL return the existing
non-retryable terminal truth without resolving a new binding, requesting
presence, or creating another handoff.

The system SHALL NOT schedule voice retries. A later explicit replay MAY claim
a new generation only from `safe_retry`; it SHALL rerun lineage, provider,
binding, DND, and fresh-presence gates. Across all generations for one logical
key, at most one generation may ever cross `provider_started`.

ID: REQ-messenger-voice-egress-008
Source: bu-7exe4.13 owner policies; RFC 0034 D7
Scope: owner-approval-required

#### Scenario: Concurrent duplicates start once

- **WHEN** two workers race with the same logical voice delivery
- **THEN** one atomic claim SHALL own the transition toward provider start
- **AND** at most one provider call SHALL cross the start boundary

#### Scenario: Confirmed replay returns the receipt

- **WHEN** a confirmed logical delivery is replayed
- **THEN** Messenger SHALL return the existing confirmed receipt
- **AND** it SHALL NOT repeat presence or provider access

#### Scenario: Ambiguous replay never speaks again

- **WHEN** an ambiguous logical delivery is replayed by a caller, scheduler, recovery path, or crash scan
- **THEN** Messenger SHALL return the existing ambiguous truth
- **AND** it SHALL NOT create a new provider handoff

#### Scenario: Rebind cannot bypass confirmed replay tombstone

- **WHEN** an owner has replaced the endpoint binding version after a logical delivery reached `confirmed` and that origin request is replayed
- **THEN** the stable logical key SHALL resolve the existing receipt and its originally selected binding version
- **AND** Messenger SHALL NOT resolve the replacement binding, request presence, or hand off speech again

#### Scenario: Rebind cannot bypass failed or ambiguous tombstone

- **WHEN** an owner has replaced the endpoint binding version after a logical delivery reached `failed` or `ambiguous` and that origin request is replayed
- **THEN** the stable logical key SHALL resolve the existing non-retryable terminal truth and its originally selected binding version
- **AND** Messenger SHALL NOT create a new generation or provider handoff

#### Scenario: Explicit safe retry reruns every gate

- **WHEN** a caller explicitly replays a logical delivery settled `safe_retry`
- **THEN** a new fenced generation MAY proceed only after all current gates and a new presence nonce pass
- **AND** no background process SHALL create that generation automatically

### Requirement: At most one linked text-only non-voice fallback is resolved outside Messenger

For an eligible terminal voice outcome, Messenger MAY emit one content-blind
fallback intent to Switchboard and SHALL NOT call `notify()` recursively.
Switchboard SHALL derive a separate fallback key from the logical voice key and
atomically resolve at most one ordinary text-only non-voice destination under the
existing recipient/preference contract. The resolver SHALL select only
Telegram or email, never voice, a physical channel, `entity_info` device data,
or multiple targets. A fallback SHALL NOT create another fallback.

Eligible outcomes SHALL be `provider_unavailable`, `invalid_initiation`,
`no_binding`, `quiet`, `no_presence`, `safe_retry`, `failed`, and `ambiguous`.
`invalid_origin` SHALL NOT create fallback. A resolver miss SHALL return
`fallback_unavailable` and terminate. The fallback SHALL contain no attachment,
audio, reaction, or draft. Pre-start fallback MAY carry the original message
through the normal text contract. `failed` or `ambiguous` fallback
SHALL use a fixed uncertainty notice and SHALL NOT repeat the original message
content. Ordinary non-voice quiet-hours deferral MAY apply to the fallback and
SHALL NOT defer or retry voice.

ID: REQ-messenger-voice-egress-009
Source: bu-7exe4.13 owner policies; RFC 0034 D8
Scope: owner-approval-required

#### Scenario: Unsafe voice produces one separately keyed text fallback

- **WHEN** an eligible pre-start voice attempt terminates and Switchboard resolves one text-only non-voice target
- **THEN** exactly one fallback delivery SHALL be associated with the separate fallback key
- **AND** replay of the fallback intent SHALL return that same linked outcome

#### Scenario: Ambiguous fallback does not repeat sensitive content

- **WHEN** a voice attempt is `failed` or `ambiguous` and fallback is available
- **THEN** the fallback SHALL contain only the fixed uncertainty notice
- **AND** it SHALL NOT repeat the original message text or synthesize another voice attempt

#### Scenario: Invalid origin gets no fallback

- **WHEN** voice terminates `invalid_origin`
- **THEN** Messenger and Switchboard SHALL create no fallback because no authenticated delivery lineage exists

#### Scenario: Resolver miss ends without cascade

- **WHEN** Switchboard cannot resolve one eligible Telegram or email target
- **THEN** it SHALL return `fallback_unavailable`
- **AND** neither Messenger nor the fallback path SHALL try another channel or fallback

### Requirement: Voice persistence and observability are content-blind with explicit retention

Generated audio, PCM/TTS bytes, message text, provider request/response bodies,
raw presence, attestation payloads, native endpoint/device/room ids,
credentials, and provider error bodies SHALL remain memory-only and SHALL NOT
be written by the voice path to a database, blob store, audit event, log,
metric, or trace.

The voice receipt SHALL contain only keyed logical/endpoint digests, selected
binding version for each generation, categorical states/reasons, provider
profile version, timestamps,
claim generation, and separately keyed fallback outcome. Detailed transition
rows SHALL expire after 30 days. A minimal replay tombstone containing logical
digest, terminal replay class, and selected binding version SHALL have no
automatic expiry so cleanup cannot erase at-most-once truth. Destructive owner-authorized
tombstone removal SHALL first disable voice globally and warn that the replay
guarantee is being removed.

A control-capability replay receipt SHALL contain only issuer, audience,
action, `kid`, nonce digest, expiry, and consumed-at time and SHALL expire after
the capability's expiry-plus-skew replay window. It SHALL NOT contain the raw
nonce, signature, payload, payload digest, message digest, or protected body.

Metrics SHALL use only bounded state, reason, provider profile class, presence
freshness class, and fallback outcome labels. IDs, keyed digests, nonces,
rooms, endpoints, content, timestamps, provider text, and exception text SHALL
NOT be metric attributes. Logs and traces MAY use the existing telemetry trace
context but SHALL NOT include the logical key or physical identifiers.

ID: REQ-messenger-voice-egress-010
Source: bu-7exe4.13 owner policies; RFC 0034 D9; RFC 0005
Scope: owner-approval-required

#### Scenario: Sensitive transient material is discarded

- **WHEN** synthesis, presence attestation, or provider handoff completes or fails
- **THEN** audio, text copies, provider bodies, raw presence, native targets, and credentials SHALL be discarded without persistence or telemetry capture

#### Scenario: Detailed evidence expires but replay truth remains

- **WHEN** a detailed transition row reaches 30 days
- **THEN** cleanup MAY remove that row
- **AND** the minimal replay tombstone SHALL remain able to fence duplicate speech

#### Scenario: Telemetry is bounded and content-blind

- **WHEN** any voice state, failure, fallback, or latency signal is observed
- **THEN** telemetry SHALL expose only the bounded categorical vocabulary
- **AND** no identifier, digest, nonce, content, raw timestamp, provider body, or exception text SHALL become a metric attribute or structured-log field

#### Scenario: Tombstone deletion is an explicit safety loss

- **WHEN** the owner invokes an authorized destructive tombstone-removal workflow
- **THEN** the system SHALL disable new voice claims before deletion
- **AND** it SHALL present and record a content-blind warning that old logical deliveries can no longer be replay-fenced

### Requirement: Voice rollout is additive, version-gated, and disable-first reversible

Existing Telegram, email, and WhatsApp behavior and omitted-channel resolution
SHALL remain unchanged. Switchboard SHALL dispatch `voice` only after Messenger
advertises the exact voice contract version and an admissible provider profile;
otherwise the result SHALL be `provider_unavailable`. An older Messenger SHALL
reject unknown `voice` before side effect. Voice disable/rollback SHALL stop new
claims first, allow in-flight claims to settle or mark unprovable handoffs
`ambiguous`, and retain endpoint versions, receipts, replay tombstones, and
fallback links. Rollback SHALL NOT drop non-empty evidence tables, enable Live
Listener egress, restore generic delivery tracking, or change existing channel
behavior.

This specification and its owner approval SHALL NOT authorize live endpoint or
presence reads, provider/credential calls, audio output, device mutation,
deployment, or audible canary. Each remains a separate explicit action.

ID: REQ-messenger-voice-egress-011
Source: bu-7exe4.13 owner policies; RFC 0034 D10
Scope: owner-approval-required

#### Scenario: Existing clients remain unchanged

- **WHEN** a caller uses Telegram, email, WhatsApp routed delivery, or omits channel
- **THEN** selection, quiet-hours, delivery, and response behavior SHALL remain as before this capability

#### Scenario: Mixed-version rollout fails closed

- **WHEN** Messenger does not advertise the exact contract version or no provider profile is admissible
- **THEN** Switchboard SHALL NOT dispatch provider-capable voice work
- **AND** the voice result SHALL be `provider_unavailable` before physical side effect

#### Scenario: Disable-first rollback preserves truth

- **WHEN** operators roll back or disable voice
- **THEN** new claims SHALL stop before implementation removal
- **AND** existing receipts, tombstones, bindings, and fallback links SHALL remain readable for reconciliation
- **AND** an unprovable in-flight handoff SHALL settle `ambiguous`, not be retried

#### Scenario: Approval does not authorize live action

- **WHEN** the owner approves this exact contract artifact
- **THEN** that approval SHALL authorize only the contract for subsequent implementation work
- **AND** no endpoint/presence/provider/credential/audio/deployment/canary action SHALL occur without its separately recorded authority
