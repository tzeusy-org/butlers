# RFC 0004: Identity and Contact Resolution

**Status:** Accepted
**Date:** 2026-03-24

## Summary

Butlers maintains a shared identity registry spanning three tables in the `public` PostgreSQL schema: `entities`, `contacts`, and `contact_info`. The `resolve_contact_by_channel()` function performs a 3-table JOIN to map external channel identifiers to canonical contact records with role information. Unknown senders receive temporary identity records with disambiguation metadata. An identity preamble is prepended to every routed message, providing downstream butlers with structured sender context for personalized responses, access control, and entity-linked memory.

## Motivation

Every external message entering the system arrives with a provider-native sender identifier (Telegram user ID, email address, Discord handle). Butlers need to resolve this raw identifier to a known identity to enable: owner-aware routing and priority handling, role-based approval gates, entity-linked memory retrieval, personalized responses, and outbound notification targeting. Without a shared identity layer, each butler would need its own contact database, creating duplication and inconsistency.

## Design

### Schema Structure

All identity tables reside in the `public` PostgreSQL schema, readable by all butler database roles.

#### public.entities

The anchor table for identity. Each row represents a known person or actor.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | TEXT | Always `"shared"` in the default tenant model |
| `canonical_name` | TEXT | Display name |
| `entity_type` | TEXT | Typically `"person"` |
| `roles` | TEXT[] | Role assignments (e.g., `['owner']`) |
| `aliases` | TEXT[] | Alternative names |
| `metadata` | JSONB | Extensible; temporary entities carry `{"unidentified": true}` |

#### public.contacts

Links a named contact record to an entity.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `name` | TEXT | Display name |
| `entity_id` | UUID (FK) | Links to `public.entities` |
| `roles` | TEXT[] | Legacy roles array (roles are now primarily sourced from the entity) |
| `metadata` | JSONB | Extensible; temporary contacts carry `{"needs_disambiguation": true}` |

#### public.contact_info

Per-channel identifiers linked to contacts.

| Column | Type | Description |
|--------|------|-------------|
| `contact_id` | UUID (FK) | Links to `public.contacts` |
| `type` | TEXT | Channel type: `"telegram"`, `"email"`, `"discord"`, etc. |
| `value` | TEXT | Channel-specific identifier (chat ID, email address, etc.) |
| `is_primary` | BOOLEAN | Whether this is the primary contact method for the channel |
| `secured` | BOOLEAN | Marks credential entries (e.g., API keys stored as contact info) |

A `UNIQUE` constraint on `(type, value)` guarantees at most one contact per channel identifier.

#### public.entity_info

Extended identity-bound data linked to entities. Used for credentials that belong to a specific identity (see RFC 0006 for credential store details).

| Column | Type | Description |
|--------|------|-------------|
| `entity_id` | UUID (FK) | Links to `public.entities` |
| `info_type` | TEXT | Type identifier (e.g., `"google_oauth_refresh"`, `"telegram_api_id"`) |
| `value` | TEXT | The stored value |
| `is_primary` | BOOLEAN | Preferred entry when multiple exist |

**Registered `info_type` values:**

| `info_type` | Description | Secured |
|-------------|-------------|---------|
| `google_oauth_refresh` | Google OAuth 2.0 refresh token | yes |
| `telegram_api_id` | Telegram user-client API ID | no |
| `telegram_api_hash` | Telegram user-client API hash | yes |
| `steam_api_key` | Steam Web API key for the gaming butler | yes |

### resolve_contact_by_channel()

The core identity operation in `src/butlers/identity.py`. Given a channel type and value, it performs:

```sql
SELECT c.id, c.name, COALESCE(e.roles, '{}'), c.entity_id
FROM public.contact_info ci
JOIN public.contacts c ON c.id = ci.contact_id
LEFT JOIN public.entities e ON e.id = c.entity_id
WHERE ci.type = $1 AND ci.value = $2
```

Returns a `ResolvedContact` dataclass:

