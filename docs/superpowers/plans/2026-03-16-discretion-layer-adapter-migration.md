# Discretion Layer → RuntimeAdapter Migration

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the discretion layer's direct Ollama HTTP calls with the project's RuntimeAdapter interface, backed by the model catalog, so discretion model selection is managed through the same Settings UI and resolution path as butler sessions.

**Architecture:** The `DiscretionEvaluator` currently calls Ollama directly via `httpx`. We introduce a `DiscretionDispatcher` that mirrors the spawner's pattern — own semaphore, own adapter pool, resolves models from `shared.model_catalog` using a new `discretion` complexity tier. Connectors inject the dispatcher into evaluators instead of a `DiscretionConfig`. The `_call_llm` function and `DiscretionConfig` env var machinery are removed.

**Tech Stack:** Python 3.12, asyncpg, RuntimeAdapter ABC, asyncio.Semaphore, Alembic, FastAPI/Pydantic (settings API), pytest

**Blocked on:** `bu-fjsb`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/butlers/connectors/discretion_dispatcher.py` | Semaphore-gated adapter dispatcher for discretion calls |
| Modify | `src/butlers/connectors/discretion.py` | Remove `_call_llm`, `DiscretionConfig`; accept dispatcher in `DiscretionEvaluator` |
| Modify | `src/butlers/core/model_routing.py` | Add `DISCRETION` to `Complexity` enum |
| Modify | `alembic/versions/core/core_025_model_catalog.py` | Add `'discretion'` to CHECK constraint (or new migration) |
| Modify | `src/butlers/api/routers/model_settings.py` | Add `'discretion'` to `_COMPLEXITY_TIERS` |
| Modify | `model_catalog_defaults.toml` | Seed default discretion model entry |
| Modify | `src/butlers/connectors/live_listener/connector.py` | Inject dispatcher into evaluators |
| Modify | `src/butlers/connectors/telegram_user_client.py` | Inject dispatcher into evaluators |
| Modify | `tests/connectors/live_listener/test_discretion.py` | Rewrite to mock adapter instead of `_call_llm` |
| Create | `tests/connectors/test_discretion_dispatcher.py` | Unit tests for the dispatcher |

---

## Chunk 1: Add `discretion` complexity tier to the system

### Task 1: Extend the Complexity enum

**Files:**
- Modify: `src/butlers/core/model_routing.py:31-37`

- [ ] **Step 1: Write a failing test**

File: `tests/core/test_model_routing_discretion_tier.py`

```python
"""Verify the Complexity enum includes a DISCRETION tier."""

import pytest

from butlers.core.model_routing import Complexity

pytestmark = pytest.mark.unit


def test_discretion_tier_exists():
    assert hasattr(Complexity, "DISCRETION")
    assert Complexity.DISCRETION.value == "discretion"


def test_discretion_tier_is_string():
    assert isinstance(Complexity.DISCRETION, str)
    assert Complexity.DISCRETION == "discretion"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_model_routing_discretion_tier.py -v`
Expected: FAIL with `AttributeError: DISCRETION`

- [ ] **Step 3: Add DISCRETION to the Complexity enum**

In `src/butlers/core/model_routing.py`, add after `EXTRA_HIGH = "extra_high"`:

```python
    DISCRETION = "discretion"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_model_routing_discretion_tier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/butlers/core/model_routing.py tests/core/test_model_routing_discretion_tier.py
