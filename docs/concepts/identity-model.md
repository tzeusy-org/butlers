# Identity Model

> **Purpose:** Explain how the shared identity schema maps external sender identifiers to known entities and roles, enabling identity-aware routing and access control.
> **Audience:** Developers working on identity resolution, routing, approval gates, or contact management.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md), [Switchboard Routing](switchboard-routing.md).

## Overview

Butlers maintains a shared identity registry anchored on `public.entities`. Channel identifiers (Telegram chat IDs, email addresses, Discord handles) attach to an entity as `relationship.entity_facts` triples, and roles live on the entity itself. The identity model powers sender recognition during Switchboard ingestion, owner-aware routing, approval gate role checks, and outbound recipient resolution via `notify()`.

## Schema Structure

Identity resolution reads two tables: the entity anchor in the `public` schema and the channel-handle triples in the `relationship` schema.

### public.entities

The entity table is the anchor for identity. Each row represents a known person or actor. Key fields:

- **`id`** (UUID) --- primary key; the authoritative identity key
- **`canonical_name`** --- display name
- **`entity_type`** --- typically `"person"`
- **`roles`** (TEXT[]) --- role assignments (e.g., `['owner']`); the authoritative source of truth for identity roles
- **`aliases`** (TEXT[]) --- alternative names
- **`metadata`** (JSONB) --- extensible metadata; temporary entities carry `{"unidentified": true}`
- **`posture`** (`active` | `memorial` | `quiet` | `no_contact`, default `active`) --- how the owner wants this person treated, with `posture_since` and `posture_set_by`. Asserted only by the owner through the Relationship butler's `entity_set_posture` tool and never inferred; a trigger on the table refuses a change from any other butler role. It is independent of `listed` and deletes nothing. Birthday highlights, gift asks, reconnection insights and the calendar overlay read it (`active` only; a memorial birthday becomes a low-priority `remembrance` overlay entry), and `notify(entity_id=...)` refuses memorial and `no_contact` recipients (and an unreadable posture) with code `recipient_posture` before any approval or delivery. Posture values are personal data and are never logged.

### relationship.entity_facts (channel handles)

Channel identifiers are stored as fact triples keyed by entity. The relevant fields:

- **`subject`** (UUID, references `public.entities.id`) --- the owning entity
- **`predicate`** (TEXT) --- the channel kind: `has-handle` (Telegram and similar handles), `has-email`, or `has-phone`
- **`object`** (TEXT) --- the channel-specific identifier; Telegram handles are stored in canonical prefixed form `telegram:<id>`
- **`object_kind`** (TEXT) --- `'literal'` for channel-identifier values
- **`validity`** (TEXT) --- `'active'` for the current resolvable handle

## Identity Resolution

The core identity operation is `resolve_contact_by_channel()` in `src/butlers/identity.py`. Given a channel type and value, it maps the channel type to a predicate and queries the triple store, joining to the entity for the name and roles:

```sql
SELECT ef.subject              AS entity_id,
       e.canonical_name        AS name,
       COALESCE(e.roles, '{}') AS roles
FROM   relationship.entity_facts ef
JOIN   public.entities e ON e.id = ef.subject
WHERE  ef.predicate   = $1
  AND  ef.object      = $2
  AND  ef.object_kind = 'literal'
  AND  ef.validity    = 'active'
```

The result is a `ResolvedContact` dataclass carrying `entity_id` (the authoritative key), `name`, and `roles` (sourced from the entity).

The function is safe to call before migrations have run --- it catches all database exceptions and returns `None` gracefully.

Travel parties also reference this shared identity anchor. `travel.travellers.entity_id` points to
`public.entities.id` when an exact canonical person is already known; an unresolved booking name
remains a local party member with a stable traveller key and a null `entity_id` rather than minting
shared identity. If that exact name later resolves, Travel promotes the existing local party member
instead of creating a duplicate; caller-supplied IDs are accepted only for live, unmerged person
entities. When a linked source person has since been merged into a canonical survivor, the next
booking ingest repoints the trip-local traveller and deduplicates its leg participation against the
survivor. `travel.leg_passengers` records which party members occupy each shared leg.
Relational facts remain owned by the Relationship butler and are never copied into the travel schema.

## Owner Entity

The owner is the single person the system serves. Every daemon startup idempotently ensures the owner entity exists (`src/butlers/owner_bootstrap.py` `_ensure_owner_entity`). The owner entity carries the `"owner"` role, which is used for:

- **Identity preamble** --- Routed messages from the owner are prepended with `[Source: Owner (entity_id: ...), via <channel>]`.
- **Approval gates** --- Certain sensitive tool calls require owner authorization.
- **Routing priority** --- Owner messages may receive preferential queue ordering.

## Unknown Sender Handling

When identity resolution returns no match for a sender, the system creates a temporary entity via `create_temp_contact()`. This function:

