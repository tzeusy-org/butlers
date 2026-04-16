---
name: butler-test-condensation
description: Guide for discovering, analyzing, and pruning the Butlers test suite. Use when working on test condensation beads (epic bu-rhztl), assessing test bloat, identifying pruning targets, or rewriting tests to be contract-driven. Triggers on test reduction, test pruning, test consolidation, or condensation tasks for this project. Also use when a fresh session needs to assess test health, create new condensation beads, or resume in-progress condensation work.
---

# Butler Test Condensation

Systematic reduction of the Butlers test suite to ~2,000 contract-driven tests.
Each surviving test must trace to an architectural invariant, an RFC wire
contract, or an OpenSpec capability.

## Before You Start

1. **Rediscover current state** — never trust hardcoded counts in this skill:
   ```bash
   CURRENT=$(find tests/ -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print sum}')
   echo "Current test count: $CURRENT (skill baseline was 13,675 on 2026-04-05)"
   ```
2. **Check epic status**: `bd list --parent bu-rhztl` — see which beads are done/in-progress
3. **Read your bead**: `bd show <bead-id>` for targets and acceptance criteria
4. **Load doctrine**: read `about/heart-and-soul/` for invariants, relevant RFCs in `about/legends-and-lore/`
5. **Run scoped discovery** on your domain — see [references/discovery.md](references/discovery.md)

If your measured counts differ >10% from this skill's numbers, update
[references/domains.md](references/domains.md) before starting work.

## Resuming Mid-Epic

If beads are already in-progress or completed:

```bash
# 1. What's done, what's available?
bd list --parent bu-rhztl
bd ready

# 2. Check predecessor's progress on your domain
git log --oneline -- tests/YOUR_DOMAIN/ | head -10

# 3. Measure current state of your domain
find tests/YOUR_DOMAIN -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print sum}'
```

If bu-zkrix (Phase 1) is NOT completed, **stop** — Phase 1 must finish first.

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
2. **Count target met**: compare against [references/beads.md](references/beads.md) targets
3. **No lost edge cases**: for each deleted file, verify its unique behaviors are
   covered by remaining tests (grep for the error/edge case in surviving tests)
4. **Contract tests pass**: `uv run pytest tests/contracts/ -q -m contract` (if Phase 1 done)
5. **Commit documents delta**: `"Condense X tests: N → M (details of what was removed)"`

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
