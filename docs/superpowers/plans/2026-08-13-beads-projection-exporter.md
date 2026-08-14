# Beads Projection Exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a minimal active Beads snapshot from the tracker host to
PostgreSQL and have Decisions consumers read one atomic, source-honest snapshot
through `BeadReadProvider`.

**Architecture:** A deterministic tracker-host exporter is the only tracker
reader. It normalizes an allowlisted active snapshot, publishes candidate rows
and the active pointer in one PostgreSQL transaction, and retains only three
complete snapshots plus categorical failed-run metadata. Runtime consumers use
a repeatable-read, read-only provider; the existing decision classifier,
dependency escalation, and lint semantics remain pure consumer logic.

**Tech Stack:** Python 3.12, asyncpg, PostgreSQL, Alembic core chain, existing
Beads JSONL export/linter, FastAPI, React/Vite/Vitest, Docker/testcontainers.

## Global Constraints

- Beads/Dolt remains the sole authoritative tracker. The selected JSONL file is
  a derived compatibility and rollback path only, never a second tracker
  authority. Runtime code must never call `bd`, Dolt, GitHub, or expose a
  general tracker API.
- Project only active `open`, `in_progress`, and `blocked` records; retain no
  raw notes, comments, history, metadata, attachments, export bytes,
  credentials, host paths, raw linter output, or raw error text.
- Retain a decision description only for an eligible non-epic `decision`-
  labeled record. Retain typed normalized options/default and a categorical
  structured-detail result, never raw metadata.
- Writer transport requires `sslmode=verify-full`, a trusted CA bundle, server
  peer-certificate and configured-hostname verification, and TLS 1.2 or newer;
  missing or unverifiable configuration fails closed before export, connection,
  or publication and never falls back to plaintext, system trust, another
  hostname, or an unverified peer. Runtime reader roles receive only bounded
  active-view access. Prove all grants using actual `SET ROLE` tests; do not
  rely on an administrator test pass. CA and credential provisioning remain
  separately owner-gated.
- Enforce `MAX_BEAD_ID_CHARS = 128`, `MAX_ISSUE_TITLE_CHARS = 512`,
  `MAX_STATUS_CHARS = 16`, `MAX_ISSUE_TYPE_CHARS = 64`,
  `MAX_TIMESTAMP_CHARS = 64`, `MAX_LABELS_PER_ISSUE = 32`,
  `MAX_LABEL_CHARS = 128`, `MAX_DECISION_DESCRIPTION_CHARS = 16_384`,
  `MAX_OPTIONS_PER_DECISION = 16`, `MAX_DECISION_OPTION_CHARS = 512`,
  `MAX_DEPENDENCY_TYPE_CHARS = 64`, `MAX_LINT_CATEGORY_CODE_CHARS = 64`,
  `MAX_CATEGORICAL_REASON_CHARS = 128`, `MAX_PRODUCER_VERSION_CHARS = 64`,
  `MAX_SNAPSHOT_ISSUES = 10_000`, `MAX_SNAPSHOT_DEPENDENCY_EDGES = 25_000`,
  and `MAX_SNAPSHOT_LINT_VIOLATIONS = 1_000`; priority is `0..4`, timestamps
  are valid RFC 3339 within their bound, and a digest is 64 lowercase hex
  characters. Any overflow rejects the entire candidate with categorical
  `validation_failed` / `field_bound_exceeded`, no truncation or partial rows,
  and the active pointer unchanged.
- Publish pointer and candidate rows atomically. Read them in one
  `REPEATABLE READ, READ ONLY` transaction and fail closed on a mixed snapshot.
