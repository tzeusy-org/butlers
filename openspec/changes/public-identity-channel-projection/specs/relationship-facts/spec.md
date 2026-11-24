## MODIFIED Requirements

### Requirement: Switchboard `resolve_contact_by_channel()` re-points to triples

The Switchboard's `resolve_contact_by_channel()` function (defined in RFC 0004 §"resolve_contact_by_channel()", `rfcs/0004:83-95`) SHALL query `public.identity_channel_projection` (the dropped `public.contact_info` table is never read). The query MUST use the following SQL shape:
- Required public-only query shape:
```sql
WITH channel_facts AS (
    SELECT entity_id AS subject, predicate, value AS object,
           'literal'::text AS object_kind, 'active'::text AS validity
    FROM public.identity_channel_projection
)
SELECT f.subject AS entity_id,
       e.canonical_name AS name,
       COALESCE(e.roles, '{}') AS roles
FROM channel_facts f
JOIN public.entities e ON e.id = f.subject
WHERE f.predicate = $1                                  -- e.g. 'has-handle' or 'has-email'
  AND f.object   = $2                                   -- channel-specific identifier
  AND f.object_kind = 'literal'
  AND f.validity = 'active';
```
- The channel-type → predicate mapping is:
- `telegram` → `has-handle` (object value = `telegram:<chat_id>`)
- `email` → `has-email`
- `discord` → `has-handle` (object = `discord:<user_id>`)
- `phone` → `has-phone`
- The `ResolvedContact` return shape carries `entity_id` as the routing key in place of the
retired `contact_id`, and the identity preamble references only `entity_id`.
- The CTE adapts the bounded projection row contract to the existing triple query shape; it does not read the private source. The resolver MUST additionally preserve current full distinct-live-entity ambiguity, normalization and liveness checks. After verified cutover every eligible committed source triple MUST have its exact derived row through same-transaction maintenance. A missing projected row MUST return no established identity, even if a private source row exists; private-table or definer fallback MUST NOT be used.

ID: REQ-relationship-facts-009
Source: RFC 0004 Amendment 4 §§4.1–4.3, 4.5–4.6; existing source-owned channel triple contract
Scope: v1-mandatory

#### Scenario: Telegram chat resolves to entity via has-handle triple
- **WHEN** an incoming Telegram message arrives with chat_id `12345` and a triple
  `(subject=ent-7, predicate='has-handle', object='telegram:12345',
  object_kind='literal', validity='active')` exists in `relationship.entity_facts`
- **THEN** `resolve_contact_by_channel('telegram', 'telegram:12345')` MUST return a
  `ResolvedContact` with `entity_id = ent-7`
- **AND** the returned shape MUST NOT include a `contact_id` field

#### Scenario: Unknown channel value returns no match
- **WHEN** `resolve_contact_by_channel('email', 'nobody@example.com')` is called and no
  triple with predicate `has-email` and object `nobody@example.com` exists
- **THEN** the function MUST return `None` (or equivalent absent-match sentinel)
- **AND** the identity preamble builder MUST treat the caller as unknown

#### Scenario: Eligible source commit publishes the existing resolution result

- **WHEN** an admitted Relationship source transaction commits an eligible channel fact
- **THEN** its exact public projected row MUST commit with it and the existing channel/entity-only resolution result MUST remain available
- **AND** another runtime resolver MUST need no access to the private Relationship schema

#### Scenario: Private fact without projected row remains unresolved

- **WHEN** an eligible source fact exists but the corresponding public row is absent during partial recovery
- **THEN** identity resolution MUST return the absent-match sentinel without querying the private source
- **AND** verified population parity MUST be required before claiming successful cutover or recovery