git commit -m "feat(model-routing): add DISCRETION complexity tier"
```

---

### Task 2: Database migration — add `discretion` to CHECK constraints

**Files:**
- Create: `alembic/versions/core/core_026_discretion_tier.py`

The existing `core_025` migration has a CHECK constraint limiting `complexity_tier` to `('trivial', 'medium', 'high', 'extra_high')`. We need a new migration to widen it.

- [ ] **Step 1: Write the migration**

Create `alembic/versions/core/core_026_discretion_tier.py`:

```python
"""Add 'discretion' to model_catalog and butler_model_overrides complexity_tier CHECK constraints.

Revision ID: core_026
Revises: core_025
Create Date: 2026-03-16 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_026"
down_revision = "core_025"
branch_labels = None
depends_on = None

_NEW_TIERS = "('trivial', 'medium', 'high', 'extra_high', 'discretion')"
_OLD_TIERS = "('trivial', 'medium', 'high', 'extra_high')"


def upgrade() -> None:
    # model_catalog: drop old, add new
    op.execute(
        "ALTER TABLE shared.model_catalog"
        " DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier"
    )
    op.execute(
        "ALTER TABLE shared.model_catalog"
        " ADD CONSTRAINT chk_model_catalog_complexity_tier"
        f" CHECK (complexity_tier IN {_NEW_TIERS})"
    )

    # butler_model_overrides: drop old, add new
    op.execute(
        "ALTER TABLE shared.butler_model_overrides"
        " DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier"
    )
    op.execute(
        "ALTER TABLE shared.butler_model_overrides"
        " ADD CONSTRAINT chk_butler_model_overrides_complexity_tier"
        f" CHECK (complexity_tier IS NULL OR complexity_tier IN {_NEW_TIERS})"
    )


def downgrade() -> None:
    # Restore original constraints (will fail if 'discretion' rows exist)
    op.execute(
        "ALTER TABLE shared.model_catalog"
        " DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier"
    )
    op.execute(
        "ALTER TABLE shared.model_catalog"
        " ADD CONSTRAINT chk_model_catalog_complexity_tier"
        f" CHECK (complexity_tier IN {_OLD_TIERS})"
    )

    op.execute(
        "ALTER TABLE shared.butler_model_overrides"
        " DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier"
    )
    op.execute(
        "ALTER TABLE shared.butler_model_overrides"
        " ADD CONSTRAINT chk_butler_model_overrides_complexity_tier"
        f" CHECK (complexity_tier IS NULL OR complexity_tier IN {_OLD_TIERS})"
    )
```

- [ ] **Step 2: Verify migration file is syntactically valid**

Run: `uv run python -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', 'alembic/versions/core/core_026_discretion_tier.py'); mod = importlib.util.module_from_spec(spec)"`
Expected: No import errors

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/core/core_026_discretion_tier.py
git commit -m "migration(core_026): add discretion to complexity_tier CHECK constraints"
```

---

### Task 3: Seed default discretion model in catalog defaults

**Files:**
- Modify: `model_catalog_defaults.toml`

- [ ] **Step 1: Add discretion default entry**

Append to `model_catalog_defaults.toml`:

```toml
[[models]]
alias = "discretion-qwen3.5-9b"
runtime_type = "opencode"
model_id = "ollama/qwen3.5:9b"
extra_args = []
complexity_tier = "discretion"
priority = 10
enabled = true
```

Note: The `model_id` format depends on how OpenCode references Ollama models. Check the existing opencode entries in the catalog — they use `opencode-go/<model>` format. The Ollama provider in OpenCode may use a different prefix. The implementer should verify the correct `model_id` format by checking OpenCode's Ollama provider configuration (likely in `~/.opencode/config.json` or similar). If OpenCode uses `ollama/<model>` syntax, use that. If it uses a different provider prefix, adjust accordingly.

- [ ] **Step 2: Verify TOML is valid**

Run: `uv run python -c "import tomllib; tomllib.load(open('model_catalog_defaults.toml', 'rb'))"`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add model_catalog_defaults.toml
git commit -m "config: seed default discretion model (qwen3.5:9b via opencode)"
```

---

### Task 4: Add `discretion` to the Settings API tier list

**Files:**
- Modify: `src/butlers/api/routers/model_settings.py:34`

- [ ] **Step 1: Write a failing test**

File: `tests/api/test_model_settings_discretion_tier.py`

```python
"""Verify the settings API accepts the discretion complexity tier."""

import pytest

pytestmark = pytest.mark.unit


def test_discretion_tier_in_api_constant():
    from butlers.api.routers.model_settings import _COMPLEXITY_TIERS

    assert "discretion" in _COMPLEXITY_TIERS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_model_settings_discretion_tier.py -v`
Expected: FAIL — `"discretion" not in ("trivial", "medium", "high", "extra_high")`

- [ ] **Step 3: Add `discretion` to the tuple**

In `src/butlers/api/routers/model_settings.py`, change line 34:

```python
_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high", "discretion")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_model_settings_discretion_tier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/butlers/api/routers/model_settings.py tests/api/test_model_settings_discretion_tier.py
git commit -m "feat(api): accept discretion tier in model settings endpoints"
```

---

## Chunk 2: Build the DiscretionDispatcher

### Task 5: Create `DiscretionDispatcher` with semaphore + adapter resolution

This is the core new component. It mirrors the spawner's pattern:
- Own `asyncio.Semaphore` (configurable concurrency, default 4)
- Own adapter pool (lazy instantiation via `get_adapter`)
- Resolves model from catalog via `resolve_model(pool, "discretion", Complexity.DISCRETION)`
- Exposes a simple `async def call(prompt, system_prompt) -> str` that returns raw LLM text

**Files:**
- Create: `src/butlers/connectors/discretion_dispatcher.py`
- Create: `tests/connectors/test_discretion_dispatcher.py`

- [ ] **Step 1: Write failing tests**

File: `tests/connectors/test_discretion_dispatcher.py`

```python
"""Tests for DiscretionDispatcher — adapter-based LLM dispatch for discretion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher

pytestmark = pytest.mark.unit


@pytest.fixture()
def mock_pool():
    """Mock asyncpg pool that returns a catalog row for discretion tier."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "runtime_type": "opencode",
            "model_id": "ollama/qwen3.5:9b",
            "extra_args": "[]",
        }
    )
    return pool


class TestDispatcherResolution:
    async def test_resolves_model_from_catalog(self, mock_pool):
        """Dispatcher should query the catalog for the discretion tier."""
        mock_adapter_cls = MagicMock()
        mock_adapter_instance = MagicMock()
        mock_adapter_instance.invoke = AsyncMock(
            return_value=("FORWARD: direct request", [], None)
        )
        mock_adapter_instance.create_worker.return_value = mock_adapter_instance
        mock_adapter_cls.return_value = mock_adapter_instance

        with patch(
            "butlers.connectors.discretion_dispatcher.get_adapter",
            return_value=mock_adapter_cls,
        ):
            dispatcher = DiscretionDispatcher(pool=mock_pool)
            result = await dispatcher.call(
                prompt="evaluate this message",
                system_prompt="You are a filter.",
            )

        assert result == "FORWARD: direct request"
        mock_pool.fetchrow.assert_called_once()

    async def test_raises_on_no_catalog_entry(self, mock_pool):
        """When no catalog entry exists for discretion tier, raise RuntimeError."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        dispatcher = DiscretionDispatcher(pool=mock_pool)

        with pytest.raises(RuntimeError, match="No model configured for discretion"):
            await dispatcher.call(
                prompt="evaluate this",
                system_prompt="filter",
            )


class TestDispatcherConcurrency:
    async def test_semaphore_limits_concurrency(self, mock_pool):
        """Dispatcher should respect max_concurrent setting."""
        dispatcher = DiscretionDispatcher(pool=mock_pool, max_concurrent=2)
        assert dispatcher._semaphore._value == 2

    async def test_default_concurrency_is_4(self, mock_pool):
        dispatcher = DiscretionDispatcher(pool=mock_pool)
        assert dispatcher._semaphore._value == 4