- Require source-completeness evidence from the same source watermark for every
  candidate: a source-complete tracker snapshot identity, authoritative active
  count, and canonical active-record digest must exactly match the candidate
  active count/digest. Retain only a bounded watermark digest. An empty or
  count-regressed candidate without that evidence records
  `source_completeness_unverified`, leaves the pointer unchanged, and returns an
  unavailable envelope rather than an all-clear. Its singleton availability
  override remains sticky until a later source-complete publication clears it;
  a later failed run cannot clear it. This is exact consistency, not an
  arbitrary numerical regression threshold; regression coverage must prove it.
  The source-side read, not a recount/digest of the staged candidate, must bind
  the watermark/count/digest at one consistency point.
- Freshness: target `<=5m`, observable target miss `>5m..<=10m`, warning
  `>10m..<=15m`, hard unavailable `>15m`.
- Retain active plus two prior complete snapshots; retain categorical failed
  run metadata for 30 days.
- Shadow against explicit JSONL mode for 14 full consecutive days. Projection
  cutover and seven-day explicit JSONL rollback require separate owner
  authorization. JSONL retirement is a separate later authorization.
- Do not install a service, create/use a credential, execute a migration,
  change network/firewall state, deploy, cut over, or mutate Beads until the
  corresponding operational gate is explicitly granted.

## File Structure

| File | Responsibility |
|---|---|
| `alembic/versions/core/<allocated-next-core-revision>_beads_projection.py` | Core migration allocated from the exact rebased core-chain head at implementation time: schema, private tables, active reader views, role boundaries, and constraints. This planning packet reserves neither its revision identifier nor its predecessor. |
| `src/butlers/core/beads_projection.py` | Typed snapshot model, source selector, async `BeadReadProvider`, atomic active-view reader, freshness classifier, and pure JSONL compatibility adapter. |
| `scripts/beads_projection_exporter.py` | Tracker-host-only deterministic candidate parser, lint normalizer, advisory-lock publisher, retention, and bounded run reporting. Must carry PEP 723 metadata. |
| `src/butlers/jobs/decision_review.py` | Source-agnostic decision calculation and lint/attention integration; no direct runtime parser after cutover. |
| `src/butlers/api/routers/decisions.py` | Shared provider read and additive response provenance. |
| `src/butlers/api/models/decision.py` | API metadata/type contract for source, snapshot time, and freshness. |
| `src/butlers/api/models/__init__.py` | Verify whether its deliberately extensible `ApiMeta` needs no change; keep endpoint-specific provenance typed in the Decisions API/frontend contracts. |
| `frontend/src/api/types.ts` | Typed additive Decisions metadata. |
| `frontend/src/pages/DecisionsPage.tsx` | Source-as-of plaque and warning rendering without changing decision authority. |
| `tests/core/test_beads_projection.py` | Provider, atomicity, freshness, and source-selection unit coverage. |
| `tests/scripts/test_beads_projection_exporter.py` | Parser, allowlist, lint, publication, retention, and categorical failure coverage. |
| `tests/migrations/test_beads_projection_migration.py` | Migrated PostgreSQL schema, transaction, retention, and `SET ROLE` proof. |
| `tests/jobs/test_decision_review.py` | JSONL/projection semantic parity and no-all-clear coverage. |
| `tests/api/test_decisions.py` | API provider provenance, warning, hard-unavailable, and summary compatibility coverage. |
| `frontend/src/pages/DecisionsPage.test.tsx` | Warning/source plaque and degraded UI coverage. |
| `frontend/src/pages/DecisionsPage.a11y.test.tsx` | Accessible warning/degraded rendering coverage. |
| `docs/operations/beads-projection-exporter.md` | Owner-gated provisioning, shadow, cutover, rollback, alerts, and evidence runbook. |

## Interfaces Locked Before Implementation

