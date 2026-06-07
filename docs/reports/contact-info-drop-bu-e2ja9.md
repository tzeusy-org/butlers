# `public.contact_info` Drop — Migration Bead 10 (bu-e2ja9)

- **Bead:** bu-e2ja9 (migration bead 10; parent epic bu-uhjxr)
- **Spec anchor:** Brief §6b Amendment 1.1.A.6 (drop is a separate dated decision;
  backups retained ≥ 90 days) + Amendment 1.1.C bead 10.
- **Gating report:** `docs/reports/contact-migration-postmortem-2026-05-31.md` (bead 9, bu-hpv4u).
- **Migration:** `alembic/versions/core/core_115_drop_contact_info.py`

## What this change ships (code, reversible until applied)

| Area | File | Change |
|---|---|---|
| Drop migration | `alembic/.../core_115_drop_contact_info.py` | Self-guarding drop of `public.contact_info` (snapshot → parity-guard → drop). |
| Cross-chain guard | `roster/relationship/migrations/019_prefix_telegram_has_handle.py` | `to_regclass` guard so it no-ops when `contact_info` is already dropped (order-independent across chains). |
| Sync matcher | `src/butlers/modules/contacts/backfill.py` | `_match_email` / `_match_phone` re-pointed from `public.contact_info` to `relationship.entity_facts` (`has-email` / `has-phone` → `public.contacts.entity_id`). This was an **uncovered live read** not in bu-l5w8a's 6-file scope. |
| Reconciler retirement | `roster/relationship/butler.toml`, `src/butlers/scheduled_jobs.py`, `roster/relationship/jobs/relationship_jobs.py` | Removed the `contact_info_reconciler` cron entry + job-registry handler; added a `to_regclass` retirement guard to the impl so a stale dispatch is a crash-safe no-op. |
| One-shot scripts | `backfill_contact_info_triples.py`, `contact_migration_snapshot.py`, `contact_backfill_triples.py`, `contact_backfill_credentials.py` | `SUPERSEDED` banners (retained as migration audit trail; not auto-run). |
| Tests | `tests/migrations/test_drop_contact_info.py` (new), `tests/jobs/test_reconciler_empty_value_guard.py`, `tests/config/test_schema_acl_isolation.py`, `roster/relationship/tests/test_reconciler.py` | New unit + integration coverage for the parity guard; fixture/assertion updates for the retired reconciler and the dropped table. |

## Self-guarding migration design

`core_115.upgrade()` will **refuse to drop** if it cannot prove zero data loss:

1. **Snapshot** `public.contact_info` → `public.contact_info_dropbak_core_115`
   (full copy) — recoverable for the 90-day window (AC#3 / Amendment 1.1.A.6).
2. **Parity guard** — counts non-secured, entity-linked rows whose `type` maps to a
   channel predicate but which have **no matching active triple** in
   `relationship.entity_facts` (mirrors the proven `run_contact_info_reconciler`
   sweep, incl. the `telegram:` object prefix). Owner-accepted unmapped types
   (default `google_health`, env-overridable) are excluded. Non-zero → **raise**.
   - If `relationship.entity_facts` is absent (core chain provisioned in isolation),
     it only proceeds when `contact_info` is empty (a fresh DB has nothing to lose).
3. **Drop** only once parity is clean.

Operator override: `CONTACT_INFO_DROP_FORCE=1` bypasses the raise **only** with
explicit owner sign-off to accept residual loss (the snapshot is still taken).

`downgrade()` recreates the table DDL (core_002 + core_083 `context`) and restores
rows from the snapshot when present.

## Acceptance criteria status

1. **`public.contact_info` dropped** — ✅ **executed and verified** on the live
   host (`butlers-db-dev`) via `core_115`; ran through the self-guard (parity 0, no
   force). See *Execution* below.
2. **`public.contacts` channel-identity columns removed** — **N/A on the real
   schema**: `public.contacts` has no channel-*identity* columns. The only
   channel-adjacent column is `preferred_channel`, a routing *preference*
   (`telegram`|`email`) still read/written by the relationship API — it is **not**
   an identity and is intentionally retained.
3. **Backup retention ≥ 90 days** — `core_115` snapshots to
   `public.contact_info_dropbak_core_115`; the pre-migration snapshots from bead 1
   (`contact_info_pre_migration_*`) are likewise retained. Operator must keep both
   ≥ 90 days from the drop date.
4. **Final reconciliation recorded** — ✅ see *Execution* below: 872 rows
   snapshotted, retroactive parity gap = 0, zero data loss.
5. **Gated on bead 9 sign-off (bu-hpv4u)** — closed; the parity guard is the
   in-migration enforcement of its precondition (*backfill applied + gap = 0*).

## Execution (completed — live host `butlers-db-dev`)

> **Host note.** The active system is `butlers-db-dev` (`.env.dev`) — it holds all
> the data and ran the full migration. The host named `butlers-db` (`.env.prod`)
> is empty and far behind (`core_045`, no relationship chain); it is **deferred**
> (bu-qs8sp) and the drop was **not** run there. See bu-e2ja9 close notes.

Sequence performed:

1. Backfill `--apply` (idempotent) → 866 rows already present; the 4 remaining
   gaps were the owner entity's own channels, parked as `pending_actions` per the
   RFC 0017 §2.3 owner carve-out.
2. Owner approved those 4 actions → channel triples became active (parity → 0).
3. `core_115` applied (`alembic upgrade core@head`) — it snapshotted
   `public.contact_info` → `public.contact_info_dropbak_core_115`, the parity
   guard passed at gap 0 (no `CONTACT_INFO_DROP_FORCE`), and dropped the table.
4. Stale owner `pending_actions` (now redundant — triples active) transitioned to
   `executed` during reconciliation.

> **Final reconciliation:**
> rows in `public.contact_info_dropbak_core_115` (snapshot of the dropped table) = **872**;
> retroactive parity gap (non-secured, entity-linked, mapped rows lacking an active
> triple) = **0** → zero data loss;
> active `has-*` triples = **872**; `google_health` unmapped (owner-accepted) = **2**;
> secured/orphan/empty-value = **0**;
> core alembic revision = **`core_115`**;
> drop applied ≈ **2026-06-04/05** (after PR #2087 merged 2026-06-03); verified
> **2026-06-07**; snapshot retained ≥ 90 days (prune-after ≈ **2026-09-07**).

## Follow-ups

- **bu-qs8sp** (deferred per owner): the `butlers-db` host is empty/at `core_045`;
  deploy the full migration chain there if it is to become real production.
- bu-u1mw8 (doctrine): update `about/heart-and-soul/v1.md` to reflect the post-drop
  reality (entity-facts-backed contacts). Unblocked (epic bu-uhjxr closed).
- The project `CLAUDE.md` "Database Isolation" section still describes
  `public.contact_info` as current; fold into the doctrine update.
