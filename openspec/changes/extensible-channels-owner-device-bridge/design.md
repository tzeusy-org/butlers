## Context

See `proposal.md` for motivation and RFC 0033 for the proposed cross-system contract. Current
runtime behavior has four relevant constraints:

- `IngestSourceV1` accepts a closed `SourceChannel`/`SourceProvider` vocabulary and validates an
  in-code pair matrix before Switchboard can consult durable authority.
- RFC 0013 and `passive-interaction-sync` assign arbitrary hours to three channels, even though the
  fact writer already has a partial source-endpoint-aware dedupe path.
- the shipped Discord connector is bot-token Gateway ingestion; the OAuth user-context design is
  unresolved and outside this change;
- `core-notify` validates `sms` at the type layer but rejects it at delivery because only Telegram
  and email adapters exist.

The change is large because it crosses a database authority boundary, the ingress wire, connector
lifecycle, relationship fact identity, owner-facing setup, credentials, and future provider egress.
It remains specification-only and unapproved.

## Goals / Non-Goals

**Goals:**

- let migrations add bounded inbound source pairs without editing the ingest model;
- keep catalog, connector activation, relationship scoring, and outbound delivery as four separate
  authorities;
- preserve all current pairs and stored event meanings through rollout and rollback;
- remove interaction idempotency's finite hour-slot allocation and score shipped Discord evidence;
- provide a reusable Watched Source lifecycle contract;
- give the owner a concrete, evidence-backed calls/SMS provider and privacy decision; and
- specify future proof seams across migration, grants, validation, API, UI, audit, adapter,
  concurrency, failure, and partial-effect boundaries.

**Non-Goals:**

- no implementation, migration execution, provider account, credential, live webhook, configuration,
  deployment, activation, message send, backfill, or historical rewrite;
- no catalog mutation API, runtime self-registration, connector orchestration, or outbound adapter
  capability metadata;
- no Discord OAuth v2, new scope, user token, consent flow, or direct-message expansion;
- no call recording, audio capture, transcription, media streaming, call control, clinical/FHIR,
  web-action, or shipment connector; and
- no SMS deliverability or approval/escalation policy adoption.

## Decisions

### 1. Catalog pair identity, not connector capability

Use one exact-pair table in `public`, owned by the migration role. The pair is the authority unit,
so one channel can admit multiple providers without a second enablement state that can disagree.
Switchboard alone receives runtime `SELECT`; the dashboard API reads its bounded projection through
Switchboard, and every other runtime role has neither direct read nor write privilege.

Rejected alternatives:

- **A JSON blob in config:** preserves a closed deployment-time world and has no grant boundary or
  migration parity proof.
- **Separate channel and provider tables:** add an independent channel enablement state with no
  authority the pair itself cannot express.
- **Connector heartbeat self-registration:** lets untrusted/runtime presentation claim ingress
  authority and confuses liveness with protocol admission.
- **One wide capability registry:** fields such as `deliverable` or `supports_interactions` would
  silently widen authority when a row changes. Those decisions stay at their owning boundaries.

### 2. Syntactic wire validation followed by semantic Switchboard validation

The wire accepts only lowercase ASCII tokens up to 64 characters. Switchboard validates the exact
enabled pair before dedupe and persistence. This preserves cheap bounded parsing while moving the
open set to durable authority.

The cache is a complete immutable snapshot. Switchboard begins the next refresh no later than 60
seconds after the preceding complete load, and a snapshot is usable only while its age is strictly
less than 60 seconds. A fresh last-known-good snapshot preserves already-known traffic during a
brief catalog read failure; it never admits an unseen pair. At expiry, all ingest stops with a
retryable availability error. A committed disablement therefore stops acceptance on the next
successful refresh or expiry, whichever comes first. This is deliberately stricter than triage's
fail-open cache: source identity is an admission boundary, while triage only chooses what happens
after admission.

### 3. Representation, propagation, enforcement