```python
# src/butlers/core/beads_projection.py
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

Freshness = Literal["fresh", "warning", "unavailable"]
Source = Literal["jsonl", "projection"]

@dataclass(frozen=True)
class BeadIssue:
    id: str
    title: str
    status: Literal["open", "in_progress", "blocked"]
    issue_type: str | None
    priority: int | None
    created_at: datetime | None
    updated_at: datetime | None
    due_at: datetime | None
    labels: tuple[str, ...]
    decision_description: str | None
    decision_options: tuple[str, ...] | None
    decision_default: str | None
    structured_details_available: bool
    structured_details_unavailable_reason: str | None

@dataclass(frozen=True)
class BeadDependency:
    issue_id: str
    depends_on_id: str
    type: str
    created_at: datetime

@dataclass(frozen=True)
class BeadLint:
    status: Literal["clean", "violations", "unavailable"]
    unavailable_reason: str | None
    violations: tuple[dict[str, str], ...]

@dataclass(frozen=True)
class BeadSnapshot:
    available: bool
    source: Source
    snapshot_id: str | None
    completed_at: datetime | None
    source_exported_at: datetime | None
    freshness: Freshness
    target_met: bool | None
    unavailable_reason: str | None
    issues: tuple[BeadIssue, ...]
    dependencies: tuple[BeadDependency, ...]
    lint: BeadLint

class BeadReadProvider(Protocol):
    async def read_active(self, *, now: datetime | None = None) -> BeadSnapshot: ...
```

The provider returns an unavailable `BeadSnapshot` for every reader, schema,
or freshness failure. It never uses an empty `issues` tuple to mean source
failure. The pure decision calculation consumes only `BeadSnapshot`, so JSONL
and projection adapters must converge before cutover. `target_met` is `true`
at or below five minutes, `false` for a readable target miss, and `null` when
the source is unavailable, so the ten-minute warning is not the first
observable freshness signal.

---

### Task 1: Establish source-agnostic decision fixtures and RED parity tests

**Files:**

- Create: `tests/core/test_beads_projection.py`
- Modify: `tests/jobs/test_decision_review.py`
- Modify: `tests/scripts/test_lint_decision_beads.py`

**Interfaces:**

- Consumes: existing JSONL fixtures and `DecisionDigest`/`DecisionLintResult`.
- Produces: a shared active-source fixture and assertions that bind the legacy
  and future provider semantics before storage code exists.

- [ ] **Step 1: Add one canonical active-source fixture**

  Include one valid eligible decision, one malformed decision, one
  title-marker unlabeled record, one P1 bug and one deploy record with
  `blocks` edges, inactive rows, and fields that must be excluded. Keep source
  data in a test-local dictionary/JSONL fixture; do not use the live tracker.
  Add a source-complete watermark fixture with authoritative active
  count/canonical digest, plus empty and count-regressed candidates with absent
  or mismatched evidence.

  ```python
  def _active_source() -> list[dict[str, object]]:
      return [
          {
              "id": "bu-decision",
              "status": "open",
              "issue_type": "task",
              "labels": ["decision"],
              "created_at": "2026-08-13T00:00:00Z",
              "metadata": {"decision": {"options": ["A", "B"], "default": "A"}},
              "description": "Choose the bounded projection path.",
              "due_at": "2026-08-20T00:00:00Z",
          },
      ]
  ```

- [ ] **Step 2: Add RED parity tests**

  Write tests that expect a future `snapshot_from_records()` adapter and
  `compute_decision_digest_from_snapshot()` function to preserve decision ids,
  order, structured-detail availability/reason, escalation hit fields, and
  lint status compared with the current JSONL calculation.

  ```python
  assert projection_digest.open_decisions == legacy_digest.open_decisions
  assert projection_digest.escalations == legacy_digest.escalations
  assert projection_lint == legacy_lint
  ```

  Run: `uv run pytest tests/core/test_beads_projection.py tests/jobs/test_decision_review.py -q`

  Expected: RED for missing provider/snapshot functions; existing JSONL tests
  stay green.

- [ ] **Step 3: Add non-materialization tests**

  Assert that a source record containing `notes`, `comments`, `history`, raw
  metadata, and arbitrary description text does not serialize any of those
  fields into a `BeadIssue`, `BeadDependency`, `BeadLint`, or `BeadSnapshot`.

  ```python
  assert "secret-note" not in repr(snapshot)
  assert snapshot.issues[0].decision_description is None
  assert not hasattr(snapshot.issues[0], "metadata")
  ```

