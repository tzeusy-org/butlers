"""Regression coverage for approvals ToolMeta handoff at daemon startup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.config import load_config
from butlers.daemon import ButlerDaemon
from butlers.modules.base import ToolMeta

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _MetadataModule:
    name = "email"

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {"email_send_message": ToolMeta(arg_sensitivities={"to": True, "body": False})}


class _ApprovalsModuleProbe:
    name = "approvals"

    def __init__(self) -> None:
        self.tool_metadata: dict[str, ToolMeta] | None = None

    def set_tool_metadata(self, metadata: dict[str, ToolMeta]) -> None:
        self.tool_metadata = metadata

    def set_approval_policy(self, _policy: object) -> None:
        pass


class _UnexpectedMetadataModule:
    name = "email"

    def tool_metadata(self) -> dict[str, ToolMeta]:
        raise AssertionError("ToolMeta must not be collected without an approvals module")


@pytest.mark.parametrize("roster_name", ("relationship", "home"))
async def test_disabled_rosters_inject_metadata_for_manual_approvals(
    roster_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured ApprovalsModule receives ToolMeta even when it has no gates."""
    config = load_config(_REPO_ROOT / "roster" / roster_name)
    approvals = _ApprovalsModuleProbe()
    apply_gates = AsyncMock()
    monkeypatch.setattr("butlers.daemon.apply_approval_gates", apply_gates)

    daemon = SimpleNamespace(
        config=config,
        _active_modules=[_MetadataModule(), approvals],
    )

    result = await ButlerDaemon._apply_approval_gates(daemon)

    assert result == {}
    assert approvals.tool_metadata == {
        "email_send_message": ToolMeta(arg_sensitivities={"to": True, "body": False})
    }
    apply_gates.assert_not_awaited()


async def test_unconfigured_approvals_module_keeps_gate_setup_inactive() -> None:
    """No configured approvals module means no gate or metadata setup work."""
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="review", modules={}),
        _active_modules=[_UnexpectedMetadataModule()],
    )

    result = await ButlerDaemon._apply_approval_gates(daemon)

    assert result == {}


async def test_enabled_gates_receive_the_deterministic_approval_push_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon, not an LLM tool, owns park → Switchboard push wiring."""
    config = load_config(_REPO_ROOT / "roster" / "messenger")
    approvals = _ApprovalsModuleProbe()
    apply_gates = AsyncMock(return_value={})
    push_runtime = object()
    monkeypatch.setattr("butlers.daemon.apply_approval_gates", apply_gates)

    daemon = SimpleNamespace(
        config=config,
        _active_modules=[_MetadataModule(), approvals],
        mcp=object(),
        db=SimpleNamespace(pool=object()),
        _build_approval_push_runtime=lambda: push_runtime,
    )

    result = await ButlerDaemon._apply_approval_gates(daemon)

    assert result == {}
    assert apply_gates.await_args.kwargs["approval_push_runtime"] is push_runtime
