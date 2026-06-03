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

1. **`public.contact_info` dropped** — migration authored; the drop is enforced
   by `core_115` and self-guards against loss. ✅ (code) / ⏳ (execution, see below)
2. **`public.contacts` channel-identity columns removed** — **N/A on the real
   schema**: `public.contacts` has no channel-*identity* columns. The only
   channel-adjacent column is `preferred_channel`, a routing *preference*
   (`telegram`|`email`) still read/written by the relationship API — it is **not**
   an identity and is intentionally retained.
3. **Backup retention ≥ 90 days** — `core_115` snapshots to
   `public.contact_info_dropbak_core_115`; the pre-migration snapshots from bead 1
   (`contact_info_pre_migration_*`) are likewise retained. Operator must keep both
   ≥ 90 days from the drop date.
4. **Final reconciliation recorded** — see the execution checklist below; row
   counts (in vs dropped) are recorded by the operator at actual drop time.
5. **Gated on bead 9 sign-off (bu-hpv4u)** — closed; the parity guard is the
   in-migration enforcement of its precondition (*backfill applied + gap = 0*).

## Remaining operational gate (NOT done in this PR — owner-approved, irreversible)

The actual `DROP TABLE` against **production** data is a separate dated decision and
is not performed here. Before/at execution, the operator must:

1. Re-run the backfill `--apply` on production so all mapped rows land in
   `entity_facts`; approve the owner-entity `pending_actions`; disposition the
   null-entity orphans and any `google_health`/secured residue.
2. Confirm a fresh zero-loss verification (the `core_115` parity guard does this
   automatically and aborts if gap > 0).
3. Apply the migration (`alembic upgrade core@head`). Record the pre-drop row count
   (`SELECT count(*) FROM public.contact_info`) and confirm the snapshot row count
   matches — append both numbers here.
4. Retain `contact_info_dropbak_core_115` + bead-1 snapshots ≥ 90 days.

> **Final reconciliation (fill in at execution):**
> rows in `public.contact_info` immediately before drop = `<N>`;
> rows in `public.contact_info_dropbak_core_115` = `<N>`; parity gap = `0`;
> drop date = `<YYYY-MM-DD>`; snapshot prune-after = `<drop date + 90d>`.

## Follow-ups

- bu-u1mw8 (doctrine): update `about/heart-and-soul/v1.md` to reflect the post-drop
  reality (entity-facts-backed contacts). Unblocked by this bead.
- The project `CLAUDE.md` "Database Isolation" section still describes
  `public.contact_info` as current; fold into the doctrine update.