RFC 0033 `LEGACY_SOURCE_PAIRS_V1`, both runtime Literal projections, the static pair matrix, and the
catalog seed must be equal before any runtime type is relaxed. The comparison rejects missing,
extra, or substituted tuples even at equal cardinality. Consumer
propagation includes ingest construction, Pydantic validation, connector conformance, direct
dashboard ingress, filtering, identity resolution, relationship sync, and read projections. Only
then does semantic catalog enforcement become authoritative.

Rollback returns the binary to the exact legacy set and leaves catalog data and accepted history in
place. Catalog-only connectors are disabled first. A destructive table downgrade is unnecessary and
unsafe while catalog-only rows exist.

### 4. Source-aware fact key and real timestamps

Use `(entity_id, source_channel, interaction_date, direction, source_endpoint_identity)` as the
daily message interaction key. This preserves RFC 0013's daily aggregation and two directions while
removing source identity from `valid_at`. Endpoint identity comes only from accepted Switchboard
context. `interaction_date` is the UTC calendar date of the source event.

The deterministic real timestamp for a grouped interaction should be the earliest eligible event
time in the group. That choice remains stable under replay and communicates when observed activity
began. The database writer must linearize duplicates, so concurrent runs do not depend on a
check-then-insert race.

Discord uses the existing `has-handle` value `discord:<user_id>`. The explicit relationship source
map adds Discord; the general ingest catalog does not imply scoring eligibility. Because the
shipped event does not provide a trustworthy guild population, eligibility is limited to context
the bot-token connector can prove is Discord channel type `DM` with no `guild_id`. Guild, group,
other, and unknown contexts are explicitly ineligible. This prevents the current observed-sender
fallback from giving a quiet large channel undiluted DM weight without adding OAuth or member-list
authority.

### 5. Watched Source composes connector-base

Watched Source is a profile for connectors that monitor one provider feed. It inherits every
connector-base obligation. A provider adapter may poll or receive authenticated webhooks, but it
cannot classify, route, bypass source filters, advance past unknown acceptance, or infer activation
from a catalog row.

Checkpoint movement is per event/source and follows durable acceptance or durable filtered-event
accounting. Independent endpoints back off independently. Revocation stops future observation at a
linearized boundary while retained canonical history follows existing retention contracts.

Connector runtimes receive no catalog grant. They call Switchboard's bounded
`source.pair.preflight` MCP tool at the exact configured Switchboard origin before provider
connection. Switchboard authenticates the connector-base bearer token first and derives a
server-held principal binding connector type, allowed source pairs, allowed configured endpoint
identities, Switchboard audience, and active/revoked state. It rejects missing, invalid, expired,
revoked, cross-connector, out-of-scope, endpoint-mismatched, redirected, or wrong-audience requests
before catalog lookup. A matching response carries only an opaque principal-bound reference and
expiry that cannot outlive the snapshot's 60-second acceptance window. Denied, unavailable,
failed, or expired preflight keeps the connector inactive. Preflight does not reserve authority;
Switchboard still validates every envelope, and an active connector stops observation if renewal
fails.

Watched Source therefore requires its trusted connector endpoint identity to be provisioned into
the bearer principal before provider connection. If it is absent, the connector remains inactive.
No provider identity read, endpoint discovery call, URL rotation, or caller placeholder is an
authorized bootstrap path in this change.

This change chooses synchronous durable Switchboard acceptance for webhook acknowledgment rather
than adding a connector-owned durable ingress queue. A webhook handler may buffer at most 1 MiB of
exact request bytes in memory to perform provider signature validation, then normalizes the event
and calls Switchboard. It returns provider-success 2xx only for `accepted` or `duplicate`; rejection,
unavailability, timeout, or unknown durability returns non-2xx and the provider retry reuses the
same event identity. The verification buffer is released and never becomes persistence, logging,
telemetry, tracing, or LLM input.

### 6. Telnyx is the only conditional V1 candidate