- [ ] **Step 4: Commit the test contract**

  ```bash
  git add tests/core/test_beads_projection.py tests/jobs/test_decision_review.py tests/scripts/test_lint_decision_beads.py
  git commit -m "test: define beads projection parity contract"
  ```

### Task 2: Create the projection schema and privilege boundary

**Revision-allocation precondition:** Immediately before creating this migration,
rebase the implementation branch onto its exact target base, inspect the core
chain's single head in that rebased worktree, and allocate the next core Alembic
revision with `down_revision` set to that head. Do not reuse a revision
identifier, filename, or predecessor from this planning packet. If the target
base advances before implementation or review, rebase and repeat this
allocation from the new exact rebased core-chain head at implementation time.

**Files:**

- Create: `alembic/versions/core/<allocated-next-core-revision>_beads_projection.py`
- Create: `tests/migrations/test_beads_projection_migration.py`
- Modify: `tests/config/test_migration_chain_head.py`

**Interfaces:**

- Consumes: `BeadIssue`, `BeadDependency`, `BeadLint`, and snapshot identifiers
  from Task 1.
- Produces: private snapshot tables and read-only active views for Task 4.

- [ ] **Step 1: Add migrated-DB RED tests**

  Provision through `migrated_core_postgres_pool`, not hand-written fixture
  DDL. Test the singleton pointer, snapshot foreign keys, status/category
  constraints, bounded views, source-watermark digest/authoritative-count
  provenance, a bounded current availability-override category, and actual-role
  access.

  ```python
  async with migrated_core_postgres_pool() as pool:
      await pool.execute("SET ROLE butler_switchboard_rw")
      await pool.fetch("SELECT * FROM beads_projection.active_issues")
      with pytest.raises(asyncpg.InsufficientPrivilegeError):
          await pool.fetch("SELECT * FROM beads_projection.snapshots")
  ```

  Run: `uv run pytest tests/migrations/test_beads_projection_migration.py -q`

  Expected: RED because the schema/views/grants do not exist.

- [ ] **Step 2: Implement the allocated core migration**

  Use the existing core-chain conventions and guard optional role operations.
  Create:

  ```sql
  CREATE SCHEMA beads_projection;
  CREATE TABLE beads_projection.snapshots (...);
  CREATE TABLE beads_projection.snapshot_issues (...);
  CREATE TABLE beads_projection.snapshot_dependencies (...);
  CREATE TABLE beads_projection.snapshot_decision_lint (...);
  CREATE TABLE beads_projection.publication_state (... CHECK (singleton_id = 1));
  CREATE TABLE beads_projection.sync_runs (... CHECK (outcome IN (...)));
  CREATE VIEW beads_projection.active_snapshot AS ...;
  CREATE VIEW beads_projection.active_issues AS ...;
  CREATE VIEW beads_projection.active_dependencies AS ...;
  CREATE VIEW beads_projection.active_decision_lint AS ...;
  ```

  Grant `USAGE`/`SELECT` only on the active views to reader roles. Grant the
  writer identity only the private projection relations/functions it needs.
  Revoke default public access. Keep all references schema-qualified and make
  fresh/core-only invocation safe.

- [ ] **Step 3: Add publication and retention transaction tests**

  Exercise a failed candidate transaction and an injected pointer update
  failure, then verify the old active snapshot stays visible. Publish four
  snapshots and verify only active plus two predecessors remain; publish old
  categorical failed runs and verify only those older than 30 days are pruned.

- [ ] **Step 4: Add the actual-role test matrix**

  Test exporter writer, Switchboard reader, dashboard reader, and an unrelated
  butler role. Assert no role can write through active views, read private
  history/pointer rows, or read unrelated schemas; assert the writer cannot
  read/write `public` application data.

