> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Behavior was already RFC-mandated; the env-isolation fix landed in the Codex runtime adapter.
> **Successor:** `src/butlers/core/runtimes/codex.py`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Codex Failover Environment Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Codex's invocation-local `HOME` from mutating the caller-owned runtime environment and poisoning a same-tier fallback.

**Architecture:** `RuntimeAdapter.invoke(..., env=...)` receives an explicit, restricted process environment from `Spawner`. The Codex adapter derives a private subprocess copy before installing its temporary `HOME`, and `Spawner` derives a fresh copy for each runtime attempt. The abstract adapter contract documents that adapters cannot mutate the caller-owned mapping.

**Tech Stack:** Python 3.12, asyncio subprocess adapters, pytest/pytest-asyncio, Ruff.

## Global Constraints

- Preserve the restricted environment contract; do not add host variables or a global `HOME`.
- Preserve Codex's isolated `HOME`, MCP configuration, and canonical auth symlink behavior.
- Do not alter model catalog IDs, OpenCode configuration, pricing, dashboard probes, or the gated runtime-probe/canonical-ID work.
- Existing OpenSpec and RFC requirements already mandate this behavior, so no capability-spec or operator-document change is required.

---

### Task 1: Capture caller-environment immutability in a failing adapter test

**Files:**

- Modify: `tests/adapters/test_codex_adapter.py` after `test_invoke_behaviors`

**Interfaces:**

- Consumes: `CodexAdapter.invoke(..., env: dict[str, str])`
- Produces: a regression assertion that a failed invocation does not add `HOME` to the supplied mapping while the subprocess still receives isolated `HOME`.

- [x] **Step 1: Write the failing test**

```python
async def test_invoke_failure_does_not_mutate_caller_env() -> None:
    adapter = CodexAdapter(codex_binary="/usr/bin/codex")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: authentication failed"))
    mock_proc.returncode = 1
    caller_env = {"PATH": "/usr/bin"}

    with patch(_EXEC, return_value=mock_proc) as mock_sub:
        with pytest.raises(RuntimeError, match="Codex CLI exited with code 1"):
            await adapter.invoke(prompt="test", system_prompt="", mcp_servers={}, env=caller_env)

    assert caller_env == {"PATH": "/usr/bin"}
    assert mock_sub.call_args.kwargs["env"] is not caller_env
    assert "HOME" in mock_sub.call_args.kwargs["env"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/adapters/test_codex_adapter.py::test_invoke_failure_does_not_mutate_caller_env -q --tb=short`

Expected: FAIL because `caller_env` is mutated with Codex's temporary `HOME`.

### Task 2: Make the adapter own its subprocess environment

**Files:**

- Modify: `src/butlers/core/runtimes/base.py` in `RuntimeAdapter.invoke` parameter documentation
- Modify: `src/butlers/core/runtimes/codex.py` around temporary `HOME` construction and `_run_codex_subprocess` invocation

**Interfaces:**

- Consumes: caller-owned `env: dict[str, str]`
- Produces: `subprocess_env: dict[str, str]` whose temporary `HOME` is private to the Codex subprocess.

- [x] **Step 1: Document the contract**

Update the abstract `env` parameter documentation to state that adapters must treat the supplied mapping as caller-owned and derive a private map before changing subprocess-specific variables.

- [x] **Step 2: Implement the minimal copy-on-write change**

```python
subprocess_env = dict(env)
subprocess_env["HOME"] = str(tmp_dir)

return await self._run_codex_subprocess(
    cmd,
    subprocess_env,
    cwd,
    effective_timeout,
    cmd_for_log,
    mcp_servers,
    prompt_input,
    token_path=auth_token_path,
    auth_invocation=auth_invocation,
)
```

- [x] **Step 3: Run the regression test to verify it passes**

Run: `uv run pytest tests/adapters/test_codex_adapter.py::test_invoke_failure_does_not_mutate_caller_env -q --tb=short`

Expected: PASS; the subprocess map contains isolated `HOME`, and `caller_env` is unchanged.

### Task 3: Defend the Spawner's logical-session environment boundary

**Files:**

- Modify: `src/butlers/core/spawner.py` in the same-tier failover invoke loop
- Modify: `tests/core/test_spawner_same_tier_failover.py` in `TestAC3RuntimeFailureRetry`

**Interfaces:**

- Consumes: the immutable baseline `env` built for one logical session
- Produces: a fresh environment mapping for each adapter invocation, including same-tier fallbacks.

- [x] **Step 1: Write the failing cross-runtime regression**

Create a failing primary adapter that adds a synthetic `HOME` before raising an eligible error. Seed an explicit restricted environment and assert that a different successful fallback receives that exact original map.

- [x] **Step 2: Run the regression to verify it fails**

Run: `uv run pytest tests/core/test_spawner_same_tier_failover.py::TestAC3RuntimeFailureRetry::test_fallback_gets_pristine_env_after_mutating_failed_attempt -q --tb=short -n0`

Expected: FAIL because the fallback inherits the primary attempt's synthetic `HOME`.

- [x] **Step 3: Copy the environment per Spawner attempt**

Pass `dict(env)` through the per-attempt `invoke_kwargs` so no adapter can mutate the logical-session baseline for a later attempt.

- [x] **Step 4: Run the regression to verify it passes**

Run: `uv run pytest tests/core/test_spawner_same_tier_failover.py::TestAC3RuntimeFailureRetry::test_fallback_gets_pristine_env_after_mutating_failed_attempt -q --tb=short -n0`

Expected: PASS; the fallback receives the exact original restricted environment without synthetic `HOME`.

### Task 4: Validate and commit the bounded runtime surface

**Files:**

- Verify: `src/butlers/core/runtimes/base.py`
- Verify: `src/butlers/core/runtimes/codex.py`
- Verify: `src/butlers/core/spawner.py`
- Verify: `tests/adapters/test_codex_adapter.py`
- Verify: `tests/core/test_spawner_same_tier_failover.py`

- [x] **Step 1: Run the focused adapter suite**

Run: `uv run pytest tests/adapters/test_codex_adapter.py -q --maxfail=1 --tb=short`

Expected: all tests pass.

- [x] **Step 2: Run formatting and lint checks**

Run: `uv run ruff check src/butlers/core/runtimes/base.py src/butlers/core/runtimes/codex.py tests/adapters/test_codex_adapter.py && uv run ruff format --check src/butlers/core/runtimes/base.py src/butlers/core/runtimes/codex.py tests/adapters/test_codex_adapter.py`

Expected: exit 0.

- [x] **Step 3: Run the final unit-test gate**

Run: `make test-qg`

Expected: exit 0.

- [x] **Step 4: Commit the coherent fix**

```bash
git add docs/archive/superpowers/plans/2026-08-10-codex-failover-env-isolation.md \
  AGENTS.md \
  src/butlers/core/runtimes/base.py \
  src/butlers/core/runtimes/codex.py \
  src/butlers/core/spawner.py \
  tests/adapters/test_codex_adapter.py \
  tests/core/test_spawner_same_tier_failover.py
git commit -m "fix: isolate Codex HOME from failover env"
```