RFC 0033 records the official primary-source research and exact limitations. Telnyx is the only V1
candidate because its current docs explicitly describe signed webhook event IDs, duplicate and
out-of-order delivery, and a voice command dedupe primitive. It is not selected: eligibility,
number capability, regional registration, ingress topology, and account-specific restrictions
cannot be derived from public docs and require provider evidence plus exact owner approval.

Twilio is retained only as future research. Its incoming-message webhook has a stable MessageSid but
no signed timestamp, and its default connection override policy does not retry 4xx, 5xx, or read
timeout outcomes. Keeping it eligible would require new authenticated provider reads or webhook
configuration authority that this change does not grant. A separate approved provider-specific
freshness/replay and retry contract is required before Twilio can become a candidate.

### 7. Proposed privacy default is metadata-only

The recommended privacy profile is call lifecycle metadata only, with no audio-bearing feature,
plus metadata-only inbound SMS. Exact request bytes may exist briefly in the 1 MiB verification
buffer, but the body never enters canonical ingest or an LLM. Credentials and verification
material are Tier 2 secured owner identity data.

Content-enabled SMS is a separate owner option. The proposed 30-day period applies to direct source
copies in connector filter/dead-letter payloads, Switchboard raw/normalized messages, route inbox
envelopes, and verbatim session prompt/transcript fields. Expiry redacts those content fields while
preserving lineage. It does not promise deletion of `public.ingestion_events` metadata, LLM output,
tool calls, facts, memories, episodes, summaries, embeddings, earlier owner exports, or still-live
managed backups. New exports see redacted state; restored managed backups run the sweep before
normal reads. If the owner rejects any derived/backup survival, content-enabled activation needs a
separate cross-system lineage-cascade and backup-erasure contract.

The body-free canonical metadata that can survive includes the remote E.164 party identity. The
owner must accept that identity-retention boundary explicitly; the direct-copy TTL is not a phone-
metadata deletion promise.

This is a proposal for exact owner review. The implementation packet cannot treat the recommendation
as approval. A different provider, SMS profile, retention period, or ingress route changes the
observable contract and must amend the spec before implementation.

### 8. Owner surface is informative before it is actionable

Before approval, the setup route presents `not_approved`, the proposed choices, and no effect
control. After a future approved implementation, the surface presents provider, enabled inbound
event kinds, privacy/retention, ingress exposure, status, and safe recovery actions. It never
returns secrets or communication content.

Activation is idempotent, reports pending state immediately, remains keyboard-operable, and keeps
the prior inactive state on failure. Revocation uses server-derived owner attribution and records a
content-blind audit event. These requirements apply the Dispatch design language and `/th-design`
walkthrough to entry, first glance, pace, repetition, recovery, and accessibility.

### 9. SMS egress stays out of this artifact

The current unsupported response is a compatibility and safety guarantee. A later change must
modify `core-notify` only after it defines the real adapter, credentials, recipient resolution,
approval interception, defense-in-depth Messenger check, provider handoff evidence, ambiguity,
partial effects, and rollback.

RFC 0023 is the intended recovery-policy dependency if accepted, but its proposed state grants no
authority today. Provider ambiguity is still treated conservatively: once a send may have started,
absence of proof is not permission to retry.

## Risks / Trade-offs

- **[Catalog availability becomes ingress-critical]** → Bound a last-known-good snapshot to less
  than 60 seconds, expose degraded availability, and reject before persistence at expiry.
- **[Cached disablement can take up to 60 seconds]** → Treat emergency disable as connector
  shutdown plus catalog disable; the cache interval is the maximum semantic propagation delay.
- **[Two rollout authorities can drift]** → Require exact parity before enforcement and delete the
  static semantic path only after rollback compatibility is intentionally retired in a later change.
- **[An additive requirement can conflict with old hour-offset wording]** → Give the new rule an
  explicit enforcement boundary: old wording governs before cutover, source-aware identity governs
  after it. Reconcile the baseline during archive so both stages remain legible.
