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
Switchboard and the dashboard API can read; runtime callers cannot write.

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

The cache is a complete immutable snapshot refreshed every 60 seconds with a 300-second maximum
stale age. A fresh last-known-good snapshot preserves already-known traffic during a brief catalog
read failure; it never admits an unseen pair. After the bound, all ingest stops with a retryable
availability error. This is deliberately stricter than triage's fail-open cache: source identity is
an admission boundary, while triage only chooses what happens after admission.

### 3. Representation, propagation, enforcement

The static and catalog sets must match exactly before any runtime type is relaxed. Consumer
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
map adds Discord; the general ingest catalog does not imply scoring eligibility.

### 5. Watched Source composes connector-base

Watched Source is a profile for connectors that monitor one provider feed. It inherits every
connector-base obligation. A provider adapter may poll or receive authenticated webhooks, but it
cannot classify, route, bypass source filters, advance past unknown acceptance, or infer activation
from a catalog row.

Checkpoint movement is per event/source and follows durable acceptance or durable filtered-event
accounting. Independent endpoints back off independently. Revocation stops future observation at a
linearized boundary while retained canonical history follows existing retention contracts.

### 6. Provider choice is owner policy; technical recommendation is Telnyx first

RFC 0033 records the official primary-source research and exact limitations. Telnyx is the proposed
first choice because its current docs explicitly describe signed webhook event IDs, duplicate and
out-of-order delivery, and a voice command dedupe primitive. Twilio is the fallback because it has
signed callbacks, stable Message/Call SIDs, restricted keys, and mature messaging/voice resources,
but its cited Message create contract does not document a general create idempotency parameter.

Neither option is selected. Provider eligibility, number capability, regional registration,
ingress topology, and account-specific restrictions cannot be derived from public docs. The owner
must approve the provider and the provider must confirm those facts for the intended account before
implementation.

### 7. Proposed privacy default minimizes call content

The recommended privacy profile is call lifecycle metadata only, with no audio-bearing feature,
plus inbound SMS content through the protected ingest path with a 30-day provider/raw-content
ceiling. The owner may instead choose metadata-only SMS, accepting that message content cannot be
routed. Credentials and verification material are Tier 2 secured owner identity data.

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

- **[Catalog availability becomes ingress-critical]** → Bound a last-known-good snapshot to five
  minutes, expose degraded availability, and reject before persistence after expiry.
- **[Cached disablement can take up to 60 seconds]** → Treat emergency disable as connector
  shutdown plus catalog disable; the cache interval is the maximum semantic propagation delay.
- **[Two rollout authorities can drift]** → Require exact parity before enforcement and delete the
  static semantic path only after rollback compatibility is intentionally retired in a later change.
- **[An additive requirement can conflict with old hour-offset wording]** → Give the new rule an
  explicit enforcement boundary: old wording governs before cutover, source-aware identity governs
  after it. Reconcile the baseline during archive so both stages remain legible.
- **[Discord messages may lack a relationship handle]** → Preserve unresolved-sender/degraded
  behavior; do not fabricate identity or broaden OAuth access.
- **[Webhook retries and ordering can duplicate/regress lifecycle]** → Deduplicate stable event IDs
  and apply monotonic lifecycle rules under a database transaction.
- **[Public ingress increases attack surface]** → Require TLS, provider signature/freshness
  validation, expected account/number binding, bounded bodies, rate limits, and no acceptance before
  authentication. The concrete exposure route remains an owner decision.
- **[Provider API behavior can drift]** → Refresh official documentation and account capability at
  the owner gate and again before implementation; fail closed when the expected primitive is absent.
- **[Status projection leaks sensitive data]** → Build field-by-field content-blind DTOs and audit
  metadata; test forbidden field/value classes across API and UI.
- **[SMS send result is ambiguous]** → No adapter in this change; a later adapter must reconcile the
  same immutable attempt or stop without resend.
- **[Foreign drafts move governing text]** → Recheck PR #4046 and #3960 immediately before semantic
  review/merge and rebuild any colliding delta against the landed baseline.

## Migration Plan

This plan describes future implementation order; no step runs in this change.

1. Add catalog representation, constraints, complete 20-pair seed, and least-privilege grants.
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

These choices are non-deferrable because each changes the provider, privacy, or authentication
contract:

1. Telnyx (recommended if eligible) or Twilio;
2. exact provider account/number and confirmed regional capabilities;
3. owned public HTTPS ingress route;
4. enabled call lifecycle subset;
5. metadata-only or content-enabled inbound SMS;
6. finite provider/raw-content retention (recommended 30 days); and
7. continued outbound SMS deferral, unless a separate approved effect artifact is ready.

Until an independently reviewed exact artifact records all seven and receives owner sign-off, the
bridge remains `not_approved` and `bu-8cdl1.14` is not ready for the owner-device implementation
slice.
