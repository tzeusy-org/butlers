# Chronicler Health Facts Grant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Chronicler's least-privilege read access to `health.facts` after the Health memory schema creates it, so retained Google Health facts resume projection on the existing checkpoint.

**Architecture:** The grant belongs in the Health-targeted memory migration chain, not the core/bootstrap path: lifecycle runs core before memory, so `facts` does not exist when either an initial bootstrap or core migration runs. `mem_011` runs after `mem_001` creates `facts`, acts only when `current_schema() = 'health'`, and grants only `SELECT` to `butler_chronicler_rw`.

**Tech Stack:** Python 3.12, Alembic raw-SQL module migrations, PostgreSQL roles, pytest/testcontainers, Ruff.

## Global Constraints

- Preserve RFC 0006 schema isolation and RFC 0014's migration-tracked, read-only evidence-surface contract.
- Grant only `SELECT` on `health.facts` to `butler_chronicler_rw`; never use a broad schema/table grant.
- Run only in the Health memory target; other butlers' memory schemas must remain unaffected.
- Tolerate a missing role/table without blocking optional-schema startup.
- Do not issue an ad hoc live `GRANT`, delete facts, or reset the Chronicler checkpoint.
- Code changes ship by PR; post-merge deployment verification is separate from local tests.

---

### Task 1: Reproduce fresh-order failure with a real migration test

**Files:**

- Create: `tests/migrations/test_chronicler_health_facts_grant_migration.py`

**Interfaces:**

- Consumes: normal daemon ordering, core migration before the Health-targeted `memory` chain.
- Produces: a failing assertion that a fresh `health.facts` table lacks Chronicler `SELECT` before `mem_011` exists.

- [x] **Step 1: Write the failing fresh-order regression**

Provision a disposable database by running `core` against schema `health`, create the normal Chronicler role/schema usage prerequisite, then run the `memory` chain against `health`. Assert:

```python
assert privileges["can_select"] is True
assert privileges["can_insert"] is False
```

The unmodified chain fails because core runs before the memory migration creates `facts`.

- [x] **Step 2: Verify RED**

Run: `env -u VIRTUAL_ENV ./.venv/bin/python -m pytest tests/migrations/test_chronicler_health_facts_grant_migration.py -q`

Observed: three failures—missing `mem_011` for direct tests and no fresh-order `SELECT` grant.

### Task 2: Add a Health-targeted post-creation grant migration

**Files:**

- Create: `src/butlers/modules/memory/migrations/011_grant_chronicler_health_facts.py`
- Test: `tests/migrations/test_chronicler_health_facts_grant_migration.py`

**Interfaces:**

- Consumes: `current_schema()`, `health.facts`, and role `butler_chronicler_rw`.
- Produces: an idempotent, post-creation read grant only for the Health memory schema.

- [x] **Step 1: Implement the minimal migration**

Use `revision = "mem_011"`, `down_revision = "mem_010"`, and a guarded `DO` block:

```python
condition = (
    "current_schema() = 'health' "
    "AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_chronicler_rw') "
    "AND to_regclass('health.facts') IS NOT NULL"
)
```

Upgrade grants only `SELECT`; downgrade revokes only `SELECT`; both are no-ops outside the Health target or when prerequisites are absent.

- [x] **Step 2: Verify GREEN**

Run: `env -u VIRTUAL_ENV ./.venv/bin/python -m pytest tests/migrations/test_chronicler_health_facts_grant_migration.py -q`

Observed: `3 passed`.

### Task 3: Prove least privilege and real role access

**Files:**

- Modify: `tests/migrations/test_chronicler_health_facts_grant_migration.py`

**Interfaces:**

- Consumes: the new memory migration under a real PostgreSQL role.
- Produces: coverage for scope, idempotence, actual `SET ROLE` reads, denied writes, missing prerequisites, and downgrade.

- [x] **Step 1: Add behavior-executing ACL assertions**

Use a disposable health schema and role to prove:

```python
await conn.execute(f'GRANT USAGE ON SCHEMA health TO "{chronicler_role}"')
await conn.execute(f'SET ROLE "{chronicler_role}"')
assert await conn.fetchval("SELECT COUNT(*) FROM health.facts") == 0
```

Assert `INSERT`, `UPDATE`, and `DELETE` privileges remain false. Run the same migration in `general` first and assert it grants nothing there.

- [x] **Step 2: Verify targeted tests**

Run: `env -u VIRTUAL_ENV ./.venv/bin/python -m pytest tests/migrations/test_chronicler_health_facts_grant_migration.py -q`

Observed: `3 passed`.

### Task 4: Verify, review, and prepare the PR

**Files:**

- Create: `src/butlers/modules/memory/migrations/011_grant_chronicler_health_facts.py`
- Create: `tests/migrations/test_chronicler_health_facts_grant_migration.py`
- Modify: `docs/superpowers/plans/2026-08-27-chronicler-health-facts-grant.md`

**Interfaces:**

- Consumes: completed migration and regression coverage.
- Produces: a reviewable PR ready for post-merge runtime verification.

- [x] **Step 1: Run targeted and chain validation**

Run:

```bash
env -u VIRTUAL_ENV ./.venv/bin/python -m pytest \
  tests/migrations/test_chronicler_health_facts_grant_migration.py \
  tests/config/test_migrations.py::test_core_migration_smoke_empty_to_head \
  tests/config/test_schema_standin_parity.py -q
uv run ruff check src/butlers/modules/memory/migrations/011_grant_chronicler_health_facts.py \
  tests/migrations/test_chronicler_health_facts_grant_migration.py
uv run ruff format --check src/butlers/modules/memory/migrations/011_grant_chronicler_health_facts.py \
  tests/migrations/test_chronicler_health_facts_grant_migration.py
make test-qg
```

Observed: targeted memory-migration tests, memory-chain idempotence, Ruff, and
diff hygiene pass. `make test-qg` reaches an unrelated, reproducible baseline
failure in `test_redelivery_across_expired_cooldown_upserts_not_crashes`: its
fixed expiry passed the wall clock on 2026-08-27. The same isolated test fails
on unchanged `main`; filed separately as `bu-hzemz`.

- [x] **Step 2: Review and record evidence**

Ran `git diff --check`, two independent ACL reviews, and recorded the revised
post-creation decision in Bead `bu-nm0ao`.

- [ ] **Step 3: Commit, push, and open a PR**

Commit the migration and tests with no session URL in commit/PR metadata. Push `fix/chronicler-health-facts-grant`, open a PR against `main`, and wait for terminal hosted checks before any merge/deploy decision.

## Self-Review

- Fresh-order coverage proves the failure that bootstrap/core-only tests miss.
- The grant is constrained to a named table, one reader role, and the Health memory target.
- No data, checkpoint, connector, or OAuth behavior changes.
