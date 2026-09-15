---
name: butler-test-condensation
description: Guide for discovering, analyzing, and pruning the Butlers test suite. Use when working on test condensation beads (Phase 1 epic bu-rhztl and Phase 2 epic bu-hg8rl both CLOSED; a Phase 3 maintenance cycle was drafted 2026-06-21 but never filed), assessing test bloat, identifying pruning targets, or rewriting tests to be contract-driven. Triggers on test reduction, test pruning, test consolidation, or condensation tasks for this project. Also use when a fresh session needs to assess test health, create new condensation beads, or resume in-progress condensation work.
metadata:
  owner: tze
  authors:
    - tze
    - Claude
  status: active
  last_reviewed: "2026-09-05"
---

# Butler Test Condensation

Systematic reduction of the Butlers test suite to ~2,000 contract-driven tests.
Each surviving test must trace to an architectural invariant, an RFC wire
contract, or an OpenSpec capability.

## Reference Files

| File | Load when |
|---|---|
| [references/discovery.md](references/discovery.md) | Measuring current test counts, running staleness/scoped/smell-detection commands, or verifying post-condensation |
| [references/classification.md](references/classification.md) | Deciding keep/delete/rewrite for a specific test — decision tree + mock-assertion litmus test |
| [references/domains.md](references/domains.md) | Working a specific domain bead — per-domain targets, file inventories, condensation strategy |
| [references/beads.md](references/beads.md) | Checking the epic/bead dependency graph, bead IDs, or bead lifecycle commands |

## Epic History

Full bead graphs and lifecycle commands live in
[references/beads.md](references/beads.md); current counts live in
[references/domains.md](references/domains.md) and
[references/discovery.md](references/discovery.md) — never trust hardcoded
numbers here.

- **Phase 1** (`bu-rhztl`, CLOSED 2026-04-06): 13,675 → 2,196 tests, 10 PRs.
  Established the three-tier architecture below.
- **Phase 2** (`bu-hg8rl`, CLOSED 2026-05-05): ~3,700 tests, "all steps complete."
- **Phase 3** (drafted 2026-06-21, **NEVER FILED**): suite had doubled to
  7,494; only ~1,050–1,250 was safely removable — most growth was real
  feature coverage. No epic was ever opened, so the trim never ran and the
  suite kept growing. Re-measure before filing anything.

> **Suite-doubling cadence**: the suite reliably ~doubles between condensation
> cycles, yet the safely-removable fraction stays small (~15%). Budget a
> recurring (≈monthly) pass; do not over-trim chasing a number.

## Before You Start

1. **Rediscover current state** — run the staleness check in
   [references/discovery.md](references/discovery.md); never trust hardcoded
   counts in this skill.
2. **Check epic status**: `bd list --status all` — Phase 1 (`bu-rhztl`) and
   Phase 2 (`bu-hg8rl`) are both CLOSED. **No Phase 3 epic exists**; the
   2026-06-21 proposal in [references/beads.md](references/beads.md) was
   never filed. Do not file one casually — READY beads auto-trigger the
   autonomous fleet.
3. **Read your bead**: `bd show <bead-id>` for targets and acceptance criteria.
4. **Load doctrine**: `about/heart-and-soul/` for invariants, relevant RFCs in
   `about/legends-and-lore/`.
5. **Run scoped discovery** on your domain — see
   [references/discovery.md](references/discovery.md).

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
   file, run the shared-helper check in
   [references/discovery.md](references/discovery.md#shared-helper-detection-run-before-any-delete_file)
   and confirm nothing imports it. Never delete `conftest.py`, `__init__.py`,
   or any file on the never-delete list there.

## Resuming Mid-Epic

If beads are already in-progress or completed:

```bash
bd list --parent <epic-id>                                             # what's done, available?
bd ready
git log --oneline -- tests/YOUR_DOMAIN/ | head -10                     # predecessor's progress
grep -rc 'def test_' tests/YOUR_DOMAIN --include='*.py' | awk -F: '{sum+=$2} END {print sum}'  # current count
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

RFC-defined schemas and state machines: ingest.v1 envelope (RFC 0003); route
inbox state machine accepted->processing->processed/errored (RFC 0001); Module
ABC contract — register_tools, migrations, on_startup/on_shutdown (RFC 0002);
migration chain execution (schema outcomes, not SQL strings); API response
contracts (Pydantic schema validation, not field-by-field); cross-butler
briefing view + 5 guardrails (RFC 0010); insight delivery — candidate schema,
dedup key format, cooldown, anti-spam (RFC 0011); finance transaction model —
tiered dedup, CRUD, soft-delete-only (RFC 0012).

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

CI actually runs a sharded fan-in — `check-preflight` plus five `check-unit-N`
and five `check-integration-N` shards, fanned into a fail-closed `check` job;
the merge queue's `merge_group` run against the exact landing tree is the
terminal gate. Full job breakdown, the merge-queue ruleset, and the
worktree-venv gotcha (never symlink the main repo's `.venv` — bu-1redj) live in
[references/discovery.md](references/discovery.md#ci-gate-structure) and
[references/discovery.md](references/discovery.md#post-condensation-verification).

## Updating OpenSpec When Tests Reveal Gaps

A test may validate behavior not in any spec. If it's essential (users rely on
it), create an OpenSpec change to document it; if it's an implementation
detail, delete the test; if the spec contradicts the test, update the spec to
match current behavior. Document your decision in a commit message or bead
comment.