- [ ] **Step 5: Run migration gates and commit**

  ```bash
  uv run pytest tests/migrations/test_beads_projection_migration.py tests/config/test_migration_chain_head.py -q
  MIGRATION_PATH="alembic/versions/core/<allocated-next-core-revision>_beads_projection.py"
  uv run ruff check "$MIGRATION_PATH" tests/migrations/test_beads_projection_migration.py
  uv run ruff format --check "$MIGRATION_PATH" tests/migrations/test_beads_projection_migration.py
  git add "$MIGRATION_PATH" tests/migrations/test_beads_projection_migration.py tests/config/test_migration_chain_head.py
  git commit -m "feat: add bounded beads projection schema"
  ```

### Task 3: Build the tracker-host exporter with bounded failures

**Files:**

- Create: `scripts/beads_projection_exporter.py`
- Create: `tests/scripts/test_beads_projection_exporter.py`
- Modify: `pyproject.toml` only if a repository-approved dependency is truly
  absent; do not add a new dependency for parsing JSONL or PostgreSQL.

**Interfaces:**

- Consumes: the migration relations from Task 2 and test records from Task 1.
- Produces: `publish_candidate(records, pool, now)` and bounded `SyncOutcome`.

- [ ] **Step 1: Write exporter RED tests**

  Cover valid normalization; duplicate identifiers; malformed JSON/timestamps;
  inactive rows; unknown active endpoints; every at-limit acceptance and
  bound-plus-one rejection for the named limits; source-complete empty and
  count-regressed acceptance; absent or mismatched source-completeness evidence
  returning `source_completeness_unverified` with no pointer move; lint clean,
  violations, and unavailable; advisory-lock loss; transaction failure; retry;
  and no raw field materialization.

  ```python
  outcome = await publish_candidate(records, pool, now=_NOW)
  assert outcome.category == "validation_failed"
  assert await _active_snapshot_id(pool) == previous_snapshot_id
  ```

  Run: `uv run pytest tests/scripts/test_beads_projection_exporter.py -q`

  Expected: RED for the missing exporter module.

- [ ] **Step 2: Implement typed normalization and lint adaptation**

  Give the script PEP 723 metadata. Keep `bd export` invocation confined to
  the tracker-host entry point. Pass its staged file through a parser that
  creates the Task 1 typed values. Before accepting a candidate, obtain the
  source-side watermark/count/canonical-digest proof from one source-consistent
  read; never derive that proof by recounting the staged candidate. Run the
  strict lint semantics against the candidate, and convert exceptions to fixed
  categories such as
  `source_unavailable`, `parse_failed`, `validation_failed`,
  `field_bound_exceeded`, `source_completeness_unverified`,
  `tls_verification_failed`, `lock_unavailable`, and `database_write_failed`.

  ```python
  async with pool.acquire() as conn:
      locked = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", "beads_projection_exporter")
      if not locked:
          return SyncOutcome(category="lock_unavailable", published=False)
      try:
          async with conn.transaction():
              await _insert_snapshot(conn, candidate)
              await _activate_snapshot(conn, candidate.snapshot_id)
      finally:
          await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", "beads_projection_exporter")
  ```

- [ ] **Step 3: Add a non-mutating preflight mode**

  `--check-config` validates a tracker-host marker and a TLS PostgreSQL writer
  configuration before export/write work. It accepts only `sslmode=verify-full`
  with a trusted CA bundle, server peer-certificate validation, configured DNS
  hostname verification, and TLS 1.2 or newer. It must fail closed for every
  unverified mode and any missing, unreadable, empty, untrusted, expired,
  hostname-mismatched, or below-floor configuration, with no export, connection,
  candidate row, or pointer movement. Add regression cases for every rejection;
  never print credential material. It must not install a service or create a
  credential.