class TestDispatcherTimeout:
    async def test_timeout_raises_timeout_error(self, mock_pool):
        """Dispatcher should enforce its own timeout."""
        import asyncio

        mock_adapter_cls = MagicMock()
        mock_adapter_instance = MagicMock()

        async def slow_invoke(**kwargs):
            await asyncio.sleep(10)
            return ("IGNORE", [], None)

        mock_adapter_instance.invoke = AsyncMock(side_effect=slow_invoke)
        mock_adapter_instance.create_worker.return_value = mock_adapter_instance
        mock_adapter_cls.return_value = mock_adapter_instance

        with patch(
            "butlers.connectors.discretion_dispatcher.get_adapter",
            return_value=mock_adapter_cls,
        ):
            dispatcher = DiscretionDispatcher(pool=mock_pool, timeout_s=0.1)
            with pytest.raises(TimeoutError):
                await dispatcher.call(
                    prompt="test",
                    system_prompt="filter",
                )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/connectors/test_discretion_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'butlers.connectors.discretion_dispatcher'`

- [ ] **Step 3: Implement DiscretionDispatcher**

Create `src/butlers/connectors/discretion_dispatcher.py`:

```python
"""DiscretionDispatcher — adapter-based LLM dispatch for discretion evaluation.

Mirrors the Spawner's pattern but dedicated to discretion:
- Own asyncio.Semaphore (default 4 concurrent calls)
- Resolves model from shared.model_catalog using the 'discretion' tier
- Lazy adapter instantiation via the RuntimeAdapter registry
- Simple call() interface: prompt + system_prompt → raw text

The dispatcher does NOT manage context windows, weight resolution, or
verdict parsing — those remain in DiscretionEvaluator.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.model_routing import Complexity, resolve_model
from butlers.core.runtimes.base import RuntimeAdapter, get_adapter

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT = 4
_DEFAULT_TIMEOUT_S = 5.0


class DiscretionDispatcher:
    """Semaphore-gated adapter dispatcher for discretion LLM calls.

    Parameters
    ----------
    pool:
        asyncpg connection pool for catalog queries.
    max_concurrent:
        Maximum concurrent discretion LLM calls. Default 4.
    timeout_s:
        Per-call timeout in seconds. Default 5.0.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._pool = pool
        self._timeout_s = timeout_s
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._adapter_cache: dict[str, RuntimeAdapter] = {}

    def _get_or_create_adapter(self, runtime_type: str) -> RuntimeAdapter:
        """Return a cached adapter for runtime_type, creating lazily if needed."""
        if runtime_type in self._adapter_cache:
            return self._adapter_cache[runtime_type]

        adapter_cls = get_adapter(runtime_type)
        try:
            adapter = adapter_cls(butler_name="discretion", log_root=None)  # type: ignore[call-arg]
        except TypeError:
            adapter = adapter_cls()
        self._adapter_cache[runtime_type] = adapter
        logger.debug("Discretion dispatcher: created adapter for %s", runtime_type)
        return adapter

    async def call(
        self,
        prompt: str,
        system_prompt: str,
    ) -> str:
        """Dispatch a discretion evaluation to the catalog-resolved adapter.

        Returns the raw LLM response text.

        Raises
        ------
        RuntimeError
            If no model is configured for the discretion tier.
        TimeoutError
            If the call exceeds timeout_s.
        """
        # Resolve model from catalog
        catalog_result = await resolve_model(
            self._pool, "discretion", Complexity.DISCRETION
        )
        if catalog_result is None:
            raise RuntimeError(
                "No model configured for discretion tier in shared.model_catalog. "
                "Add an entry with complexity_tier='discretion' via the Settings UI."
            )

        runtime_type, model_id, extra_args = catalog_result

        adapter = self._get_or_create_adapter(runtime_type)
        worker = adapter.create_worker()

        async with self._semaphore:
            text, _, _ = await asyncio.wait_for(
                worker.invoke(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    mcp_servers={},
                    env={},
                    max_turns=1,
                    model=model_id,
                    runtime_args=extra_args,
                    timeout=int(self._timeout_s),
                ),
                timeout=self._timeout_s,
            )

        if text is None:
            raise RuntimeError("Adapter returned no text for discretion call")

        return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/connectors/test_discretion_dispatcher.py -v`
Expected: PASS (or adjust mocks as needed — the adapter invoke signature must match)

- [ ] **Step 5: Commit**

```bash
git add src/butlers/connectors/discretion_dispatcher.py tests/connectors/test_discretion_dispatcher.py
git commit -m "feat: add DiscretionDispatcher — adapter-based LLM dispatch for discretion"
```

---

## Chunk 3: Rewire DiscretionEvaluator to use the dispatcher

### Task 6: Modify DiscretionEvaluator to accept a dispatcher instead of config

**Files:**
- Modify: `src/butlers/connectors/discretion.py`
- Modify: `tests/connectors/live_listener/test_discretion.py`

The evaluator keeps: `ContextWindow`, `ContactWeightResolver`, `_build_user_prompt`, `_parse_verdict`, `DiscretionResult`, `ContextEntry`, `Verdict`, `WeightTier`.

The evaluator loses: `DiscretionConfig`, `_call_llm`, all `httpx` usage, all env var machinery.

The evaluator gains: accepts a `DiscretionDispatcher` (or a protocol/callable matching its `call()` signature for testability).

- [ ] **Step 1: Define a protocol for the dispatcher**

This keeps the evaluator testable without importing the full dispatcher. Add at the top of `discretion.py` (after removing `httpx` and env var code):

```python
from typing import Protocol

