# Identity Model

> **Purpose:** Explain how the shared identity schema maps external sender identifiers to known contacts and roles, enabling identity-aware routing and access control.
> **Audience:** Developers working on identity resolution, routing, approval gates, or contact management.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md), [Switchboard Routing](switchboard-routing.md).

## Overview

Butlers maintains a shared identity registry in the `shared` PostgreSQL schema. This registry maps external channel identifiers (Telegram chat IDs, email addresses, Discord handles) to canonical contact records, which in turn link to entity records carrying roles. The identity model powers sender recognition during Switchboard ingestion, owner-aware routing, approval gate role checks, and outbound recipient resolution via `notify()`.

## Schema Structure

The identity model spans three tables in the `shared` schema:

### shared.entities

The entity table is the anchor for identity. Each row represents a known person or actor. Key fields:

- **`id`** (UUID) --- primary key
- **`tenant_id`** --- always `"shared"` in the default tenant model
- **`canonical_name`** --- display name
- **`entity_type`** --- typically `"person"`
- **`roles`** (TEXT[]) --- role assignments (e.g., `['owner']`)
- **`aliases`** (TEXT[]) --- alternative names
- **`metadata`** (JSONB) --- extensible metadata; temporary entities carry `{"unidentified": true}`

### shared.contacts

The contacts table links a named contact to an entity. Key fields:

- **`id`** (UUID) --- primary key
- **`name`** --- display name
- **`entity_id`** (UUID, FK to `shared.entities`) --- the linked entity
- **`roles`** (TEXT[]) --- legacy roles array (roles are now primarily sourced from the entity)
- **`metadata`** (JSONB) --- extensible; temporary contacts carry `{"needs_disambiguation": true}`

### shared.contact_info

The contact_info table stores per-channel identifiers linked to contacts. Key fields:

- **`contact_id`** (UUID, FK to `shared.contacts`) --- the owning contact
- **`type`** (TEXT) --- channel type (e.g., `"telegram"`, `"email"`, `"discord"`)
- **`value`** (TEXT) --- the channel-specific identifier (e.g., a Telegram chat ID, an email address)
- **`is_primary`** (BOOLEAN) --- whether this is the primary contact method for the channel
- **`secured`** (BOOLEAN) --- marks credential entries

A UNIQUE constraint on `(type, value)` guarantees at most one contact per channel identifier.

## Identity Resolution

The core identity operation is `resolve_contact_by_channel()` in `src/butlers/identity.py`. Given a channel type and value, it performs a JOIN across all three tables:

```sql
SELECT c.id, c.name, COALESCE(e.roles, '{}'), c.entity_id
FROM shared.contact_info ci
JOIN shared.contacts c ON c.id = ci.contact_id
LEFT JOIN shared.entities e ON e.id = c.entity_id
WHERE ci.type = $1 AND ci.value = $2
```

The result is a `ResolvedContact` dataclass containing `contact_id`, `name`, `roles` (sourced from the entity, not the contact), and `entity_id`.

The function is safe to call before migrations have run --- it catches all database exceptions and returns `None` gracefully.

## Owner Contact

The owner contact is the system administrator. It is bootstrapped automatically on daemon startup. The owner entity carries the `"owner"` role, which is used for:

- **Identity preamble** --- Routed messages from the owner are prepended with `[Source: Owner (contact_id: ..., entity_id: ...), via <channel>]`.
- **Approval gates** --- Certain sensitive tool calls require owner authorization.
- **Routing priority** --- Owner messages may receive preferential queue ordering.

## Unknown Sender Handling

When identity resolution returns no match for a sender, the system creates a temporary contact and entity via `create_temp_contact()`. This function:

1. Creates a `shared.entities` row with `metadata.unidentified = true` and `entity_type = "person"`.
2. Creates a `shared.contacts` row linked to the entity with `metadata.needs_disambiguation = true`.
3. Creates a `shared.contact_info` row for the channel identifier (using `ON CONFLICT DO NOTHING` for race safety).
4. Returns a `ResolvedContact` with empty roles.

The identity preamble for unknown senders includes `-- pending disambiguation`, signaling to the receiving butler that the sender identity is provisional.

## Identity Preamble

The `build_identity_preamble()` function constructs a structured text prefix that is prepended to every routed message. The format varies by sender type:

- **Owner:** `[Source: Owner (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- **Known contact:** `[Source: Chloe (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- **Unknown sender:** `[Source: Unknown sender (contact_id: <uuid>, entity_id: <uuid>), via telegram -- pending disambiguation]`

This preamble gives domain butlers the sender context they need for personalized responses, access control decisions, and entity-linked memory retrieval.

## Tenant Model

All identity tables use `tenant_id = "shared"` as the default tenant. This unified tenant model means all butlers in a deployment share a single identity namespace. The `shared` schema is readable by all butler database roles, while each butler's own schema is private.

## Usage Points

Identity resolution is called at several points in the system:

- **Switchboard ingestion** --- before routing, to inject the sender identity preamble
- **notify()** --- to resolve outbound recipients from a `contact_id`
- **Approval gate** --- to replace name-heuristic target resolution with role-based checks
- **Memory module** --- to anchor facts and episodes to the correct entity

## Related Pages

- [Switchboard Routing](switchboard-routing.md) --- how identity preambles are injected during routing
- [Modules and Connectors](modules-and-connectors.md) --- how connectors provide sender identity
- [Trigger Flow](trigger-flow.md) --- how identity-resolved messages become sessions
