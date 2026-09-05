> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** The plan explicitly created and shipped the bounded-availability change.
> **Successor:** `openspec/changes/bound-secrets-inventory-availability`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Bounded Secrets Inventory Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GET /api/secrets/inventory` return an honest, content-blind response within ten seconds even when one credential source or its audit enrichment is slow.

**Architecture:** Preserve the existing inventory wire shape and content-blind projections. Replace the audit window query with index-backed per-target top-N lookups, then run the per-butler reads under a bounded concurrent fan-out. A timed-out source contributes no rows, is named in `meta.sources_degraded`, and cannot cause the passport to make an all-clear claim.

**Tech Stack:** FastAPI, asyncio/asyncpg, PostgreSQL `(target, ts DESC)` index, React/TypeScript, Vitest, pytest, OpenSpec.

## Global Constraints

- Create a new OpenSpec change named `bound-secrets-inventory-availability`; add a unique `### Requirement: Secrets Inventory Availability and Bounded Source Reads` block. Do **not** add a second `MODIFIED Requirements` block for `Secrets Inventory and Per-Credential Read Endpoints`, because `project-secret-read-endpoints-content-blind` already owns that unarchived block.
- Preserve every existing inventory field, owner/default-primary-Google semantics, deduplicated count semantics, `audit[]` presence, and explicit content-blind projections. Raw secret values, OAuth scopes, persisted user types/labels, probe messages, and audit notes remain absent from response bytes.
- Use an endpoint-wide 10-second budget, a 3-second per-source budget, and at most 6 simultaneous source reads. These bounds stay below the browser's 15-second request deadline.
- A failed or timed-out credential/audit source contributes no rows. Name it in `meta.sources_degraded` in configured source order; `shared-public` represents the shared system/user/CLI bundle. Counts describe only surviving rows and the UI must call the inventory incomplete rather than all-clear. Existing absent-probe semantics remain best-effort.
- Do not add a migration, cache, audit retention change, credential mutation, or frontend timeout increase. `ix_audit_log_target_ts (target, ts DESC)` already exists.

---

### Task 1: Define the bounded-inventory availability contract

**Files:**
- Create: `openspec/changes/bound-secrets-inventory-availability/proposal.md`
- Create: `openspec/changes/bound-secrets-inventory-availability/design.md`
- Create: `openspec/changes/bound-secrets-inventory-availability/tasks.md`
- Create: `openspec/changes/bound-secrets-inventory-availability/specs/dashboard-api/spec.md`

**Interfaces:**
- Consumes: `openspec/specs/dashboard-api/spec.md` requirement `Secrets Inventory and Per-Credential Read Endpoints`; active `project-secret-read-endpoints-content-blind` delta.
- Produces: a unique added availability requirement that later implementation and tests cite.

- [x] **Step 1: Write the new requirement before code**

Add an `## ADDED Requirements` block with this requirement heading and scenarios:

```markdown
### Requirement: Secrets Inventory Availability and Bounded Source Reads

`GET /api/secrets/inventory` SHALL complete its source reads within a server-side
budget below the dashboard client's request timeout. It SHALL retain rows only
from sources whose credential and audit evidence completed within their source
budget; existing absent-probe semantics remain best-effort.

#### Scenario: A healthy inventory remains complete
- **WHEN** every configured butler source and the shared credential source complete
- **THEN** the response retains the existing inventory shape and omits
  `meta.sources_degraded`

#### Scenario: A slow source produces an honest partial inventory
- **WHEN** a configured source exceeds its source-read budget
- **THEN** that source contributes no rows, its stable name appears in
  `meta.sources_degraded`, and counts are computed only from returned rows
- **AND** the response contains no database error text, secret value, probe
  message, audit note, raw scope, persisted user type, or persisted user label

#### Scenario: Partial zero is not an all-clear
- **WHEN** `meta.sources_degraded` is non-empty
- **THEN** the passport names the unavailable sources and does not assert that
  every credential is accounted for
```

- [x] **Step 2: Record the collision guard**

In `design.md`, state that the new change adds a distinct requirement rather
than rewriting the active content-blind requirement. Before any future archive,
run:

```bash
rg -l '^### Requirement: Secrets Inventory and Per-Credential Read Endpoints$' \
  openspec/changes/*/specs/*/spec.md
```

If more than one result exists, rebuild the remaining modified block against
the refreshed baseline before archiving.

- [x] **Step 3: Validate the new delta**

Run:

```bash
openspec validate bound-secrets-inventory-availability --strict
```

Expected: exit 0 with no malformed-delta errors.

### Task 2: Make audit evidence a true index-backed top-N read

