## Why

Every butler runtime role and `connector_writer` holds `SELECT, INSERT, UPDATE, DELETE` on every `public` table. `scripts/init-db.sql` grants it on each bootstrap rerun (`GRANT ... ON ALL TABLES IN SCHEMA public`, L381 and L598) and through `ALTER DEFAULT PRIVILEGES` for tables created later (L409 and L637). The write matrix in `database-security` describes a small, targeted set (core_065), but the effective ACL is far broader, so the matrix understates what a confined runtime role can write.

The owner accepted that risk for `public.qa_patrols` (bu-w24vr3 option B, recorded in the spec by PR #4316) and asked for a systematic review before any narrowing. The owner approved an audit-first split on 2026-10-03 (bu-yfd9v1): this change is the audit and proposal. Narrowing is a security-posture decision that belongs to the owner, and a `REVOKE` in a migration alone cannot hold, because the next `init-db.sql` run re-grants it.

## What Changes

This change is documentation and proposal only. It changes no grant, no ACL, no migration, no `init-db.sql` line and no code.

- `inventory.md`: a `public` table x runtime-role writer inventory, derived from the migration chain, `init-db.sql` and a static scan of `src/` and `roster/`, with `file:line` evidence, `qa_patrols` first. Where static evidence is insufficient the inventory says so instead of guessing.
- `design.md`: the three narrowing mechanisms the repo already uses, why a migration `REVOKE` does not persist, the constraints any narrowing must meet (including the `src/butlers/api/routers/qa.py` synthetic-finding writer), and a recommendation plus alternatives per candidate table.
- `tasks.md`: proposed follow-up beads, as text. None are filed. They are filed only after the owner approves a posture per table.
- A `database-security` delta with ADDED requirements that hold for any narrowing the owner later approves. They adopt no per-table posture.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-security`: adds requirements for how a narrowing must persist and what it must preserve. No existing requirement or scenario is modified.

## Impact

- Files under `openspec/changes/audit-public-table-write-grants/` only. No runtime, schema or test change; test delta is +0 ~0 -0.
- Findings that need an owner or reconciliation follow-up, not fixed here: the matrix in `database-security` lists `public.contacts`, `public.contact_info` and `public.facts`, which no longer exist as public tables or have no public definition; and its "Read-only public tables" scenario contradicts the bootstrap grants. See `inventory.md` section 0.