1. Re-checks the triple store to avoid double-creation; if the channel identifier already resolves, it returns that entity instead of minting a duplicate.
2. Creates a `public.entities` row with `metadata.unidentified = true` and `entity_type = "person"`.
3. Returns a `ResolvedContact` for the new entity with empty roles.

The sender's channel triple is not written here. Asserting the `relationship.entity_facts` handle happens in a post-resolution hook in the routing pipeline (`relationship.tools.relationship_assert_fact.assert_sender_channel_fact()`); the Switchboard ingress path never writes `relationship.entity_facts`.

The identity preamble for unknown senders includes `-- pending disambiguation`, signaling to the receiving butler that the sender identity is provisional.

### Owner notification for surfaced unknown senders

Normal Switchboard fleet ingress enables identity resolution and supplies a
deterministic owner-notification callback. When the helper surfaces a
transitory entity, it first atomically inserts a sender-scoped claim in the
Switchboard `state` table. Only the winner sends one content-free `notify.v1`
notice through the normal Switchboard-to-Messenger boundary; the notice points
to `/entities/index?state=unidentified`, never a contacts route.

The claim remains after a delivery failure, so later ingress cannot turn an
outage into a notification storm. If the state claim cannot be persisted, the
helper logs the problem, skips the owner send, and leaves normal unknown-sender
routing intact. The owner can still review the transitory entity through the
Unidentified Entities flow.

## Identity Preamble

The `build_identity_preamble()` function constructs a structured text prefix that is prepended to every routed message. The format varies by sender type:

- **Owner:** `[Source: Owner (entity_id: <uuid>), via telegram]`
- **Known contact:** `[Source: Chloe (entity_id: <uuid>), via telegram]`
- **Unknown sender:** `[Source: Unknown sender (entity_id: <uuid>), via telegram -- pending disambiguation]`

This preamble gives domain butlers the sender context they need for personalized responses, access control decisions, and entity-linked memory retrieval.

## Usage Points

Identity resolution is called at several points in the system:

- **Switchboard ingestion** --- before routing, to inject the sender identity preamble
- **notify()** --- to resolve outbound recipients from an `entity_id`; when optional `channel` is omitted, it selects the entity's preferred `channel` only when it is deliverable and reachable through an active `has-*` fact; otherwise it falls back to `telegram`, then `email`; without an `entity_id`, it defaults to `telegram`
- **Approval gate** --- to replace name-heuristic target resolution with role-based checks
- **Memory module** --- to anchor facts and episodes to the correct entity

## Merge Rebind Receipts

Entity merges are coordinated only by Relationship. The merge transaction
rewires canonical relationship facts and the shared `public.memory_catalog`,
tombstones the source, and opens a cohort in `public.entity_rebind_log` with
one receipt per memory-bearing schema. Each daemon later updates only its own
facts and association tables and settles its receipt. `pending` therefore
means "not yet reported", while `failed` names the exception class; neither is
presented as a zero-count success.

The post-commit `entity.rebound.v1` fleet event makes running daemons process
their local pending receipt immediately and refreshes dashboard caches. Event
delivery is deliberately not the recovery authority: a daemon that missed it
establishes its listener before draining pending receipts during startup. A
listener connection failure triggers re-establishment plus another pending
drain. PostgreSQL row security permits only Relationship to create a cohort
and permits each runtime role to settle only the receipt bound to its own
schema.

Owner-source merges retain the immediate singleton index. Inside the same
locked merge transaction, the source is tombstoned and loses only `owner`
before the target receives the ordered role union computed from the original
rows. Other source roles remain on its history. A downstream exception rolls
back both identities and all moved references; no audit, receipt cohort or
post-commit event records a successful merge.

## Verification

To confirm the identity model described here matches the running system:

```bash
# 1. Owner entity exists in public.entities with "owner" role
psql -h localhost -U butlers -d butlers -c \
  "SELECT id, canonical_name, roles FROM public.entities WHERE 'owner' = ANY(roles);"
# Expected: exactly one row with roles including "owner"

# 2. Owner's channel handles are stored in relationship.entity_facts
psql -h localhost -U butlers -d butlers -c \
  "SELECT subject, predicate, object FROM relationship.entity_facts
   WHERE object LIKE 'telegram:%' AND validity = 'active' LIMIT 5;"
# Expected: row(s) with predicate "has-handle" and object "telegram:<chat_id>"

# 3. Identity resolution returns the owner for a known Telegram chat ID
# In Python (with a running pool), call:
#   from butlers.identity import resolve_contact_by_channel
#   result = await resolve_contact_by_channel(pool, "telegram", "telegram:<your_chat_id>")
#   assert "owner" in result.roles

# 4. Unknown sender creates a temporary entity with unidentified metadata
psql -h localhost -U butlers -d butlers -c \
  "SELECT COUNT(*) FROM public.entities WHERE metadata->>'unidentified' = 'true';"
# Expected: count matches the number of unrecognized senders seen by the Switchboard
```