- [ ] **Step 4: Run focused tests and commit**

  ```bash
  uv run pytest tests/scripts/test_beads_projection_exporter.py tests/migrations/test_beads_projection_migration.py -q
  uv run ruff check scripts/beads_projection_exporter.py tests/scripts/test_beads_projection_exporter.py
  uv run ruff format --check scripts/beads_projection_exporter.py tests/scripts/test_beads_projection_exporter.py
  git add scripts/beads_projection_exporter.py tests/scripts/test_beads_projection_exporter.py
  git commit -m "feat: add tracker-host beads projection exporter"
  ```

### Task 4: Implement the atomic provider and preserve decision semantics

**Files:**

- Create: `src/butlers/core/beads_projection.py`
- Modify: `src/butlers/jobs/decision_review.py`
- Modify: `tests/core/test_beads_projection.py`
- Modify: `tests/jobs/test_decision_review.py`

**Interfaces:**

- Consumes: active views from Task 2 and typed normalized candidate fields from
  Task 3.
- Produces: `ProjectionBeadReadProvider`, `JsonlBeadReadProvider`, and one
  pure `compute_decision_digest_from_snapshot(snapshot, now)` calculation.

- [ ] **Step 1: Implement provider models and freshness classifier**

  Implement exactly the locked interfaces above. Keep `JsonlBeadReadProvider`
  as the explicit compatibility adapter; it may parse a selected local file,
  but no runtime code calls the parser directly once the provider seam exists.

  ```python
  def classify_freshness(completed_at: datetime | None, now: datetime) -> Freshness:
      if completed_at is None or now - completed_at > timedelta(minutes=15):
          return "unavailable"
      if now - completed_at > timedelta(minutes=10):
          return "warning"
      return "fresh"

  def is_target_met(completed_at: datetime | None, now: datetime) -> bool | None:
      if completed_at is None or now - completed_at > timedelta(minutes=15):
          return None
      return now - completed_at <= timedelta(minutes=5)
  ```

- [ ] **Step 2: Add RED atomic-read tests**

  Test one repeatable-read transaction, pointer/row snapshot-id agreement,
  missing/non-singleton pointer, view error, schema mismatch, a current
  sticky `source_completeness_unverified` availability override with a retained
  pointer (including a subsequent non-completeness failure), and a concurrent
  pointer flip. Assert each inconsistency produces `available=False`, not an
  empty available snapshot or normal retained all-clear.

- [ ] **Step 3: Refactor decision review to pure source-independent logic**

  Move source parsing behind the provider and keep decision selection,
  structured-detail mapping, escalation, and lint-to-attention handling pure.
  Use a configured source selector shared by scheduled jobs and the API; do
  not make fallback automatic.

  ```python
  async def compute_decision_digest_from_provider(
      provider: BeadReadProvider, *, now: datetime | None = None
  ) -> DecisionDigest:
      snapshot = await provider.read_active(now=now)
      return compute_decision_digest_from_snapshot(snapshot, now=now)
  ```

- [ ] **Step 4: Verify exact semantic parity and commit**

  ```bash
  uv run pytest tests/core/test_beads_projection.py tests/jobs/test_decision_review.py -q
  uv run ruff check src/butlers/core/beads_projection.py src/butlers/jobs/decision_review.py tests/core/test_beads_projection.py tests/jobs/test_decision_review.py
  uv run ruff format --check src/butlers/core/beads_projection.py src/butlers/jobs/decision_review.py tests/core/test_beads_projection.py tests/jobs/test_decision_review.py
  git add src/butlers/core/beads_projection.py src/butlers/jobs/decision_review.py tests/core/test_beads_projection.py tests/jobs/test_decision_review.py
  git commit -m "refactor: read decision digest through beads provider"
  ```

### Task 5: Migrate dashboard and scheduled consumers together

**Files:**

- Modify: `src/butlers/api/routers/decisions.py`
- Modify: `src/butlers/api/models/decision.py`
- Modify: `tests/api/test_decisions.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/pages/DecisionsPage.tsx`
- Modify: `frontend/src/pages/DecisionsPage.test.tsx`
- Modify: `frontend/src/pages/DecisionsPage.a11y.test.tsx`

