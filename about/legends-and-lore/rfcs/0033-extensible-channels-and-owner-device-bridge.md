# RFC 0033: Extensible Channels and the Owner-Device Bridge

**Status:** Proposed — exact owner sign-off required before implementation
**Date:** 2026-09-07
**Authority:** Draft prerequisite `bu-atsax` for `bu-8cdl1.14`; the released pursuit authorizes
this draft, not adoption or effects

## Summary

Butlers will replace its static inbound source-pair allowlist with a migration-owned catalog while
keeping outbound delivery under a separate Messenger adapter and approval authority. The change
also replaces relationship interaction hour slots with a source-aware stable key, admits only the
already-shipped Discord bot-token source to passive scoring, and defines Watched Source as a
connector profile.

The owner-device calls/SMS bridge remains reserved and inactive. This RFC presents researched
Twilio and Telnyx options plus a proposed privacy/authentication contract, but it selects neither
provider and does not authorize ingress exposure, credential setup, activation, or SMS delivery.

## Existing Contracts and Precedence

- RFC 0003 currently enumerates valid `ingest.v1` channel/provider pairs and the runtime enforces
  the same closed `Literal` and static pair matrix. After the enforcement cutover defined here,
  the catalog becomes semantic authority while the `ingest.v1` field meanings remain unchanged.
- RFC 0013 D4 assigns arbitrary per-channel hours so daily incoming/outgoing facts do not collide.
  After source-aware persistence is deployed to every writer, the stable source tuple defined here
  supersedes those hour markers and `valid_at` returns to real event time.
- `connector-base-spec` remains the complete connector lifecycle. Watched Source adds no bypass.
- `core-notify` currently delivers Telegram and email and rejects SMS. This RFC preserves that
  behavior; outbound SMS needs a later approved delta.
- RFC 0023 is proposed, not accepted. Its durable presentation and ambiguity model remains the
  intended composition point for any later SMS approval delivery; this RFC neither accepts nor
  duplicates it.
- `connector-discord` distinguishes the shipped bot-token Gateway connector from unresolved OAuth
  user-context v2. Only the shipped source is eligible here.

If a clause here is accepted, it governs only the staged state named by that clause. Before the
named cutover, the existing RFC 0003 and RFC 0013 mechanisms remain authoritative.

## D1: Inbound Catalog and Outbound Adapter Are Separate Authorities

The inbound catalog answers exactly one question: may Switchboard accept this canonical
`source.channel`/`source.provider` pair? It does not answer whether a connector is configured,
whether an identity can be contacted, or whether Messenger can deliver on the channel.

Outbound authority remains the intersection of:

1. a registered and usable Messenger adapter;
2. a resolved recipient or owner default;
3. the applicable approval and defense-in-depth gates;
4. provider handoff/idempotency evidence; and
5. an enabled delivery policy.

No catalog column will mirror or predict those facts. In particular, the catalog contains no
`deliverable`, `approval_required`, `supports_interactions`, credential, scope, health, or adapter
capability flag. A future relationship scoring source remains explicitly declared by the
relationship contract rather than inferred from catalog metadata.

## D2: Catalog Representation and Grants

The proposed representation is one additive `public` table whose authority unit is the exact pair:

```sql
public.source_channel_catalog (
    channel       text NOT NULL,
    provider      text NOT NULL,
    enabled       boolean NOT NULL,
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    PRIMARY KEY (channel, provider),
    CHECK (channel ~ '^[a-z][a-z0-9_]{0,63}$'),
    CHECK (provider ~ '^[a-z][a-z0-9_]{0,63}$')
)
```

Registration is migration-only for this move: the migration owner writes; Switchboard and the
dashboard API receive read access; connector, butler, and Messenger runtime roles receive no direct
access; and no runtime role receives a write grant. A later audited owner mutation API would be a
new trust boundary and needs its own approved change.

The seed is the exact 20-pair set currently enforced in
`roster/switchboard/tools/routing/contracts.py`:

```text
telegram_bot/telegram             telegram_user_client/telegram
slack/slack                       email/gmail
email/imap                        api/internal
mcp/internal                      voice/live-listener
whatsapp_user_client/whatsapp     google_calendar/google_calendar
spotify_user_client/spotify       owntracks/owntracks
dashboard/internal                home_assistant/home_assistant
gaming/steam                      google_drive/google_drive
discord/discord                   wellness/google_health
wellness/home_assistant           activitywatch/activitywatch
```