**Files:**
- Modify: `src/butlers/api/routers/secrets_v2.py:1525-1574`
- Modify: `tests/api/test_secrets_v2_inventory.py:2738-2770`
- Modify: `tests/migrations/test_audit_log_index_perf.py:138-207`

**Interfaces:**
- Consumes: `_fetch_audit_bulk(pool, targets, limit=3, raise_on_failure=False)`.
- Produces: the same `dict[str, list[dict]]` with newest-first rows, but with at most `limit` index-seeked rows per distinct target.

- [x] **Step 1: Add failing unit assertions for the query contract**

Extend `test_fetch_audit_bulk_returns_recent_rows_per_target_newest_first` to
assert the SQL passed to `pool.fetch` contains `CROSS JOIN LATERAL`,
`ORDER BY ts DESC`, and `LIMIT $2`, and does not contain `ROW_NUMBER`. Add a
duplicate target input and assert the second bind is the limit while the target
bind is deduplicated.

- [x] **Step 2: Run the focused test red**

Run:

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py \
  -k fetch_audit_bulk_returns_recent_rows_per_target_newest_first -q
```

Expected: FAIL because the current SQL uses `ROW_NUMBER()` rather than a
per-target lateral top-N lookup.

- [x] **Step 3: Replace the ranking query**

Deduplicate targets in Python while preserving first-seen order. Replace the
window query with this shape:

```sql
WITH requested_targets AS (
    SELECT DISTINCT requested.target
    FROM unnest($1::text[]) AS requested(target)
)
SELECT audit.target, audit.ts, audit.actor, audit.action, audit.note
FROM requested_targets
CROSS JOIN LATERAL (
    SELECT target, ts, actor, action, note
    FROM public.audit_log
    WHERE target = requested_targets.target
    ORDER BY ts DESC
    LIMIT $2
) AS audit
ORDER BY audit.target, audit.ts DESC
```

Keep the existing exception policy and internal `note` value; only the
content-blind projection decides what reaches a response.

- [x] **Step 4: Add the production-shaped performance proof**

In `test_audit_log_index_perf.py`, seed a hot target plus several cold targets
and run `EXPLAIN (ANALYZE, FORMAT TEXT)` on the lateral multi-target query with
`limit=3`. Assert no `Seq Scan`, presence of `ix_audit_log_target_ts`, and
execution below the existing 50ms threshold.

- [x] **Step 5: Verify green**

Run:

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py \
  -k fetch_audit_bulk -q
uv run pytest tests/migrations/test_audit_log_index_perf.py -m perf -q
```

Expected: focused helper tests pass; the opt-in Docker performance test proves
the multi-target plan uses the existing composite index.

### Task 3: Bound the inventory fan-out and preserve truthful partial state

**Files:**
- Modify: `src/butlers/api/routers/secrets_v2.py:2605-2745`
- Modify: `tests/api/test_secrets_v2_inventory.py:680-887`

**Interfaces:**
- Consumes: `DatabaseManager.butler_names`, `DatabaseManager.pool(name)`,
  `DatabaseManager.credential_shared_pool()`, `_fetch_system_secrets`,
  `_fetch_user_secrets`, `_fetch_cli_secrets`, `_fetch_identity_info`, and
  `DegradedSources`.
- Produces: the same `ApiResponse[InventoryData]`; `meta.sources_degraded`
  gains stable timeout/failure names for omitted sources.

- [x] **Step 1: Add deterministic red tests**

Add these tests to `tests/api/test_secrets_v2_inventory.py`:

```python
async def test_inventory_reads_butler_sources_concurrently():
    """Two source reads pass a shared barrier; a serial loop cannot finish."""

async def test_inventory_timeout_omits_only_the_slow_source(monkeypatch):
    """A timed-out source returns no rows and is named in stable meta order."""

def test_inventory_partial_zero_does_not_suppress_degraded_meta():
    """A fully omitted source remains observable even when returned counts are zero."""
```

Use `asyncio.Event` barriers instead of elapsed-time assertions. Monkeypatch
the module's source timeout to a small test value; one never-released pool must
not prevent a healthy pool's row from returning, and the response meta must
name only the slow pool.

- [x] **Step 2: Run the focused tests red**

Run:

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py \
  -k 'inventory_reads_butler_sources_concurrently or inventory_timeout_omits_only_the_slow_source or inventory_partial_zero' -q
