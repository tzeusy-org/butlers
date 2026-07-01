# Identity Model

> **Purpose:** Explain how the shared identity schema maps external sender identifiers to known entities and roles, enabling identity-aware routing and access control.
> **Audience:** Developers working on identity resolution, routing, approval gates, or contact management.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md), [Switchboard Routing](switchboard-routing.md).

## Overview

Butlers maintains a shared identity registry anchored on `public.entities`. Channel identifiers (Telegram chat IDs, email addresses, Discord handles) attach to an entity as `relationship.entity_facts` triples, and roles live on the entity itself. The identity model powers sender recognition during Switchboard ingestion, owner-aware routing, approval gate role checks, and outbound recipient resolution via `notify()`.

## Schema Structure

Identity resolution reads two tables: the entity anchor in the `public` schema and the channel-handle triples in the `relationship` schema. (The earlier `public.contacts` and `public.contact_info` tables are retired --- `public.contact_info` was dropped in `core_115` and `public.contacts` in `core_134`; resolution no longer touches either.)

### public.entities

The entity table is the anchor for identity. Each row represents a known person or actor. Key fields:

- **`id`** (UUID) --- primary key; the authoritative identity key
- **`canonical_name`** --- display name
- **`entity_type`** --- typically `"person"`
- **`roles`** (TEXT[]) --- role assignments (e.g., `['owner']`); the authoritative source of truth for identity roles
- **`aliases`** (TEXT[]) --- alternative names
- **`metadata`** (JSONB) --- extensible metadata; temporary entities carry `{"unidentified": true}`

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

The result is a `ResolvedContact` dataclass containing `name`, `roles` (sourced from the entity), `entity_id` (the authoritative key), and `contact_id` (always `None` since resolution no longer reads `public.contacts`).

The function is safe to call before migrations have run --- it catches all database exceptions and returns `None` gracefully.

## Owner Contact

The owner contact is the system administrator. It is bootstrapped automatically on daemon startup. The owner entity carries the `"owner"` role, which is used for:

- **Identity preamble** --- Routed messages from the owner are prepended with `[Source: Owner (contact_id: ..., entity_id: ...), via <channel>]`.
- **Approval gates** --- Certain sensitive tool calls require owner authorization.
- **Routing priority** --- Owner messages may receive preferential queue ordering.

## Unknown Sender Handling

When identity resolution returns no match for a sender, the system creates a temporary entity via `create_temp_contact()`. This function:

1. Re-checks the triple store to avoid double-creation; if the channel identifier already resolves, it returns that entity instead of minting a duplicate.
2. Creates a `public.entities` row with `metadata.unidentified = true` and `entity_type = "person"`.
3. Returns a `ResolvedContact` with empty roles and `contact_id = None`.

The sender's channel triple is not written here. Asserting the `relationship.entity_facts` handle happens in a post-resolution hook in the routing pipeline (`relationship.tools.relationship_assert_fact.assert_sender_channel_fact()`); the Switchboard ingress path never writes `relationship.entity_facts`.

The identity preamble for unknown senders includes `-- pending disambiguation`, signaling to the receiving butler that the sender identity is provisional.

## Identity Preamble

The `build_identity_preamble()` function constructs a structured text prefix that is prepended to every routed message. The format varies by sender type:

- **Owner:** `[Source: Owner (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- **Known contact:** `[Source: Chloe (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- **Unknown sender:** `[Source: Unknown sender (contact_id: <uuid>, entity_id: <uuid>), via telegram -- pending disambiguation]`

This preamble gives domain butlers the sender context they need for personalized responses, access control decisions, and entity-linked memory retrieval.

## Usage Points

Identity resolution is called at several points in the system:

- **Switchboard ingestion** --- before routing, to inject the sender identity preamble
- **notify()** --- to resolve outbound recipients from a `contact_id`
- **Approval gate** --- to replace name-heuristic target resolution with role-based checks
- **Memory module** --- to anchor facts and episodes to the correct entity

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

# 4. public.contact_info and public.contacts tables no longer exist
psql -h localhost -U butlers -d butlers -c \
  "SELECT to_regclass('public.contact_info'), to_regclass('public.contacts');"
# Expected: both values NULL (dropped in core_115 and core_134)

# 5. Unknown sender creates a temporary entity with unidentified metadata
psql -h localhost -U butlers -d butlers -c \
  "SELECT COUNT(*) FROM public.entities WHERE metadata->>'unidentified' = 'true';"
# Expected: count matches the number of unrecognized senders seen by the Switchboard
```

## Related Pages

- [Switchboard Routing](switchboard-routing.md) --- how identity preambles are injected during routing
- [Modules and Connectors](modules-and-connectors.md) --- how connectors provide sender identity
- [Trigger Flow](trigger-flow.md) --- how identity-resolved messages become sessions
