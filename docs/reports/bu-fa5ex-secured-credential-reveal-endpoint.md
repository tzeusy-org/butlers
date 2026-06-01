# Secured Credential Reveal Endpoint Design

**Bead**: bu-fa5ex
**Status**: Implemented
**Date**: 2026-06-01

## Reconciliation: Canonical Home for Secured Credential Rows

The bead text referenced `relationship.credentials` as the canonical home for
`secured=true` contact_info rows. **This is stale.** The authoritative sources
resolve differently:

| Source | Says |
|--------|------|
| RFC 0004 Amendment 2 | `public.entity_info` is the entity-level credential anchor (unchanged by Amendment 2) |
| Alembic migrations | `public.entity_info` table exists; no `relationship.credentials` table |
| PR #2042 (bu-pl8fy) shipped code | `create_contact_info` with `secured=True` writes to `public.entity_info` via `INSERT ... ON CONFLICT (entity_id, type) DO UPDATE` |

**Conclusion**: `public.entity_info` is canonical. The bead text was stale.
Implementation targets `public.entity_info` exclusively.

## Endpoint Design

### Why a dedicated reveal endpoint?

`GET /entities/{id}` returns all `entity_info` entries but masks secured values
(`value=None` for rows where `secured=True`). Callers that need the actual
credential value use the reveal endpoint. This pattern follows the existing
`GET /contacts/{id}/secrets/{info_id}` contract and the RFC 0007 dashboard API
surface spec.

### Endpoint

```
GET /api/relationship/entities/{entity_id}/secrets/{info_id}
```

**Already existed** (implemented in bu-pl8fy). This bead adds the missing
owner-only authz gate.

### Authorization model

**Owner-only gate** via `_assert_owner_role(pool)`, identical to the gate
applied to other PII-bearing read surfaces in this router (Amendment 12b,
entity-redesign Phase 2).

The gate checks that at least one `public.entities` row carries the `'owner'`
role. If not, the system is considered misconfigured and returns HTTP 403 with
`{"code": "owner_required"}`. In the v1 single-tenant dashboard model, owner
presence is the caller-identity proxy.

### Response contract

| Condition | Status | Body |
|-----------|--------|------|
| Owner present, entry found, `secured=True` | 200 | `{"id": "...", "type": "...", "value": "..."}` |
| No owner entity registered | 403 | `{"code": "owner_required", "message": "..."}` |
| Entry not found / wrong entity | 404 | `{"detail": "Entity info entry not found"}` |
| Entry exists but `secured=False` | 400 | `{"detail": "This entity_info entry is not secured; ..."}` |

### Audit trail

Every successful 200 reveal emits a `reveal_entity_secret` dashboard audit
event via `emit_dashboard_audit`. GETs bypass the middleware-level audit hook,
so this explicit call is required, matching the same pattern in
`reveal_contact_secret`.

### RFC 0004 Amendment 2 carve-out preserved

Non-secured channel identifiers live in `relationship.entity_facts` (RDF triple
store). Those rows carry `secured=False` by design and are returned in plain
view by the entity detail endpoint. The reveal endpoint is only relevant for
`public.entity_info` rows where `secured=True` (the credential
carve-out population defined by RFC 0004 Amendment 2).

## Files Changed

- `roster/relationship/api/router.py`: added owner-only authz gate to
  `reveal_entity_secret` handler; expanded docstring to document authz model
  and credential-carve-out semantics.
- `tests/api/test_relationship_entity_info_reveal.py`: unit tests for
  authorized success, unauthorized rejection (403), 404 for missing entry,
  400 for non-secured entry, and audit emission.