## Implementation Notes

- Owner bootstrap at daemon startup (`src/butlers/owner_bootstrap.py::_ensure_owner_entity`,
  called from `src/butlers/lifecycle.py`) resolves an existing owner via
  `'owner' = ANY(roles)` before inserting, and inserts with a target-less `ON CONFLICT DO NOTHING`
  so `ix_entities_owner_singleton` cannot raise during startup.
- Owner Telegram handle seeding is relationship-only:
  `src/butlers/owner_bootstrap.py::_seed_owner_telegram_handle` checks
  `current_schema() = 'relationship'` before probing `relationship.entity_facts`.

## Related Pages

- [Switchboard Routing](switchboard-routing.md) --- how identity preambles are injected during routing
- [Modules and Connectors](modules-and-connectors.md) --- how connectors provide sender identity
- [Trigger Flow](trigger-flow.md) --- how identity-resolved messages become sessions

## Report authority and channel adoption

Relationship stores the original report independently of confidence: content authority, a
nullable live reporter, the inert original UUID and its source-captured `created_at` witness.
New report versions use the new admitted reporter. Corrections, confirmation, adoption and
approval replay retain the selected original report. Authorized Google/Steam entity deletion
uses the existing subject FK and Relationship's generated `object_entity_id` UUID FK to cascade
all subject/object versions. Literal objects produce NULL, including UUID-shaped literals;
malformed entity UUIDs refuse migration rather than being reclassified. The object FK is
installed `NOT VALID` to preserve existing well-typed orphan history; new reference changes
must resolve. Reporter-only deletion clears the live link while
the surviving report explicitly records deleted reporter availability. The original UUID never joins a recreated
entity. Legacy NULL authority stays resolvable and unknown; no backfill guesses an owner.

Third-party or mixed channel reports on a known person remain candidates. They do not supply
inbound identity, outbound reachability, coverage, graph edges or established gap answers. The
protected contact card's exact Adopt/Reject operation records its effects and decision receipt
in one Relationship transaction. A lost acknowledgment requires decision readback, not an
automatic resend. Compatible adoption returns the existing active occurrence as an explicit
survivor, carries the candidate's typed evidence, and retains the superseded candidate and its
original report. A different packet or ambiguous occurrence refuses without a decision receipt.
Ordinary candidate reassertion preserves the stored default packet; it cannot clear a known bound.
Owner confirmation is displayed separately from the original reporter. Responses distinguish
`owner_asserted`, `owner_confirmed`, `legacy_verified`, and `unconfirmed`. Raw legacy verification
renders as "Legacy verification: author unknown", including core-date and identity inspector rows.
Deleted, forgotten and merged reporters are separate unavailable states without a navigation link.
Only the selected candidate's decision controls are busy during its pending request.
Adoption serializes canonical recipient slots, including the existing bounded phone suffix
matches. A merge locks the complete affected slot union in one order. Preferred-channel
serialization belongs to its actual subject; two reachable people may both prefer email.

Fact approval replay binds the executor's locked original action and argument digest before
retiring legacy `verified` keys. A standing permission additionally requires its actual
owner-admitted creation record, `rule_created` and `action_auto_approved` events, stored rule
birth and unchanged constraints matching the stored action. The subject's owner role is not
an outbound-message bypass for fact confirmation. An admitted request may finish after a
later revocation; this does not supply custody's commit-time currentness guarantee.

The fact writer and executor acknowledgement commit on separate connections. If the fact
handler returns but terminal acknowledgement fails, the result is explicitly unknown and
retains the fact/action locators. Read the durable fact and action before retrying; replay of
the same approved record preserves the selected report and avoids a second fact version.

Switchboard has no direct Relationship fact grant. It calls the registered, read-only owning
`identity_resolve_channels(channel_type, channel_values)` MCP boundary, which preserves the
canonical normalization and ambiguity rules. The deterministic unidentified-sender hook calls
`identity_assert_sender_channel` on that same owner; its public-entity reservation still
deduplicates ingress when the owning writer is unavailable. These results attribute identity;
they do not authenticate a channel. No channel-mapping revision/current-at-COMMIT guarantee is
introduced by an entity lifetime witness.

The dashboard's private stamp binds its first admitted turn to the canonical event, source endpoint,
conversation/thread, sender, body/page context and pinned destination. The owning accepted-source
reader compares that binding independently before borrowing owner-device attribution. Exact text
and a copied message UUID alone are insufficient. Transport `observed_at` may change on a retry;
the original stable binding and report remain frozen. This is request admission under the adopted
trusted host boundary, not a custody COMMIT fence or external transport authentication proof.
