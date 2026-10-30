## Why

Passive Relationship sync writes channel-specific hours rather than observed instants.
The accepted RFC 0013 D4 still requires those hours as dedup markers. Current code
already separates direction and attested endpoint, but still collapses different
chats through a contact/channel/day guard and key. Changing only the timestamp
would neither preserve replay nor establish source identity.

Owner choice C for `bu-8cdl1.14` authorizes this small specification draft. It does
not adopt a new writer. The former connector catalog and Watched Source proposal
remains withdrawn.

## What Changes

- Keep current chat/day aggregation, owner-presence direction, group rules,
  supported channels and canonical entity resolution. Name endpoint, chat and
  direction in one deterministic, versioned logical key.
- Use an actual persisted inbox `received_at` anchor as `occurred_at`, explicitly
  labelled receipt time. This is not a promise of original sender-clock time;
  batch envelopes do not expose individual message instants uniformly today.
- Anchor incoming facts in the contact's first contributing row and outgoing
  facts in the first owner row, ordering timestamp ties by inbox UUID. Preserve
  the first committed binding on retry; later rows do not add scoring facts to
  the same unit or silently retime it.
- Use the existing explicit temporal `store_fact(idempotency_key=...)` path.
  The job must bypass its old date-only interaction guard only for the trusted,
  versioned sync path. Other callers retain their current contracts. Full key
  bindings and database uniqueness must prevent race/collision false success.
- Keep old offset history untouched. Require a durable UTC-day cutover boundary,
  refusal of ambiguous overlap, and a reviewable rollback that cannot restart
  offset writes over new units. No historical timestamp reconstruction is claimed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `passive-interaction-sync`: replace synthetic-hour identity with source-aware
  receipt-time identity. All sixteen existing message scenario headings remain;
  the historical hour-offset heading is retained with an explicit replacement.
  Calendar, schedule, return statistics and existing resolver/failure scenarios
  remain in force. Identity failures use the existing `errors` result.

## Proposed RFC 0013 D4 Amendment (Not Yet Adopted)

The D4 deduplication paragraph and offset table MUST be replaced on adoption with:

> Passive message sync identifies a unit by tenant, Relationship scope,
> canonical entity, source channel, attested endpoint, canonical chat identity,
> UTC receipt day and direction. It writes at most one incoming and one outgoing
> fact per such chat/day unit. Actual persisted inbox receipt time supplies the
> first committed anchor; hour offsets are not identities. An explicit versioned
> temporal-fact key supplies idempotency and database-enforced concurrency;
> retries return the existing binding. Legacy synthetic-hour facts remain
> historical records and are not retimed or duplicated across cutover.

The D4 grouping algorithm must retain the accepted external-conversation/thread/
sender fallback, and explicitly include endpoint and UTC receipt date. The
proposed cap is per **chat**, rather than the old global contact/channel/day cap.
Two genuinely distinct chats can therefore contribute separately where today's
coarse key loses one; this scoring-cardinality consequence is part of the owner
adoption decision. It is not per-message scoring. D1/D2/D3 and D5/D6/D7, the
calendar contract and the three existing supported channels are unchanged.

The accepted RFC file is deliberately not edited by this draft. Adoption must
update its D4 text together with the owning capability; accepting only one would
leave contradictory normative rules. No other RFC exception is proposed.

## Current Source and Future Implementation Boundary

- `roster/relationship/jobs/relationship_jobs.py`: groups existing inbox rows by
  chat/channel/endpoint/UTC receipt date but synthesizes 0/1/2 and 12/13/14 hours.
  Its query must eventually retain actual anchors without inflating message_count
  when batch senders fan out.
- `roster/relationship/tools/interactions.py`: explicit timestamps currently use
  date/direction/endpoint dedup. Its trusted sync branch needs a source-aware key
  and binding verification; manual calls and server-only attestation stay intact.
- `src/butlers/modules/memory/storage.py`: already accepts explicit temporal keys;
  its automatic key also includes object entity and source episode. This proposal
  does not change that generic key or claim that current key hits validate the
  full payload. Atomic uniqueness/collision behavior requires implementation proof.
- `src/butlers/modules/pipeline.py`: persists an inbox receipt instant and source
  context. No connector clock, provider fetch, new access right or ingress change
  is required or authorized here.

Source message IDs are evidence when actually persisted; they are not invented
from content or substituted for canonical chat identity. Batch receipt evidence
remains batch-level. The existing narrative memory `facts` store is distinct from
structural `relationship.entity_facts`; the separate effective-time proposal for
that structural store is not adopted or extended here.

## Impact and Non-Goals

Specification only: three new OpenSpec files. No runtime, test, migration,
revision reservation, data rewrite, archive or baseline-spec edit. No connector
catalog, Watched Source UI, Discord, owner-device bridge, SMS, provider/account,
credential, ingress/authentication or outbound-send work. No general interaction
registry, per-message scoring, or change to Dunbar weights/eligibility.

## Acceptance and Review

1. One small Relationship change must pass named strict OpenSpec validation.
2. The delta specifies actual receipt timestamps, source-aware keys and replay;
   its scenarios include two channels, both directions, concurrent replay,
   day-boundary skew, collision and old-row coexistence.
3. The exclusions above remain binding.
4. Independent review must name the exact draft head. Owner adoption must name
   that reviewed head before merge; GitHub actions under the owner's account,
   successful CI or silence are not adoption.
5. Code, migration and test implementation starts only after that adoption.

The draft remains pending review and owner adoption. Validation is source/spec
verification only, not an executing dedup, migration or scoring-survivor claim.