class DiscretionLLMCaller(Protocol):
    """Protocol for discretion LLM dispatch — satisfied by DiscretionDispatcher."""

    async def call(self, prompt: str, system_prompt: str) -> str: ...
```

- [ ] **Step 2: Rewrite DiscretionEvaluator.__init__ and evaluate()**

Replace the `DiscretionConfig` parameter with:

```python
class DiscretionEvaluator:
    def __init__(
        self,
        source_name: str,
        dispatcher: DiscretionLLMCaller,
        *,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        weight_bypass: float = _DEFAULT_WEIGHT_BYPASS,
        weight_fail_open: float = _DEFAULT_WEIGHT_FAIL_OPEN,
    ) -> None:
        self._source = source_name
        self._dispatcher = dispatcher
        self._system_prompt = system_prompt
        self._weight_bypass = weight_bypass
        self._weight_fail_open = weight_fail_open
        self._window = ContextWindow(
            max_size=window_size,
            max_age_seconds=window_seconds,
        )
```

In `evaluate()`, replace the `_call_llm` + `asyncio.wait_for` block with:

```python
        raw = await self._dispatcher.call(
            prompt=prompt,
            system_prompt=self._system_prompt,
        )
```

The timeout is now the dispatcher's responsibility (it wraps `asyncio.wait_for` internally). The evaluator still catches `TimeoutError` and general `Exception` for fail-open/closed behavior.

- [ ] **Step 3: Remove dead code**

Delete from `discretion.py`:
- `import httpx`
- `import os`
- `_DEFAULT_LLM_MODEL`
- `_DEFAULT_LLM_URL`
- `class DiscretionConfig` (entire class)
- `async def _call_llm(...)` (entire function)

Keep all other constants (`_DEFAULT_TIMEOUT_S` can be removed too since timeout is in the dispatcher, but keep `_DEFAULT_WINDOW_SIZE`, `_DEFAULT_WINDOW_SECONDS`, `_DEFAULT_WEIGHT_BYPASS`, `_DEFAULT_WEIGHT_FAIL_OPEN`, `_DEFAULT_SYSTEM_PROMPT`).

- [ ] **Step 4: Update all test mocks**

In `tests/connectors/live_listener/test_discretion.py`:

Replace all `patch("butlers.connectors.discretion._call_llm", ...)` with a mock dispatcher:

```python
class MockDispatcher:
    """Test double for DiscretionLLMCaller protocol."""

    def __init__(self, return_value=None, side_effect=None):
        self._return_value = return_value
        self._side_effect = side_effect
        self.call_count = 0
        self.last_prompt = None

    async def call(self, prompt: str, system_prompt: str) -> str:
        self.call_count += 1
        self.last_prompt = prompt
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value
```

Then update each test fixture and test to use `MockDispatcher` instead of `patch`. For example:

```python
async def test_forward_verdict_returned(self):
    dispatcher = MockDispatcher(return_value="FORWARD: sounds like a direct request")
    evaluator = DiscretionEvaluator(
        source_name="kitchen",
        dispatcher=dispatcher,
    )
    result = await evaluator.evaluate("Hey, turn off the lights", weight=0.7)
    assert result.verdict == "FORWARD"
    assert "direct request" in result.reason
