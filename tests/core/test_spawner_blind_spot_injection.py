"""bu-2jtfw.13: the blind-spot preamble at the real spawner seam.

Covers the acceptance-criteria checks that must be proven end-to-end through
``Spawner.trigger()`` rather than at the unit level of
``butlers.core.expected_signals`` alone:

- (1) Composition byte-identity: every declared signal PRESENT (or no
  declared dependency at all) leaves ``invoke_kwargs['system_prompt']``
  unchanged from today's four-layer composition.
- (4) A stubbed stale connector's absence reaches
  ``invoke_kwargs['system_prompt']`` at the spawner.py dispatch seam.
- (3) A butler with no declared dependency for a namespace gets no line for
  it, proven at the spawner seam (not just the registry unit level).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from butlers.config import ButlerConfig
from butlers.core.expected_signals import BLIND_SPOT_QUERY_FAILED_TEXT
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


def _make_config(
    name: str = "health", port: int = 9200, modules: dict | None = None
) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        env_required=[],
        env_optional=[],
        modules=modules or {},
    )


class _SystemPromptCapturingAdapter(RuntimeAdapter):
    """Records the composed ``system_prompt`` handed to ``invoke()``."""

    def __init__(self) -> None:
        self.captured_system_prompts: list[str] = []

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict,
        env: dict,
        **kwargs: Any,
    ) -> tuple:
        self.captured_system_prompts.append(system_prompt)
        return "Done", [], None

    def build_config_file(self, mcp_servers: dict, tmp_dir: Any) -> Any:
        config_path = tmp_dir / "mock.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir: Any) -> str:
        return ""


class _FakeExpectedSignalsPool:
    """Stands in for the shared asyncpg pool across the two queries this layer issues.

    The declared-signal query (``public.expected_signals``) returns
    *signal_rows*; the per-row connector-measurability query
    (``public.v_qa_connector_state``) returns *connector_rows*.
    """

    def __init__(
        self,
        signal_rows: list[dict[str, Any]],
        connector_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._signal_rows = signal_rows
        self._connector_rows = connector_rows or []
        self.queries: list[str] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        if "v_qa_connector_state" in query:
            return self._connector_rows
        if "public.expected_signals" in query:
            return self._signal_rows
        return []

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        return "OK"

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        return None


async def test_all_present_signal_is_byte_identical_to_no_declared_dependency(
    tmp_path: Path,
) -> None:
    """AC1: a PRESENT declared signal injects no layer; the prompt is unchanged."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    now = datetime.now(UTC)

    present_pool = _FakeExpectedSignalsPool(
        signal_rows=[
            {
                "signal_key": "health:measurement-gap:weight",
                "producer": "owner",
                "producer_endpoint_identity": None,
                "expected_cadence_seconds": int(timedelta(days=14).total_seconds()),
                "last_observed_at": now - timedelta(days=1),
            }
        ],
    )
    no_dependency_pool = _FakeExpectedSignalsPool(signal_rows=[])

    present_adapter = _SystemPromptCapturingAdapter()
    present_spawner = Spawner(
        config=_make_config(name="health", modules={"health": {}}),
        config_dir=config_dir,
        pool=present_pool,
        runtime=present_adapter,
    )
    await present_spawner.trigger(prompt="do task", trigger_source="trigger")

    # Same butler name/config_dir (so the on-disk base prompt is identical) —
    # only the declared-dependency module set differs, exercising the "no
    # declared dependency at all" no-op path.
    bare_adapter = _SystemPromptCapturingAdapter()
    bare_spawner = Spawner(
        config=_make_config(name="health", modules={}),
        config_dir=config_dir,
        pool=no_dependency_pool,
        runtime=bare_adapter,
    )
    await bare_spawner.trigger(prompt="do task", trigger_source="trigger")

    assert present_adapter.captured_system_prompts[-1] == bare_adapter.captured_system_prompts[-1]
    assert "Blind Spot" not in present_adapter.captured_system_prompts[-1]
    assert BLIND_SPOT_QUERY_FAILED_TEXT not in present_adapter.captured_system_prompts[-1].lower()


async def test_stale_connector_absence_reaches_invoke_kwargs_system_prompt(
    tmp_path: Path,
) -> None:
    """AC4: a stubbed stale connector's absence reaches invoke_kwargs['system_prompt']."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    now = datetime.now(UTC)

    stale_pool = _FakeExpectedSignalsPool(
        signal_rows=[
            {
                "signal_key": "health:measurement-gap:weight",
                "producer": "connector:google_health",
                "producer_endpoint_identity": "google_health:user:owner",
                "expected_cadence_seconds": int(timedelta(days=14).total_seconds()),
                "last_observed_at": now - timedelta(days=60),
            }
        ],
        connector_rows=[{"state": "offline", "last_heartbeat_at": now - timedelta(days=10)}],
    )

    adapter = _SystemPromptCapturingAdapter()
    spawner = Spawner(
        config=_make_config(modules={"health": {}}),
        config_dir=config_dir,
        pool=stale_pool,
        runtime=adapter,
    )
    await spawner.trigger(prompt="do task", trigger_source="trigger")

    captured = adapter.captured_system_prompts[-1]
    assert "signal=health:measurement-gap:weight" in captured
    assert "producer=connector:google_health" in captured
    assert "last_observed_at=" in captured
    assert "Evaluated at" in captured  # evaluator's own clock


async def test_butler_without_declared_module_gets_no_line_at_spawner_seam(
    tmp_path: Path,
) -> None:
    """AC3: a butler with no [modules.health] never queries or surfaces that namespace."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    class _ExplodingPool(_FakeExpectedSignalsPool):
        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            raise AssertionError("declared_signal_patterns() was empty; must never query")

    adapter = _SystemPromptCapturingAdapter()
    spawner = Spawner(
        config=_make_config(name="general", modules={}),
        config_dir=config_dir,
        pool=_ExplodingPool(signal_rows=[]),
        runtime=adapter,
    )
    await spawner.trigger(prompt="do task", trigger_source="trigger")

    assert "Blind Spot" not in adapter.captured_system_prompts[-1]
