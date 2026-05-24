# routing_log.contact_id Population Audit

**Date:** 2026-05-25
**Bead:** bu-tbne5
**Scope:** Determine whether `routing_log.contact_id` is still meaningfully populated
post the bu-akads read-path cut-over (PR #1921), and recommend a deprecation path.

---

## Background

`routing_log` carries identity context for every routed message: `contact_id`,
`entity_id`, and `sender_roles`.  The bu-akads bead cut the Switchboard
read-path over to `relationship.entity_facts`, making `entity_id` the
authoritative sender key.  This audit checks whether `contact_id` still
receives non-NULL values anywhere in the write path.

---

## Schema

`routing_log.contact_id` is defined as `UUID` (nullable) in migration
`001_switchboard_messaging.py` (sw_001/sw_023 amendment).  An index exists:

```sql
CREATE INDEX idx_routing_log_contact_id
ON routing_log (contact_id)
WHERE contact_id IS NOT NULL;
```

---

## Population Paths

### Path 1: `route.py` → `_log_routing()`

All five call sites in `roster/switchboard/tools/routing/route.py` call
`_log_routing()` with only the seven positional args (source, target, tool,
success, duration_ms, error).  **`contact_id`, `entity_id`, and `sender_roles`
are never passed.**  All three columns are always `NULL` for rows written via
`route()` and `post_mail()`.

### Path 2: `pipeline.py` → `resolve_and_inject_identity()` → routing context

`src/butlers/modules/pipeline.py` (lines 1707–1718) calls
`resolve_and_inject_identity()` and captures `identity_result.contact_id`.
This is stored in routing context and later embedded in `RouteRequestContextV1`
as `source_sender_contact_id` (via `_switchboard.py` line 422).

However, `source_sender_contact_id` from `RouteRequestContextV1` is **not**
written to `routing_log.contact_id`.  It travels with the routed message
envelope for downstream consumers but is never fed back into `_log_routing`.
The `routing_log` insertion from `route()` (Path 1) happens independently
without access to the identity result.

**Net result for Path 2:** `routing_log.contact_id` is not populated here.

### Path 3: `identity.py` → `resolve_contact_by_channel()` — always returns `contact_id=None`

Since bu-akads, `resolve_contact_by_channel()` queries `relationship.entity_facts`
exclusively and constructs:

```python
return ResolvedContact(
    contact_id=None,  # entity_id is now the authoritative key (bead 7)
    ...
)
```

For known (resolved) contacts, `IdentityResolutionResult.contact_id` is
therefore always `None`.

### Path 4: `identity.py` → `create_temp_contact()` — may return a real `contact_id`

`create_temp_contact()` still writes to `public.contacts` and returns a
`ResolvedContact` with a real `contact_id` UUID.  This means
`IdentityResolutionResult.contact_id` **can be non-NULL** for unknown senders
whose temp contact was just created.

The value is captured in `source_contact_id` / `source_sender_contact_id` and
flows into the routing envelope — but per Path 2 above, it does **not** reach
`routing_log.contact_id`.

### Path 5: `extraction.py` → `_log_extractions()`

`src/butlers/tools/extraction.py` inserts into `routing_log` with only six
columns (source_butler, target_butler, tool_name, success, duration_ms, error).
`contact_id` is not set.

---

## Summary

| Path | contact_id written? | Notes |
|------|--------------------|----|
| `route()` → `_log_routing()` | No (always NULL) | 5 call sites, no identity args passed |
| `pipeline.py` identity resolution | No | Captured in routing envelope, not in routing_log |
| Resolved contact | No (always NULL) | `resolve_contact_by_channel` returns `contact_id=None` post bu-akads |
| Unknown temp contact | No (NULL in routing_log) | `create_temp_contact` sets it in `IdentityResolutionResult`, but it never reaches `_log_routing` |
| `extraction.py` | No (always NULL) | Column not included in INSERT |

**Conclusion:** `routing_log.contact_id` is always `NULL` in practice. No code
path currently writes a non-NULL value to this column.  The index
`idx_routing_log_contact_id` is therefore unused.

---

## Recommendation

Three options are available.  The recommendation is **Option A**.

### Option A (recommended): Deprecate and drop the column (separate bead)

Create a follow-up bead to:
1. Drop `idx_routing_log_contact_id` (unused index, minor write overhead).
2. Drop `routing_log.contact_id` via a migration.
3. Remove `contact_id` from `_log_routing()` signature and the `routing_log`
   INSERT statement.
4. Remove `contact_id: UUID | None` from `IdentityResolutionResult` (it's
   always `None` for known contacts; for unknowns it flows via entity_id
   instead).
5. Update `RouteRequestContextV1.source_sender_contact_id` — assess whether
   downstream consumers still need it.  If not, drop it too.

**Risk:** Low.  The column is already always `NULL`.  No downstream read path
depends on it.  The migration is a simple `ALTER TABLE routing_log DROP COLUMN
contact_id` plus index drop.

### Option B: Drop from `IdentityResolutionResult` only

Remove `contact_id` from the dataclass and `inject.py` population logic but
keep the DB column (always stays `NULL`).  Cleaner Python interface, defers
schema migration.

**Risk:** Very low, but leaves dead column and dead index in DB.

### Option C: Keep as-is

Minimal change — leave both the DB column and the Python field in place,
documented as deprecated.  No migration needed.

**Risk:** None, but accumulates technical debt and wastes index storage.

---

## Suggested Follow-up Bead

**Title:** `chore(switchboard): drop routing_log.contact_id column and IdentityResolutionResult.contact_id field [post-bu-akads cleanup]`

**Description:**
`routing_log.contact_id` is always NULL post the bu-akads read-path cut-over
(see audit report `docs/reports/routing-log-contact-id-audit-2026-05-25.md`).
No code path writes a non-NULL value.

Tasks:
1. Alembic/migration: `DROP INDEX idx_routing_log_contact_id`, `ALTER TABLE routing_log DROP COLUMN contact_id`
2. Remove `contact_id` param from `_log_routing()` and the INSERT statement
3. Remove `contact_id: UUID | None` from `IdentityResolutionResult` and all
   population in `inject.py`
4. Assess `RouteRequestContextV1.source_sender_contact_id` — remove if no downstream consumer needs it
5. Update tests that reference `routing_log.contact_id` or `IdentityResolutionResult.contact_id`

Priority: P3 (low — no functional impact, pure cleanup)
Discovered from: bu-tbne5