The catalog read API exposes only `channel`, `provider`, and `enabled`, plus an envelope-level
`source_available`. It offers no mutation and must not be combined with connector instance,
credential, health, or outbound adapter data. This avoids overlapping the connector presentation
work described under D9.

## D3: Two-Stage Validation and Bounded Cache

Pydantic/wire validation accepts only the bounded token syntax. Switchboard then validates the
exact enabled pair against one full catalog snapshot before deduplication or persistence.

The cache contract is explicit:

- refresh interval: 60 seconds;
- maximum last-known-good age: 300 seconds from the last complete successful load;
- atomic whole-snapshot replacement;
- a failed or partial refresh never changes the snapshot or its success time;
- a fresh cached snapshot may continue admitting pairs it already contains;
- a pair absent from the cached snapshot is never admitted during failure; and
- after 300 seconds, every ingest is rejected before persistence as retryable
  `source_catalog_unavailable` until a full load succeeds.

Unknown, disabled, and mismatched pairs fail closed. Safe errors expose only canonical tokens and
one of `invalid_source_syntax`, `unknown_source_pair`, `disabled_source_pair`, or
`source_catalog_unavailable`; they never echo envelope or provider content.

## D4: Additive Rollout and Rollback

Rollout has three serialized stages:

1. **Representation.** Add both tables, constraints, complete seed, and read-only grants. The
   static validator remains sole authority.
2. **Propagation.** Add the catalog loader, exact static-vs-catalog parity guard, content-blind read
   API, and update every validator/conformance consumer. Both authorities must agree; disagreement
   blocks the next stage.
3. **Enforcement.** After parity and consumer coverage pass, relax the closed wire literals to the
   bounded token type and make the catalog the semantic authority. Only then may a migration add a
   pair without editing validator code.

Binary rollback first disables any catalog-only connector configuration and returns to the static
seeded set. The catalog tables and accepted rows remain. Rollback never rewrites stored source
identity, replays provider payloads, or maps a catalog-only event onto a legacy pair. Schema
downgrade is forbidden while a runtime depends on catalog-only pairs; the safe rollback is an
application rollback with additive data retained.

## D5: Source-Aware Relationship Interaction Identity

The post-cutover stable interaction identity is:

```text
(entity_id, source_channel, interaction_date, direction, source_endpoint_identity)
```

`source_endpoint_identity` must be the Switchboard-attested value persisted from the accepted
envelope. It cannot come from caller-supplied fact metadata or catalog capability metadata.
Concurrent/replayed syncs use one database-enforced identity and return the existing fact to losing
writers. Distinct channels or endpoints remain distinct.

`interaction_date` is the UTC calendar date of the source event. `valid_at` becomes a deterministic
real timestamp from the grouped source events. It no longer
encodes channel or direction. This removes the finite hour-slot capacity without changing RFC
0013's direction weights, group-size dilution, participant gate, or one-incoming/one-outgoing daily
shape.

Discord adds this resolver entry only:

```text
source_channel = discord
lookup          = has-handle "discord:<provider_user_id>"
```

