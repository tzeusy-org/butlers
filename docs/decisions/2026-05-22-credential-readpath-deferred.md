# Decision: Credential read-path cut-over to `relationship.credentials` — Deferred

**Date:** 2026-05-22
**Bead:** bu-rehxy
**Status:** Deferred — no code change this session
**Decision:** Defer the credential read/write cut-over until bead 7 (bu-akads) or
until a new credential type requires the new table.

---

## Background

PR #1790 (bu-uj3xv) shipped the `relationship.credentials` DDL (Alembic migration
`rel_016`). The PR reviewer noted that three callers write secured credential rows to
`public.entity_info` rather than the new `relationship.credentials` table. This bead
was filed to assess whether to migrate them now (dual-write shim or full migration) or
formally defer.

---

## The Three Callers

### 1. `src/butlers/google_credentials.py`

**Storage split:**
- App credentials (`client_id`, `client_secret`, `scopes`) → `butler_secrets` via
  `CredentialStore`.
- Refresh token → `public.entity_info` on the Google account's companion entity
  (`type='google_oauth_refresh'`, `secured=true`).

**Read pattern:** `_resolve_entity_refresh_token()` — `SELECT value FROM
public.entity_info WHERE entity_id=$1 AND type=$2`.

**Write pattern:** `_upsert_entity_refresh_token()` — `INSERT INTO public.entity_info
(entity_id, type, value, secured, is_primary) ... ON CONFLICT (entity_id, type) DO
UPDATE`.

**Delete pattern:** `_delete_entity_refresh_token()` — `DELETE FROM public.entity_info
WHERE entity_id=$1 AND type=$2`.

The refresh token is anchored to a `public.google_accounts.entity_id` companion entity,
NOT to a `public.contacts` contact or `public.contact_info` row.

### 2. `src/butlers/google_account_registry.py`

**Storage split:**
- Refresh token → `public.entity_info` on the Google account companion entity
  (`type='google_oauth_refresh'`, `secured=true`).

**Read pattern:** `_get_refresh_token()` — `SELECT value FROM public.entity_info WHERE
entity_id=$1 AND type='google_oauth_refresh'`.

**Write pattern:** `INSERT INTO public.entity_info (entity_id, type, value, secured,
is_primary)` during `create_google_account()` (upsert on conflict).

**Delete pattern:** `DELETE FROM public.entity_info WHERE entity_id=$1 AND
type='google_oauth_refresh'` during `disconnect_account()`.

### 3. `src/butlers/steam_account_registry.py`

**Storage split:**
- API key → `public.entity_info` on the Steam account companion entity
  (`type='steam_api_key'`, `secured=true`).

**Read pattern:** None — the API key is written at account creation/reconnect and read
back only via entity cascade (not by any direct `SELECT` in this file).

**Write pattern:** `INSERT INTO public.entity_info (entity_id, type, value, secured,
is_primary)` during `create_steam_account()` (new + revoked reconnect branches).

**Delete pattern:** Implicit — `DELETE FROM public.entities WHERE id=$1` (hard delete)
cascades to `entity_info`.

---

## Key Observation: These Callers Do Not Touch `public.contact_info`

The critical finding is that **none of the three callers read or write
`public.contact_info`**. They all use `public.entity_info`, which is the
entity-anchored credential table in the `public` schema.

This means:

1. The contacts → triples migration epic (bu-uhjxr) and its bead 7 read-path cut-over
   (bu-akads) **do not enumerate these callers in the reader inventory** (confirmed
   against the contact-migration read-path inventory, since retired — see git history). They
   were never part of the `public.contact_info` migration scope.

2. The `relationship.credentials` table (rel_016) was designed to receive rows
   currently stored in `public.contact_info WHERE secured=true`. The reconciler in
   `roster/relationship/jobs/relationship_jobs.py::run_contact_info_reconciler` already
   skips `secured=true` rows via `WHERE ci.secured=false` in SQL plus a defensive Python
   guard. These guards ensure `relationship.entity_facts` never receives secured rows.

