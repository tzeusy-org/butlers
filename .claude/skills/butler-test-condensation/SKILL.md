---
name: butler-test-condensation
description: Guide for discovering, analyzing, and pruning the Butlers test suite. Use when working on test condensation beads (Phase 1 epic bu-rhztl and Phase 2 epic bu-hg8rl both CLOSED; Phase 3 maintenance cycle underway 2026-06-21), assessing test bloat, identifying pruning targets, or rewriting tests to be contract-driven. Triggers on test reduction, test pruning, test consolidation, or condensation tasks for this project. Also use when a fresh session needs to assess test health, create new condensation beads, or resume in-progress condensation work.
---

# Butler Test Condensation

Systematic reduction of the Butlers test suite to ~2,000 contract-driven tests.
Each surviving test must trace to an architectural invariant, an RFC wire
contract, or an OpenSpec capability.

## Epic History

- **Phase 1 epic `bu-rhztl`** (2026-04-04 → 2026-04-06, **CLOSED**): condensed
  13,675 → 2,196 tests across 10 PRs. Three-tier architecture established.
- **Phase 2 epic `bu-hg8rl`** (2026-05-03 → 2026-05-05, **CLOSED**): "all steps
  complete." Children bu-9riic (Tier 1 backfill), bu-gg4y1 (api), bu-m564i
  (chronicler) all done. Suite was ~3,700 at open.
- **Phase 3 maintenance cycle (2026-06-21, underway)**: suite **DOUBLED** to
  **7,494 `def test_` functions / 8,107 collected across 657 files** — almost
  entirely legitimate feature coverage, not bloat. A disciplined moderate pass
  this cycle found only **~1,050–1,250 SAFELY removable**. Do NOT over-trim
  chasing a target number; most growth is real behavior. Per-domain counts +
  the migrations-boilerplate lever live in [references/domains.md](references/domains.md).

> **Suite-doubling cadence**: the suite reliably ~doubles between condensation
> cycles on real feature work. Budget a recurring (≈monthly) maintenance pass,
> but expect the safely-removable fraction to be small (~15%).

## Before You Start

1. **Rediscover current state** — never trust hardcoded counts in this skill:
   ```bash
   CURRENT=$(grep -rc 'def test_' tests/ --include='*.py' | awk -F: '{sum+=$2} END {print sum}')
   echo "Current test count: $CURRENT (Phase 3 baseline 7,494 on 2026-06-21; Phase 1 closed at 2,196)"
   ```
2. **Check epic status**: `bd list --status all` — Phase 1 (`bu-rhztl`) and
   Phase 2 (`bu-hg8rl`) are both CLOSED. Look for an active Phase 3 epic.
3. **Read your bead**: `bd show <bead-id>` for targets and acceptance criteria
4. **Load doctrine**: read `about/heart-and-soul/` for invariants, relevant RFCs in `about/legends-and-lore/`
5. **Run scoped discovery** on your domain — see [references/discovery.md](references/discovery.md)

If your measured counts differ >10% from this skill's numbers, update
[references/domains.md](references/domains.md) before starting work.

## Two Hard Guards (read before deleting anything)

