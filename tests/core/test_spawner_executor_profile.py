"""Engineering-executor launch profile (bu-ejow5p.8 sibling: bu-ejow5p.5, RFC 0038 D18).

A ``qa`` session runs a repository engineer in an isolated worktree. It must get one small
core-owned system prompt: no butler persona, no memory, no situational or timezone preamble, no
blind-spot disclosure, no DB prompt override. Every other trigger keeps the full composition.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.qa.executor_profile import (
    EXECUTOR_PROFILE_SOURCE,
    ExecutorProfileUnavailable,
    executor_prompt_receipt,
)
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.spawner import Spawner
from tests.core.test_core_spawner import _FAKE_CATALOG_ID, MockAdapter, _make_config

PERSONA = "You are the test butler. NEVER read source code or use the shell."
MEMORY_FACT = "# Memory Context\n- owner is flying to Lisbon on 12 March"
RESOLUTION = (
    DEFAULT_RUNTIME_TYPE,
    "test-model",
    [],
    _FAKE_CATALOG_ID,
    1800,
    "workhorse",
)


def _config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "CLAUDE.md").write_text(PERSONA)
    return config_dir


async def _run(tmp_path: Path, trigger_source: str, **spawner_kwargs):
    adapter = MockAdapter(result_text="ok", capture=True)
    with (
        patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as create,
        patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=RESOLUTION,
        ),
        patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=MEMORY_FACT,
        ) as memory,
        patch(
            "butlers.core.spawner.fetch_situational_context_preamble",
            new_callable=AsyncMock,
            return_value="SITUATIONAL: owner lives in Lisbon",
        ) as situational,
        patch(
            "butlers.core.spawner.fetch_system_prompt_override",
            new_callable=AsyncMock,
            return_value="DB OVERRIDE PERSONA",
        ),
    ):
        create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
        spawner = Spawner(
            config=_make_config(modules={"memory": {}}),
            config_dir=_config_dir(tmp_path),
            pool=AsyncMock(),
            runtime=adapter,
            **spawner_kwargs,
        )
        await spawner.trigger("investigate fingerprint abc", trigger_source)
    return adapter.calls[0], create.call_args.kwargs, memory, situational


async def test_qa_session_gets_only_the_executor_profile(tmp_path: Path) -> None:
    call, create_kwargs, memory, situational = await _run(tmp_path, "qa")

    profile = executor_prompt_receipt()
    assert call["system_prompt"] == profile.prompt
    assert "NEVER read source code" not in call["system_prompt"]
    assert "Lisbon" not in call["system_prompt"]
    assert "DB OVERRIDE" not in call["system_prompt"]
    memory.assert_not_called()
    situational.assert_not_called()

    assert create_kwargs["effective_system_prompt"] == profile.prompt
    assert create_kwargs["prompt_digest"] == hashlib.sha256(profile.prompt.encode()).hexdigest()
    assert [entry["source"] for entry in create_kwargs["prompt_provenance"]] == [
        EXECUTOR_PROFILE_SOURCE
    ]
    assert EXECUTOR_PROFILE_SOURCE == "engineering_executor_profile@v1"


async def test_route_session_keeps_the_persona_and_memory(tmp_path: Path) -> None:
    call, create_kwargs, memory, _ = await _run(tmp_path, "route")

    assert "DB OVERRIDE PERSONA" in call["system_prompt"]
    assert MEMORY_FACT in call["system_prompt"]
    assert EXECUTOR_PROFILE_SOURCE not in [e["source"] for e in create_kwargs["prompt_provenance"]]
    memory.assert_called_once()


async def test_healing_session_is_not_an_executor_in_this_change(tmp_path: Path) -> None:
    call, _, _, _ = await _run(tmp_path, "healing")

    assert call["system_prompt"] != executor_prompt_receipt().prompt


def test_profile_is_deterministic_and_describes_the_engineer_role() -> None:
    first, second = executor_prompt_receipt(), executor_prompt_receipt()
    assert first.digest == second.digest
    text = first.prompt.lower()
    for required in ("repository engineer", "worktree", "untrusted"):
        assert required in text
    assert [e.source for e in first.provenance] == [EXECUTOR_PROFILE_SOURCE]


def test_missing_profile_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from butlers.core.qa import executor_profile

    monkeypatch.setattr(executor_profile, "_PROFILE_PATH", tmp_path / "absent.md")
    with pytest.raises(ExecutorProfileUnavailable):
        executor_profile.executor_prompt_receipt()


async def test_executor_resolves_its_exact_rung_while_others_may_fall_through(
    tmp_path: Path,
) -> None:
    seen: dict[str, dict] = {}
    for trigger in ("qa", "route"):
        adapter = MockAdapter(result_text="ok", capture=True)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=RESOLUTION,
            ) as resolve,
            patch("butlers.core.spawner.fetch_memory_context", new_callable=AsyncMock),
        ):
            create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await Spawner(
                config=_make_config(),
                config_dir=_config_dir(tmp_path / trigger),
                pool=AsyncMock(),
                runtime=adapter,
            ).trigger("x", trigger)
        seen[trigger] = resolve.call_args.kwargs
    assert seen["qa"]["allow_tier_fallthrough"] is False
    assert seen["route"]["allow_tier_fallthrough"] is True