- **[Discord messages may lack a relationship handle]** → Preserve unresolved-sender/degraded
  behavior; do not fabricate identity or broaden OAuth access.
- **[A large Discord context can look like one sender]** → Score only authenticated direct-message
  context; mark every guild, group, or unknown context ineligible before downstream fallback.
- **[Webhook retries and ordering can duplicate/regress lifecycle]** → Deduplicate stable event IDs
  and apply monotonic lifecycle rules under a database transaction.
- **[A provider may require acknowledgment sooner than Switchboard can durably accept]** → Keep the
  bridge inactive unless the selected provider's retry/timeout contract supports the synchronous
  boundary; a future durable queue is a separately specified storage boundary, not an implicit
  fallback.
- **[Public ingress increases attack surface]** → Require TLS, provider signature/freshness
  validation, expected account/number binding, bounded bodies, rate limits, and no acceptance before
  authentication. The concrete exposure route remains an owner decision.
- **[A caller can forge preflight scope or target an alternate endpoint]** → Authenticate the
  connector bearer before catalog lookup, compare every request dimension with server-held scope,
  pin the Switchboard audience/origin, and deny direct catalog access to every other runtime role.
- **[Provider API behavior can drift]** → Refresh official documentation and account capability at
  the owner gate and again before implementation; fail closed when the expected primitive is absent.
- **[Twilio does not meet the generic freshness/retry contract]** → Exclude it from V1 eligibility;
  retain only research links and require a separate exact contract before reconsideration.
- **[Status projection leaks sensitive data]** → Build field-by-field content-blind DTOs and audit
  metadata; test forbidden field/value classes across API and UI.
- **[A short source TTL can be mistaken for complete erasure]** → Name every direct store, redact
  lineage-linked content there, disclose derived/export/backup survival, and default to metadata-only
  unless the owner accepts those boundaries.
- **[SMS send result is ambiguous]** → No adapter in this change; a later adapter must reconcile the
  same immutable attempt or stop without resend.
- **[Foreign drafts move governing text]** → Recheck PR #4046 and #3960 immediately before semantic
  review/merge and rebuild any colliding delta against the landed baseline.

## Migration Plan

This plan describes future implementation order; no step runs in this change.

1. Add catalog representation, constraints, an exact `LEGACY_SOURCE_PAIRS_V1` seed, and
   Switchboard-only runtime `SELECT` grants.
   Keep the static validator authoritative.
2. Add the loader/cache, static-vs-catalog parity check, content-blind read API/UI, and update every
   consumer to accept the bounded token type internally while enforcing the static set.
3. Add source-aware interaction uniqueness and writers, migrate Discord into the explicit
   relationship source map, and verify all fact writers/readers before removing hour markers.
4. Cut semantic ingest enforcement to the catalog only after parity and consumer tests pass. Prove
   a catalog-only source pair needs no validator edit.
5. Implement Watched Source conformance independently from the owner-device provider.
6. Stop at the owner-device decision gate. After exact approval and provider confirmation,
   implement authenticated inbound calls/SMS with the approved privacy profile. Outbound SMS stays
   unavailable.

Rollback disables catalog-only connector configuration, restores static semantic validation, and
keeps additive schema/history. It does not downgrade while active code or configuration depends on
catalog-only pairs. Interaction rollback returns all writers to the compatibility version before
retiring source-aware constraints; no accepted fact is time-shifted or rewritten.

## Owner Decision Gate

The single authoritative checklist is RFC 0033 D7's eight-item owner gate. Task 1.2 must carry all
eight subjects exactly; summaries and UI copy reference that list instead of creating another.
Each subject is non-deferrable because it changes provider, privacy, authentication, or retention
authority.

Until an independently reviewed exact artifact records all eight and receives owner sign-off, the
bridge remains `not_approved` and `bu-8cdl1.14` is not ready for the owner-device implementation
slice.