```python
@dataclass
class ResolvedContact:
    contact_id: UUID
    name: str
    roles: list[str]  # Sourced from entity, not contact
    entity_id: UUID | None
```

**Resilience contract:** The function catches all database exceptions and returns `None` gracefully. It is safe to call before migrations have run, during partial startup, or when the database is temporarily unavailable.

### Unknown Sender Handling

When `resolve_contact_by_channel()` returns no match, the system creates a temporary identity via `create_temp_contact()`:

1. Creates a `public.entities` row with `metadata = {"unidentified": true}`, `entity_type = "person"`.
2. Creates a `public.contacts` row linked to the entity with `metadata = {"needs_disambiguation": true}`.
3. Creates a `public.contact_info` row for the channel identifier, using `ON CONFLICT DO NOTHING` for race safety when concurrent requests arrive from the same unknown sender.
4. Returns a `ResolvedContact` with empty roles.

Temporary entities and contacts are distinguishable from permanent ones by their metadata flags. They are intended to be merged or promoted by operator action via the dashboard.

### Identity Preamble

The `build_identity_preamble()` function constructs a structured text prefix prepended to every routed message. The format varies by sender classification:

- **Owner:** `[Source: Owner (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- **Known contact:** `[Source: Chloe (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- **Unknown sender:** `[Source: Unknown sender (contact_id: <uuid>, entity_id: <uuid>), via telegram -- pending disambiguation]`

This preamble provides downstream butlers with:

- Sender name for personalized responses
- `contact_id` and `entity_id` for entity-linked memory retrieval and fact anchoring
- Channel source for reply targeting
- Disambiguation status for trust-level decisions

### Owner Contact

The owner entity is bootstrapped automatically on daemon startup. It carries the `"owner"` role, which is used for:

- **Identity preamble** -- Owner messages are identified with `[Source: Owner ...]`.
- **Approval gates** -- Certain sensitive tool calls may require owner authorization.
- **Routing priority** -- Owner messages receive preferential queue ordering in the email priority tier system (see RFC 0003).
- **Credential anchoring** -- Owner entity_info entries store identity-bound credentials (e.g., Telegram user-client session, Google OAuth tokens).

### Tenant Model

All identity tables use `tenant_id = "shared"` as the default. This unified tenant model means all butlers in a deployment share a single identity namespace. The `public` schema is readable by all butler database roles, while each butler's own schema is private (see RFC 0006).

### Usage Points

Identity resolution is invoked at:

| Integration Point | Purpose |
|-------------------|---------|
| Switchboard ingestion (RFC 0003) | Resolve sender identity, build preamble for routed messages |
| `notify()` tool | Resolve outbound recipient from `contact_id` to channel-specific address |
| Approval gate | Role-based access control for sensitive tool calls |
| Memory module | Anchor facts and episodes to the correct entity for retrieval |

## Integration

- **RFC 0003:** The Switchboard resolves sender identity during ingestion and prepends the identity preamble to routed messages.
- **RFC 0005:** Identity resolution failures are logged but do not create OTel error spans; the system degrades gracefully to unknown-sender handling.
- **RFC 0006:** Identity tables live in the `public` schema, readable by all butlers. Writes are primarily controlled by the contacts module in the relationship butler.
- **RFC 0007:** The dashboard exposes contact management, entity detail views, and unknown-sender disambiguation workflows.

## Alternatives Considered

**Per-butler contact tables.** Rejected because every butler needs sender context for personalized responses. Duplicating contact data across schemas would create synchronization problems and inconsistent identity states.

**Roles on contacts instead of entities.** The `contacts.roles` column exists as a legacy artifact. Roles are now sourced from the entity, which is the canonical anchor. A contact is a named reference to an entity, not an independent role bearer. The entity-centric model supports multiple contacts per entity (e.g., a person with both personal and work email addresses) sharing the same role set.

**Inline identity in message payload.** Rejected in favor of the structured preamble. Inline identity would require every butler to parse arbitrary message formats. The preamble provides a predictable, machine-readable prefix that LLM sessions can consistently interpret.