3. The three callers write to `public.entity_info` — the **companion-entity** credential
   table, which predates and is separate from `public.contact_info`. They are on a
   different migration track entirely.

---

## Why Deferral Is Correct

### 1. Wrong migration track

These callers are not `public.contact_info` readers. Cutting them over is NOT required
by the bead 7 (bu-akads) read-path cut-over gate, which exclusively re-points
`public.contacts` / `public.contact_info` readers.

### 2. The table schema does not yet accommodate account registries

`relationship.credentials` has an `entity_id → public.entities FK`. The account
registries (`google_account_registry.py`, `steam_account_registry.py`) already use
companion entities in `public.entities`, so the FK is compatible in principle. However:

- The migration has no backfill for existing `public.entity_info` rows with `secured=true`.
- The bead bu-l6bb0 (open) explicitly tracks the gap between rows the backfill skips
  and rows that need to land in `relationship.credentials`. Migrating the three callers
  without resolving bu-l6bb0 would create an incomplete or inconsistent state.
- `relationship.credentials` grants only `butler_relationship_rw`. The three callers
  are NOT the relationship butler; wiring them to the new table requires a privilege
  review and possible MCP-tool intermediation per RFC 0006 schema isolation.

### 3. No production reader currently queries `relationship.credentials`

The new table is empty in production. There is no urgency to migrate writers until a
reader requires data to be there.

### 4. Credential paths are security-sensitive

`google_credentials.py` handles OAuth refresh tokens; `steam_account_registry.py`
handles Steam API keys. Both are `secured=true` secret material. Migration of write
paths for these credentials should happen in a dedicated, carefully reviewed bead — not
as a side-effect of a table-DDL assessment bead.

### 5. Premature migration creates dual-path risk

Adding a dual-write shim now (writing to both `public.entity_info` and
`relationship.credentials`) without a corresponding read-path cut-over means running two
write paths with no reader using the new table. This is net-negative complexity with
no benefit.

---

## Gating Conditions for Future Migration

This work should be taken on when **any** of the following is true:

1. **Bead 7 (bu-akads) is ready to begin:** The bead 7 cut-over is the natural moment
   to audit all credential readers and identify which ones can be served by
   `relationship.credentials`. The bu-akads scope should explicitly check whether
   the three callers here need to be included.

2. **bu-l6bb0 closes:** The backfill gap between what bead 5 skipped and what needs to
   land in `relationship.credentials` is resolved. Once the backfill path is clear,
   migrating the three callers becomes tractable.

3. **A new credential type is added that requires `relationship.credentials`:** For
   example, if a new butler module needs to store a credential and prefers the
   `relationship.credentials` table over `public.entity_info`, this is the right moment
   to batch the migration.

4. **RFC 0006 schema isolation is enforced for `public.entity_info`:** If a future
   RFC change restricts direct writes to `public.entity_info` (analogous to how
   `relationship.credentials` restricts access to `butler_relationship_rw`), migration
   becomes mandatory.

---

## Non-Impact Confirmation

- The three callers do **not** read `public.contact_info`.
- The contacts → triples migration (bu-uhjxr) does **not** need to touch these callers.
- The bead 7 read-path cut-over (bu-akads) reader inventory does **not** enumerate them.
- The reconciler's `secured=true` skip guards are already in place and tested
  (confirmed by `roster/relationship/tests/test_reconciler.py`).
- No secured rows from these callers will leak into `relationship.entity_facts`.

---

## Open Follow-Up Beads

- **bu-l6bb0** — backfill gap: secured rows skipped by bead 5 need to land in
  `relationship.credentials` (open, priority 3).
- **bu-akads** — bead 7 read-path cut-over (open, priority 0): should revisit whether
  the three callers here are in scope when that bead executes.