1. **Mock-wiring assertions are NOT all bloat.** `assert_not_called` /
   `assert_not_awaited` / `call_count` frequently encode REAL contracts
   (idempotency, retry/delivery cadence, resolver bypass, canonical-fact-store
   boundary). Apply the plumbing-vs-contract test in
   [references/classification.md](references/classification.md#1-mock-call-assertions)
   before deleting any call assertion. When in doubt, KEEP.
2. **Some test files are imported by other test files.** `DELETE_FILE` on a
   shared helper breaks its importers and reds the suite. Before deleting ANY
   file, confirm nothing imports it:
   ```bash
   base=$(basename "$FILE" .py)
   grep -rn "import .*\b$base\b\|from .*\b$base\b import" tests/ --include='*.py'
   ```
   Never delete: any `conftest.py`, any `__init__.py`,
   `tests/modules/test_module_registry.py`, `tests/modules/test_module_pipeline.py`,
   `tests/modules/memory/_test_helpers.py`,
   `tests/e2e/{envelopes,scenarios,reporting,scoring,benchmark}.py`.

## Resuming Mid-Epic

If beads are already in-progress or completed:

```bash
# 1. What's done, what's available?
bd list --parent <epic-id>
bd ready

# 2. Check predecessor's progress on your domain
git log --oneline -- tests/YOUR_DOMAIN/ | head -10

# 3. Measure current state of your domain
grep -rc 'def test_' tests/YOUR_DOMAIN --include='*.py' | awk -F: '{sum+=$2} END {print sum}'
```

Phases 1 (`bu-rhztl`) and 2 (`bu-hg8rl`) are closed. For Phase 3, any Tier 1
contract backfill should land before structural condensation in domains that
promote tests into `tests/contracts/`.

## Three-Tier Test Architecture

All tests must map to exactly one tier. If a test doesn't fit, it's a pruning target.

### Tier 1: Architectural Invariants (~200 tests) — `tests/contracts/`

Heart-and-soul non-negotiables. Tagged `@pytest.mark.contract`. Each test
docstring cites its RFC/principle. **15 invariants:**

1. **Schema isolation** — butler can't query another butler's schema (RFC 0006)
2. **MCP-only inter-butler** — no cross-butler imports or direct DB calls
3. **Daemon determinism** — 17-phase startup order, failure propagation (RFC 0001)
4. **Tool surface isolation** — ephemeral MCP config scoping (RFC 0002)
5. **Module composition** — topo sort, cycle detection, cascade failure (RFC 0002)
6. **Module boundaries** — modules MUST NOT modify core infrastructure (RFC 0002)
7. **Credential tier resolution** — Tier 0->1->2 precedence, no plaintext leakage (RFC 0006)
8. **Approval gates** — sensitive ops intercepted, can't be bypassed
9. **Graceful shutdown** — drain, reverse-order on_shutdown (RFC 0001)
10. **Session lifecycle** — request_id UUIDv7 propagation, tool call capture (RFC 0001)
11. **Identity resolution** — 3-table JOIN, owner bootstrap, unknowns (RFC 0004)
12. **Context bus** — signal TTL, write permissions, supersession (RFC 0009)
13. **Routing pipeline** — dedup, thread affinity, triage rules, priority (RFC 0003)
14. **Connector-as-transport** — connectors normalize to ingest.v1 only; no routing/classification logic
15. **Staffer routing exclusion** — staffers excluded from user-message routing candidates (RFC 0003)

### Tier 2: Wire Contracts (~500-800 tests)

RFC-defined schemas and state machines:
- ingest.v1 envelope schema (RFC 0003)
- route inbox state machine: accepted->processing->processed/errored (RFC 0001)
- Module ABC contract: register_tools, migrations, on_startup/on_shutdown (RFC 0002)
- Migration chain execution (schema outcomes, not SQL strings)
- API response contracts (Pydantic schema validation, not field-by-field)
- Cross-butler briefing view + 5 guardrails (RFC 0010)
- Insight delivery: candidate schema, dedup key format, cooldown, anti-spam (RFC 0011)
- Finance transaction model: tiered dedup, CRUD, soft-delete-only (RFC 0012)

### Tier 3: Capability Behavior (~800-1200 tests)

OpenSpec-driven. Map each spec's WHEN/THEN Scenarios to test functions.
Tests exercise behavior through MCP tool interface or public API — not internal
helpers. Assertions are **structural** (non-None, correct type, non-empty) not
**behavioral** (exact strings, specific counts, ordering). See
[references/classification.md](references/classification.md) for the decision matrix.

## Condensation Workflow Per Domain

1. **Scope**: Run discovery commands from [references/discovery.md](references/discovery.md) scoped to your domain
2. **Classify**: Apply the decision matrix in [references/classification.md](references/classification.md) to each test
3. **Write replacements**: For each deleted test that covers a unique behavior, write a behavioral replacement through MCP tool/public API interface
4. **Verify**: Run `uv run pytest tests/YOUR_DOMAIN -q --tb=short` — zero failures
5. **Delete**: Remove old implementation tests
6. **Gate**: Pass quality gates (see below)
7. **Count**: Verify test count meets bead acceptance criteria

## Quality Gates

Before marking a bead complete:

1. **Green suite**: `uv run pytest tests/YOUR_DOMAIN -q --tb=short` — 0 failures
2. **No cross-file import breaks**: `uv run pytest tests/ --collect-only -q` must
   succeed. Deletions of shared helpers fail HERE, not in the scoped run.
3. **Count target met**: compare against [references/beads.md](references/beads.md) targets
4. **No lost edge cases**: for each deleted file, verify its unique behaviors are
   covered by remaining tests (grep for the error/edge case in surviving tests)
5. **Contract tests pass**: `uv run pytest tests/contracts/ -q -m contract`
6. **Lint**: `uv run ruff check tests/YOUR_DOMAIN --output-format concise`
7. **Commit documents delta**: `"Condense X tests: N → M (details of what was removed)"`

**CI-gate reality** (what actually blocks merge):
- The **required** CI job runs `uv run pytest tests/ -m "not integration and not e2e"`
  (no Docker). Integration runs in a **separate** Docker job
  (`-m "integration and not nightly"`).
- `frontend`, `e2e`, and `check` are **NOT required** GitHub checks — PRs can
  merge over them red. Don't rely on them; gate locally.
- Local pre-merge gate = ruff + the non-integration subset + `--collect-only`.

**Verifying in a worktree** (worktrees have no `.venv`): condensation only edits
`tests/`, so symlink the root venv and run with `--no-sync` (prevents racing
re-syncs of the shared venv):
```bash
ln -s /home/tze/gt/butlers/.venv "$PWD/.venv"
uv run --no-sync pytest tests/YOUR_DOMAIN -q --tb=short
```

## Updating OpenSpec When Tests Reveal Gaps

During condensation, you may find tests that validate behavior NOT in any spec:
- If the behavior is essential (users rely on it) → create an OpenSpec change to document it
- If the behavior is an implementation detail → delete the test
- If the spec contradicts the test → update the spec to match current behavior

Document your decision in a commit message or bead comment.

## Domain-Specific Guidance

[references/domains.md](references/domains.md) — per-domain targets, file inventories, strategies.

## Test Classification Decision Matrix

[references/classification.md](references/classification.md) — how to decide keep/delete/rewrite for any test.

## Beads Epic

[references/beads.md](references/beads.md) — dependency graph, bead IDs, lifecycle.