**Interfaces:**

- Consumes: Task 4 `BeadSnapshot` and pure digest.
- Produces: one additive Decisions metadata envelope used by API and UI.
- Explicitly excludes: `GET /api/beads/{id}`. Its `BeadSnapshotReader` JSONL
  detail contract includes `design` and `acceptance_criteria`, so it remains an
  explicitly retained consumer outside this Decisions-only cutover.

- [ ] **Step 1: Add API RED tests**

  Test projection source metadata, snapshot timestamp, warning at
  `10m < age <= 15m`, hard unavailable above fifteen minutes, a retained
  pointer after `source_completeness_unverified` yielding
  `decisions_available=False`, JSONL export time compatibility, unchanged
  summary shape, and no mutation/`bd` call.

  ```python
  assert body["meta"] == {
      "decisions_available": True,
      "beads_source": "projection",
      "beads_freshness": "warning",
      "beads_target_met": False,
      "snapshot_as_of": _NOW_MINUS_ELEVEN_MINUTES.isoformat(),
  }
  ```

- [ ] **Step 2: Wire the same provider into API and Switchboard jobs**

  Acquire the already-authorized database pool through the existing dependency
  seam; do not create a second broad dashboard credential path. Both callers
  use the same configured provider/source selector and attach source,
  snapshot-as-of, freshness, and unavailable reason to their existing result
  paths.

- [ ] **Step 3: Update typed UI provenance and plaque**

  Extend `DecisionsListMeta` with `beads_source`, `snapshot_as_of`,
  `beads_freshness`, and `beads_target_met`. Prefer snapshot-as-of over
  export-as-of; warning must use an accessible text/tint and hard unavailable
  must keep the existing degraded note. Do not add approve, reject,
  default-apply, or tracker-navigation controls.

- [ ] **Step 4: Run backend/frontend tests and commit**

  ```bash
  uv run pytest tests/api/test_decisions.py tests/jobs/test_decision_review.py -q
  (cd frontend && npm run test -- --run src/pages/DecisionsPage.test.tsx src/pages/DecisionsPage.a11y.test.tsx)
  (cd frontend && npm run lint && npm run build)
  git add src/butlers/api/routers/decisions.py src/butlers/api/models/decision.py tests/api/test_decisions.py frontend/src/api/types.ts frontend/src/pages/DecisionsPage.tsx frontend/src/pages/DecisionsPage.test.tsx frontend/src/pages/DecisionsPage.a11y.test.tsx
  git commit -m "feat: surface beads projection freshness in decisions"
  ```

### Task 6: Add shadow parity, runbook, and owner-gated activation evidence

**Files:**

- Modify: `scripts/beads_projection_exporter.py`
- Modify: `tests/scripts/test_beads_projection_exporter.py`
- Create: `docs/operations/beads-projection-exporter.md`
- Modify: `docs/frontend/backend-api-contract.md`

**Interfaces:**

- Consumes: JSONL and projection adapters from Task 4.
- Produces: bounded shadow comparison summaries and operator evidence; no
  automatic reader-mode mutation.

- [ ] **Step 1: Add RED shadow comparison tests**

  Compare only semantic decision ids/order, detail state, escalation rows, and
  lint state. A mismatch or unavailable source resets the consecutive-clean
  day count and emits category/count/digest identifiers only.

  ```python
  assert result.clean_days == 0
  assert result.category == "semantic_mismatch"
  assert "description" not in result.summary
  ```

- [ ] **Step 2: Implement a bounded comparator and status output**

  Keep reader mode immutable during shadow. Record daily parity progress,
  observed source ids/digests, mismatch category/count, freshness state, and
  no raw tracker content. Require 14 full consecutive clean days before the
  result can say `cutover_eligible=true`.