```

For timeout tests, use `side_effect=TimeoutError()`.
For error tests, use `side_effect=RuntimeError("connection lost")`.

Remove `DiscretionConfig` from test imports. Remove `httpx` from test imports.

- [ ] **Step 5: Run all discretion tests**

Run: `uv run pytest tests/connectors/live_listener/test_discretion.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/butlers/connectors/discretion.py tests/connectors/live_listener/test_discretion.py
git commit -m "refactor: rewire DiscretionEvaluator to use DiscretionLLMCaller protocol"
```

---

## Chunk 4: Wire connectors to the new dispatcher

### Task 7: Update LiveListener connector

**Files:**
- Modify: `src/butlers/connectors/live_listener/connector.py`

The connector currently:
1. Creates a `DiscretionConfig(env_prefix="LIVE_LISTENER_")` at line 209
2. Creates per-mic `DiscretionEvaluator(source_name=mic, config=discretion_config)` at line 225

It needs to:
1. Accept or create a `DiscretionDispatcher` (needs a `pool` — check if the connector already has access to a DB pool)
2. Pass the dispatcher to each evaluator

- [ ] **Step 1: Check connector's DB pool access**

Read `src/butlers/connectors/live_listener/connector.py` constructor to find if it has an `asyncpg.Pool`. If not, it needs one injected (or the dispatcher can be injected directly).

The connector likely gets a DB pool for heartbeat or contact weight resolution. If it already has `self._db_pool`, use that. If not, the `DiscretionDispatcher` should be injected into the connector's constructor.

- [ ] **Step 2: Update connector imports**

Replace:
```python
from butlers.connectors.discretion import (
    DiscretionConfig,
    DiscretionEvaluator,
    DiscretionResult,
)
```

With:
```python
from butlers.connectors.discretion import (
    DiscretionEvaluator,
    DiscretionResult,
)
from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
```

- [ ] **Step 3: Update connector initialization**

Replace the `DiscretionConfig` creation and evaluator construction with dispatcher injection. Where `discretion_config = DiscretionConfig(env_prefix="LIVE_LISTENER_")` was:

```python
self._discretion_dispatcher = DiscretionDispatcher(pool=self._db_pool)
```

Where `DiscretionEvaluator(source_name=mic, config=discretion_config)` was:

```python
DiscretionEvaluator(
    source_name=mic,
    dispatcher=self._discretion_dispatcher,
)
```

- [ ] **Step 4: Run existing connector tests**

Run: `uv run pytest tests/connectors/live_listener/ -v --tb=short`
Expected: PASS (adjust mocks as needed if tests touch discretion internals)

- [ ] **Step 5: Commit**

```bash
git add src/butlers/connectors/live_listener/connector.py
git commit -m "feat(live-listener): use DiscretionDispatcher for discretion evaluation"
```

---

### Task 8: Update Telegram User Client connector

**Files:**
- Modify: `src/butlers/connectors/telegram_user_client.py`

Same pattern as Task 7. The telegram connector already has `db_pool` passed to its constructor (used for `ContactWeightResolver`).

- [ ] **Step 1: Update imports**

Replace `DiscretionConfig` import with `DiscretionDispatcher` import.

- [ ] **Step 2: Update initialization**

At line 272, replace:
```python
self._discretion_config = DiscretionConfig(env_prefix="TELEGRAM_USER_")
```
With:
```python
self._discretion_dispatcher = DiscretionDispatcher(pool=db_pool) if db_pool is not None else None
```

At lines 688 and 993, replace `DiscretionEvaluator` construction to pass `dispatcher=self._discretion_dispatcher`.

The guard `if self._discretion_config.llm_url and normalized_text:` (line 686) needs to change — the dispatcher doesn't have a URL check. Replace with `if self._discretion_dispatcher is not None and normalized_text:`.

- [ ] **Step 3: Run telegram connector tests**

Run: `uv run pytest tests/connectors/ -k telegram -v --tb=short`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/butlers/connectors/telegram_user_client.py
git commit -m "feat(telegram): use DiscretionDispatcher for discretion evaluation"
```