```

Expected: FAIL because `get_inventory` currently awaits each butler source
serially and has no timeout path.

- [x] **Step 3: Add bounded source wrappers**

Define module constants:

```python
_INVENTORY_SOURCE_TIMEOUT_S = 3.0
_INVENTORY_REQUEST_BUDGET_S = 10.0
_INVENTORY_MAX_CONCURRENT_SOURCES = 6
```

Implement one local coroutine for a per-butler source and one for the shared
source bundle. Gate every source with a semaphore of six and
`asyncio.wait_for(..., timeout=_INVENTORY_SOURCE_TIMEOUT_S)`. Return a typed
source result rather than writing to `DegradedSources` inside concurrent tasks.
After `asyncio.gather`, append successful rows in `db.butler_names` order and
mark failures/timeouts in that same order; append `shared-public` last when
the shared bundle fails. Wrap the complete task set in a 10-second timeout;
cancel unfinished tasks, await their cancellation, and mark their source names.

Treat failed audit enrichment as a failed source: omit that source's rows
rather than publishing an empty `audit[]` as though it were complete. Retain
the existing best-effort semantics for absent probe history and the existing
`UndefinedTableError` classification as a legitimate absent schema, not
degraded state.

- [x] **Step 4: Verify backend behavior and content blindness**

Run:

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py -q
uv run ruff check src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
uv run ruff format --check src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
```

Expected: all inventory tests pass, including the existing byte-level sentinels
that reject raw audit notes, probe messages, scopes, labels, and credential
values.

### Task 4: Make a partial inventory visibly incomplete

**Files:**
- Modify: `frontend/src/components/secrets/passport/DirectionPassport.tsx:297-405`
- Modify: `frontend/src/components/secrets/passport/secrets-fe5.test.tsx:650-710`
- Modify: `frontend/src/pages/SecretsPage.test.tsx:398-417`

**Interfaces:**
- Consumes: existing `InventoryResponse.sourcesDegraded` and the existing
  `SecretsPage` `SourceDegradedNote`.
- Produces: an incomplete-inventory headline while retaining the named source
  banner and all existing healthy/failure headline behavior.

- [x] **Step 1: Add a failing rendering test**

Create an inventory fixture with empty credential families, zero server counts,
and `sourcesDegraded: ["finance"]`. Assert the rendered passport contains
`Credential inventory incomplete.` and does not contain
`Every credential, accounted for.`. Keep the existing page test asserting the
banner names `finance`.

- [x] **Step 2: Run the frontend test red**

Run:

```bash
npm --prefix frontend run test -- --run \
  src/components/secrets/passport/secrets-fe5.test.tsx \
  src/pages/SecretsPage.test.tsx
```

Expected: FAIL because `DirectionPassport` currently treats a partial zero as
an all-clear.

- [x] **Step 3: Implement the headline gate**

Derive `const inventoryIncomplete = (inventory.sourcesDegraded?.length ?? 0) > 0`.
Render `Credential inventory incomplete.` when true, before the normal
failure-count headline branches. Do not alter count values, hide the existing
banner, or add raw diagnostic text.

- [x] **Step 4: Verify green**

Run:

```bash
npm --prefix frontend run test -- --run \
  src/components/secrets/passport/secrets-fe5.test.tsx \
  src/pages/SecretsPage.test.tsx
npm --prefix frontend run lint
npm --prefix frontend run knip
```

Expected: the partial view has both an incomplete headline and named banner;
the normal all-clear remains for a complete zero inventory.

### Task 5: Verify the exact branch and live diagnostic path

**Files:**
- Modify: none

**Interfaces:**
- Consumes: the branch's `GET /api/secrets/inventory` implementation and the
  dev Compose launcher.
- Produces: evidence that the endpoint responds before the browser deadline
  without inspecting secret response content.

- [x] **Step 1: Run the focused quality gates**

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py -q
npm --prefix frontend run lint
npm --prefix frontend run knip
npm --prefix frontend run build
git diff --check
```

- [x] **Step 2: Run the content-blind regression checks**

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py \
  -k 'content_blind or omit_every_probe_and_audit_sentinel' -q
```

- [ ] **Step 3: Verify a deployed dev candidate only after it is authorized**

Use a body-discarding request, never print the inventory response:

```bash
curl --noproxy '*' -sS -o /dev/null -w 'HTTP %{http_code} in %{time_total}s\n' \
  --max-time 12 http://127.0.0.1:42200/api/secrets/inventory
```

Expected: HTTP 200 before 10 seconds. Confirm `/butlers-dev/secrets` shows
normal data, or its named incomplete state when a source is deliberately
delayed. Do not restart, change credentials, or merge/deploy without separate
authorization.

## Plan Self-Review

- Spec coverage: Task 1 adds the availability contract; Tasks 2-3 retain exact
  inventory and content-blind data semantics; Task 4 prevents a partial zero
  all-clear; Task 5 verifies the real route without reading secrets.
- Placeholder scan: every task names its files, tests, and expected condition;
  no deferred implementation markers remain.
- Type consistency: the existing `meta.sources_degraded` →
  `InventoryResponse.sourcesDegraded` → `SecretsPage` path stays unchanged;
  backend source names remain strings and no frontend API type change is needed.