- [ ] **Step 3: Write the operational runbook**

  Document the explicit owner gates, required TLS/role/network evidence,
  including `verify-full`, trusted CA, peer certificate, configured-hostname,
  and TLS 1.2+ proof; staging and preflight command; failure signals;
  fourteen-day shadow audit; synchronized projection cutover; seven-day JSONL
  rollback selection; and prohibition on JSONL retirement without a separate
  decision. The later retirement packet must contain a complete JSONL consumer
  inventory; every consumer must be either migrated with contract and regression
  proof or explicitly retained with its mount/parser/materialization rationale.
  It must
  name `GET /api/beads/{id}` and `BeadSnapshotReader` as an explicitly retained
  consumer unless a separately scoped security review approves its own
  migration. Document only variable names/locations, never a credential value.

- [ ] **Step 4: Run focused documentation and shadow tests, then commit**

  ```bash
  uv run pytest tests/scripts/test_beads_projection_exporter.py tests/core/test_beads_projection.py -q
  openspec validate beads-projection-exporter --strict
  git diff --check
  git add scripts/beads_projection_exporter.py tests/scripts/test_beads_projection_exporter.py docs/operations/beads-projection-exporter.md docs/frontend/backend-api-contract.md
  git commit -m "docs: define beads projection shadow and rollback runbook"
  ```

### Task 7: Exact-head verification and separately authorized activation

**Files:**

- Modify only the files above after the owner grants the specific operational
  gate; do not broaden scope.

**Interfaces:**

- Consumes: a reviewed, exact-head implementation and explicit owner
  authorizations.
- Produces: evidence, not a hidden side effect.

- [ ] **Step 1: Run implementation quality gates from the exact PR head**

  ```bash
  openspec validate beads-projection-exporter --strict
  uv run ruff check src/ tests/ scripts/ alembic/versions/core/
  uv run ruff format --check src/ tests/ scripts/ alembic/versions/core/
  make test-qg
  (cd frontend && npm run lint && npm run build && npm run test)
  git diff --check
  ```

- [ ] **Step 2: Obtain independent security and role review**

  Review raw-field exclusion, active-only filter, writer TLS scope, actual
  role grants, advisory-lock/atomicity behavior, no automatic fallback,
  shadow result redaction, and runtime egress/mount proof. Do not treat green
  unit tests as authority to provision or activate.

- [ ] **Step 3: Execute only the owner-granted operational phase**

  The authorization must name one of: workload/credential provisioning,
  migration/deployment execution, shadow start, projection cutover, JSONL
  rollback, or later JSONL retirement. Record the exact evidence and leave
  ungranted phases untouched.

- [ ] **Step 4: Commit/push and request exact-head review**

  Inspect the staged diff, then commit only the explicitly named implementation
  files that passed the preceding gates and push the implementation branch with
  `--force-with-lease` only when its history was rebased. Do not use broad
  staging or push directly to `main`.

  Open a non-draft PR, keep session links out of all PR metadata, wait for all
  hosted checks, resolve every review thread, and use the repository exact-base
  merge helper only after independent exact-head review.

## Plan Self-Review

- **Spec coverage:** Tasks 1–4 implement all `beads-projection` requirements;
  Task 5 implements both modified Decisions contracts; Task 6 implements the
  fourteen-day parity/seven-day rollback requirement; Task 7 preserves the
  operational authorization boundary.
- **Completeness scan:** Every future source/test path, interface, bounded field
  set, command, expected outcome, and owner gate is named. The core migration
  revision is allocated only from the exact rebased core-chain head at
  implementation time, so this planning packet reserves no revision identifier
  or predecessor and remains valid if the target base advances.
- **Type consistency:** `BeadReadProvider.read_active()` returns one
  `BeadSnapshot`; the exporter publishes its data, decision review consumes it,
  and API/UI expose only its source/freshness provenance.

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-08-13-beads-projection-exporter.md`.

Before implementation, obtain the named owner authorization for the exact
operational phase. Then use subagent-driven execution or inline execution as
appropriate; neither workflow expands the authorization boundary.