The provider message ID and connector endpoint retain source idempotency. Discord's official API
reference identifies Discord object IDs as Snowflakes and documents bot-token and OAuth bearer
authentication as separate mechanisms ([Discord API Reference](https://docs.discord.com/developers/reference));
the OAuth and bot permission model is documented separately
([Discord OAuth2 and Permissions](https://docs.discord.com/developers/platform/oauth2-and-permissions)).
This change keeps the existing bot-token path and requests no OAuth scope.

## D6: Watched Source Profile

Watched Source is a reusable connector profile, not a new routing engine. Each implementation must
meet the full connector-base obligations: source filtering and filtered-event flush, replay drain,
checkpoint/resume, first baseline for delta sources, heartbeat, metrics, rate limits/backoff,
per-source isolation, and graceful shutdown.

A catalog pair is necessary for activation but never sufficient. Configuration enables a specific
instance. Poll sources persist a checkpoint only after durable acceptance or accounted filtering.
Webhook sources authenticate signature, freshness, expected account, and destination before
acknowledging acceptance. A Switchboard timeout retries the same event identity; provider events
remain at-least-once and Switchboard remains the deduplication boundary.

## D7: Owner-Device Provider Decision

The first bridge is intentionally provider-neutral until the owner chooses. Research performed on
2026-09-07 supports two credible options:

| Option | Verified capabilities | Material constraint |
|---|---|---|
| **A — Telnyx (recommended subject to eligibility)** | Messaging webhooks are Ed25519-signed, document stable event IDs for deduplication, retries, and out-of-order delivery ([Messaging webhooks](https://developers.telnyx.com/docs/messaging/messages/receiving-webhooks)). Voice webhooks carry unique event IDs, call-leg/session IDs, signatures, retry metadata, and recommend `command_id` for 60-second duplicate-command suppression ([Voice API webhooks](https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-webhooks)). API requests use bearer API keys ([Voice commands](https://developers.telnyx.com/docs/voice/programmable-voice/sending-commands)). | Regional number availability, account eligibility, registration, exact key scope, and public webhook ingress must be confirmed for the owner's account. The documented voice command window is not a general SMS-send idempotency guarantee. |
| **B — Twilio** | Incoming SMS uses configured webhooks and outgoing messages expose stable Message SIDs plus status callbacks ([Message resource](https://www.twilio.com/docs/messaging/api/message-resource), [incoming SMS webhook](https://www.twilio.com/docs/messaging/guides/webhook-request)). Calls expose Call SIDs and progress callbacks ([Call resource](https://www.twilio.com/docs/voice/api/call-resource)). Twilio signs webhook requests and recommends SDK validation ([Webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security)); restricted API keys can scope Messaging and Voice REST access ([Restricted API keys](https://www.twilio.com/docs/iam/api-keys/restricted-api-keys)). | Regional number availability, account eligibility, registration, public webhook ingress, and webhook Auth Token handling must be confirmed. The cited Message create contract does not document a general client idempotency parameter, so a timeout without a returned SID is ambiguous and cannot be blindly retried. |

The recommended owner decision is Option A if the provider confirms a suitable number and account
in the owner's region; otherwise Option B. Technical preference does not select or provision a
provider. Before implementation dispatch, an exact owner act must record:

1. provider and provisioned account/number identity;
2. verified number capabilities and regional/registration prerequisites;
3. public HTTPS ingress route and ownership;
4. provider authentication and webhook-verification material;
5. enabled inbound event types;
6. one privacy profile and finite retention period; and
7. revocation and credential-deletion expectations.

Provider documentation and account capability must be refreshed at that gate because these facts
can change. Without all seven, owner-device implementation remains blocked.

## D8: Proposed Privacy, Authentication, and Owner Experience

The proposed default for exact owner review is:

- call lifecycle metadata only (`initiated`, `ringing`, `answered`, terminal), with no audio,
  recording, transcription, media stream, or call-control effects;
- content-enabled inbound SMS so the message can enter the ordinary routing path, with a 30-day
  raw/provider-content retention ceiling; alternatively, the owner may choose metadata-only SMS,
  which omits the body and cannot support content routing;
- identity-bound provider credentials and verification material in secured Tier 2 owner
  `entity_info`;
- stable E.164 remote-party identity resolved through the existing `has-phone` contract;
- signature and freshness validation before acknowledgement, plus expected account/number binding;
- no raw message body, full phone number, credential, signature, provider error, recording URL, or
  callback body in logs, metrics, generic audit, catalog/status API, or setup UI; and
- revocation stops polling/webhook acceptance and removes runtime credential availability without
  rewriting accepted history.

The owner UI is a content-blind setup/status view. Before approval it shows `not_approved` and has
no effect action. After a separate implementation is authorized, it explains provider,
capabilities, privacy/retention, and ingress exposure before an idempotent activation. Pending work
acknowledges immediately; errors name a safe recovery step; controls are keyboard-operable with
visible focus. Status values are `not_approved`, `not_configured`, `connecting`, `active`,
`degraded`, and `revoked`.

These are proposals, not adopted policy. Changing the privacy profile or provider changes the
observable contract and requires exact owner review of the resulting artifact.

## D9: SMS Egress and Active Overlaps

SMS remains unsupported. A later activation change must provide a real Messenger adapter,
credential/reachability checks, recipient resolution, per-message approval interception,
defense-in-depth Messenger enforcement, immutable attempt identity, provider receipt or
reconciliation, partial-effect reporting, and rollback. After provider handoff may have started,
an unproven outcome is `ambiguous`; no generic retry, restart, or fresh key may duplicate it.

This draft deliberately avoids three foreign authorities:

- draft PR #4046 / `bu-poven` changes connector setup presentation inside
  `connector-runtime-instance-authority`; it does not authorize this catalog or UI;
- foreign draft PR #3960 / `bu-7exe4.2` changes RFC 0003 and the ingest envelope and is currently
  conflicting; if any version lands, this change must be rebuilt and revalidated against it before
  owner review or merge; and
- RFC 0023 and `bu-8cdl1.4` own durable approval presentation and multi-channel escalation policy.
  SMS may compose with an accepted version later but cannot pre-accept or bypass it here.

## Failure and Partial-Effect Matrix

| Boundary | Failure or race | Required result |
|---|---|---|
| Catalog load | No initial/fresh snapshot | Reject before persistence as retryable unavailable; never use an unseen pair. |
| Catalog refresh | Partial read or concurrent swap | Discard candidate; readers see one complete generation. |
| Pair validation | Unknown, disabled, mismatched | Reject before dedupe/persistence/classification/routing. |
| Registration | Runtime attempts catalog write | Database denial; no self-registration fallback. |
| Interaction sync | Concurrent/replayed group | One fact for the stable source tuple; losers observe existing. |
| Interaction sync | Resolution returns no contacts for non-empty input | Preserve existing degraded result; do not advance as an all-clear. |
| Watched Source | One endpoint fails | Independent sources continue; failed source backs off with safe status. |
| Watched Source | Switchboard result lost | Retain checkpoint; repeat the same ingest identity. |
| Webhook ingress | Invalid/stale/wrong-account signature | Reject before acknowledgement as accepted or canonical persistence. |
| Setup | Partial configuration or repeated activate | Remain inactive; at most one attempt; name missing safe categories. |
| Revocation | In-flight webhook/poll race | Linearize revocation; no event observed after the revocation boundary. |
| Future SMS | Timeout before provider start | Retry only when durable evidence proves no provider effect began. |
| Future SMS | Timeout after possible provider start | Mark ambiguous and reconcile; never blindly resend. |

## Verification Contract

Future implementation must execute behavior at the owning seams rather than assert text or symbol
presence:

- real-PostgreSQL migrations/grants and complete legacy seed;
- static/catalog parity, malformed/unknown/disabled/mismatched pairs, cache freshness, atomic
  refresh, and a catalog-only channel;
- registration/API/UI degraded and content-blind projections;
- Discord resolution and a measurable Dunbar input change;
- stable-key repeated and concurrent interaction sync with distinct channel/endpoint cases;
- Watched Source first baseline, restart resume, filtered-event flush, replay, checkpoint failure,
  per-source backoff, and authenticated webhook rejection;
- owner-device setup/revocation API and keyboard/focus/error UI behavior without secret/content
  projection;
- audit actor attribution and content-blind payloads; and
- absent SMS adapter, unapproved send, pre-handoff safe retry, post-handoff ambiguity, partial
  effects, rollout, application rollback, and no historical rewrite.

No live provider canary or exact test-count obligation is created by this RFC. Any later live probe,
credential operation, provider call, ingress exposure, deployment, or message send requires its own
explicit authority.

## Owner Decisions Required Before Readiness

The artifact is ready for independent semantic/security review once its local and hosted checks
pass. It is not owner-ready for implementation until the review returns GO and the owner approves
the exact reviewed digest, including:

1. provider Option A or B;
2. call-lifecycle event subset;
3. SMS metadata-only or content-enabled privacy profile;
4. finite retention period (proposed 30 days for provider/raw content);
5. ingress route and exposure; and
6. continued deferral of outbound SMS or a separately reviewed activation delta composed with the
   then-current RFC 0023 policy.