---

## Chunk 5: Cleanup and validation

### Task 9: Remove dead env var code and httpx dependency from discretion

**Files:**
- Modify: `src/butlers/connectors/discretion.py`

- [ ] **Step 1: Verify no remaining references to removed symbols**

Run: `uv run ruff check src/butlers/connectors/discretion.py --output-format concise`
Expected: No errors (or fix any import/reference issues)

Run grep to check nothing else imports the removed symbols:

```bash
uv run python -c "
from butlers.connectors.discretion import (
    ContextEntry, ContextWindow, DiscretionResult, DiscretionEvaluator,
    ContactWeightResolver, WeightTier, Verdict,
    _build_user_prompt, _parse_verdict,
)
print('All expected symbols importable')
"
```

Expected: `All expected symbols importable`

- [ ] **Step 2: Check that httpx is no longer imported in discretion.py**

```bash
uv run python -c "
import ast, sys
tree = ast.parse(open('src/butlers/connectors/discretion.py').read())
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if 'httpx' in alias.name:
                print(f'ERROR: httpx still imported at line {node.lineno}')
                sys.exit(1)
    if isinstance(node, ast.ImportFrom) and node.module and 'httpx' in node.module:
        print(f'ERROR: httpx still imported at line {node.lineno}')
        sys.exit(1)
print('OK: no httpx imports')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/butlers/connectors/discretion.py
git commit -m "cleanup: remove dead httpx/env-var code from discretion layer"
```

---

### Task 10: Run full quality gates

- [ ] **Step 1: Lint**

Run: `uv run ruff check src/ tests/ --output-format concise`
Expected: No errors

- [ ] **Step 2: Format check**

Run: `uv run ruff format --check src/ tests/ -q`
Expected: No formatting issues (or run `uv run ruff format src/ tests/` to fix)

- [ ] **Step 3: Run full test suite**

```bash
mkdir -p .tmp/test-logs
PYTEST_LOG=".tmp/test-logs/pytest-discretion-migration-$(date +%Y%m%d-%H%M%S).log"
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --maxfail=3 --tb=short >"$PYTEST_LOG" 2>&1 || tail -n 120 "$PYTEST_LOG"
```

Expected: All tests pass

- [ ] **Step 4: Final commit if any fixups**

```bash
git add -u
git commit -m "fix: post-migration lint and test fixes"
```

---

## Summary of what changes

| Before | After |
|--------|-------|
| `DiscretionConfig` reads env vars for URL/model | Model resolved from `shared.model_catalog` (discretion tier) |
| `_call_llm` does raw `httpx.post` to Ollama | `DiscretionDispatcher.call()` goes through `RuntimeAdapter.invoke()` |
| No concurrency control on discretion calls | `asyncio.Semaphore(4)` in dispatcher |
| Model changes require env var updates + restart | Model changes via Settings UI, immediate effect |
| Discretion decoupled from adapter ecosystem | Discretion uses same adapter registry as butler sessions |
| `httpx` is a discretion dependency | `httpx` removed from discretion (adapter handles transport) |

## What stays the same

- `ContextWindow` — unchanged
- `ContactWeightResolver` — unchanged
- `_build_user_prompt` / `_parse_verdict` — unchanged
- `DiscretionResult` / `ContextEntry` / `Verdict` — unchanged
- `WeightTier` — unchanged
- Fail-open/closed behavior based on sender weight — unchanged
- Owner bypass — unchanged
