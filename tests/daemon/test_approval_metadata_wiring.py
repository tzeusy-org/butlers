"""Regression coverage for approvals ToolMeta handoff at daemon startup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP as RuntimeFastMCP

from butlers.config import load_config
from butlers.daemon import ButlerDaemon
from butlers.modules.approvals.module import ApprovalsConfig, ApprovalsModule
from butlers.modules.base import ToolMeta
from butlers.modules.email import EmailConfig, EmailModule
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from butlers.modules.whatsapp import WhatsAppConfig, WhatsAppModule
from tests.modules.test_module_approvals import MockDB

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _MetadataModule:
    name = "email"

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {"email_send_message": ToolMeta(arg_sensitivities={"to": True, "body": False})}


class _ApprovalsModuleProbe:
    name = "approvals"

    def __init__(self) -> None:
        self.received_tool_metadata: dict[str, ToolMeta] | None = None
        self.tool_executor = None

    def set_tool_metadata(self, metadata: dict[str, ToolMeta]) -> None:
        self.received_tool_metadata = metadata

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {}

    def set_approval_policy(self, _policy: object) -> None:
        pass

    def set_tool_executor(self, executor: object) -> None:
        self.tool_executor = executor


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
        mcp=RuntimeFastMCP(f"test-{roster_name}"),
    )

    result = await ButlerDaemon._apply_approval_gates(daemon)

    assert result == {}
    assert approvals.received_tool_metadata == {
        "email_send_message": ToolMeta(arg_sensitivities={"to": True, "body": False})
    }
    assert approvals.tool_executor is not None
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

    warn_if_secret_missing = AsyncMock()
    daemon = SimpleNamespace(
        config=config,
        _active_modules=[_MetadataModule(), approvals],
        mcp=object(),
        db=SimpleNamespace(pool=object()),
        _build_approval_push_runtime=lambda: push_runtime,
        _warn_if_approval_callback_secret_missing=warn_if_secret_missing,
    )

    result = await ButlerDaemon._apply_approval_gates(daemon)

    assert result == {}
    assert apply_gates.await_args.kwargs["approval_push_runtime"] is push_runtime
    assert daemon._approval_push_runtime is push_runtime
    warn_if_secret_missing.assert_awaited_once()


async def test_relationship_registration_dispatches_legacy_merge_via_memory_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real Relationship module registration owns legacy merge recovery.

    Relationship enables the approvals action surface but has no gated tools.
    Its registered executor must therefore resolve the canonical memory MCP
    callable directly, rather than re-enter a gate or depend on another
    butler. The legacy action name is deliberately mapped only at this runtime
    boundary; the row itself remains untouched until the successful execution
    transition persists its result and audit event.
    """
    config = load_config(_REPO_ROOT / "roster" / "relationship")
    db = MockDB()
    db.schema = "relationship"
    memory = MemoryModule()
    approvals = ApprovalsModule()

    daemon = ButlerDaemon(_REPO_ROOT / "roster" / "relationship", db=db)
    daemon.config = config
    daemon.mcp = RuntimeFastMCP("relationship")
    daemon._modules = [memory, approvals]
    daemon._module_configs = {
        "memory": MemoryModuleConfig(**config.modules["memory"]),
        "approvals": ApprovalsConfig(**config.modules["approvals"]),
    }

    await daemon._register_module_tools()

    merge = AsyncMock(
        return_value={
            "source_entity_id": "source",
            "target_entity_id": "target",
            "facts_repointed": 2,
        }
    )
    monkeypatch.setattr("butlers.modules.memory.tools.entities.entity_merge", merge)
    monkeypatch.setattr(memory, "_get_or_create_chronicler_pool", AsyncMock(return_value=None))
    monkeypatch.setattr(memory, "_get_or_create_relationship_pool", AsyncMock(return_value=None))

    originals = await daemon._apply_approval_gates()

    assert originals == {}
    assert approvals._tool_executor is not None

    action_id = db._insert_action(
        tool_name="entity_merge",
        tool_args={"source_entity_id": "source", "target_entity_id": "target"},
        status="approved",
    )
    dispatch_tool = await daemon.mcp.get_tool("dispatch_approved_action")

    result = await dispatch_tool.fn(action_id=str(action_id))

    assert result["status"] == "executed"
    assert result["tool_name"] == "entity_merge"  # provenance is not rewritten
    assert result["execution_result"]["success"] is True
    merge.assert_awaited_once_with(db, "source", "target", chronicler_pool=None)
    assert db.pending_actions[action_id]["status"] == "executed"
    event_types = [call["args"][0] for call in db.approval_events]
    assert "action_execution_succeeded" in event_types


async def test_messenger_registered_email_reply_replays_approved_parked_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical parked reply executes once through Messenger's native handler."""
    config = load_config(_REPO_ROOT / "roster" / "messenger")
    db = MockDB()
    db.schema = "messenger"
    email = EmailModule()
    approvals = ApprovalsModule()

    daemon = ButlerDaemon(_REPO_ROOT / "roster" / "messenger", db=db)
    daemon.config = config
    daemon.mcp = RuntimeFastMCP("messenger")
    daemon._modules = [email, approvals]
    daemon._module_configs = {
        "email": EmailConfig(**config.modules["email"]),
        "approvals": ApprovalsConfig(**config.modules["approvals"]),
    }

    await daemon._register_module_tools()
    reply = AsyncMock(return_value={"status": "sent", "thread_id": "gmail-thread-7"})
    monkeypatch.setattr(email, "_reply_to_thread", reply)
    await daemon._apply_approval_gates()

    action_id = db._insert_action(
        tool_name="email_reply_to_thread",
        tool_args={
            "to": "chatterbox97@gmail.com",
            "thread_id": "gmail-thread-7",
            "body": "Thanks for the update.",
            "subject": "[relationship] Re: Update",
        },
        status="pending",
    )
    approved = await approvals._approve_action(
        str(action_id),
        actor={
            "type": "human",
            "id": "owner",
            "name": "Owner",
            "authenticated": True,
            "roles": ["owner"],
        },
    )
    assert approved["status"] == "executed"

    dispatch_tool = await daemon.mcp.get_tool("dispatch_approved_action")
    result = await dispatch_tool.fn(action_id=str(action_id))

    assert result["status"] == "executed"
    reply.assert_awaited_once_with(
        "chatterbox97@gmail.com",
        "gmail-thread-7",
        "Thanks for the update.",
        "[relationship] Re: Update",
    )
    assert db.pending_actions[action_id]["status"] == "executed"
    event_types = [call["args"][0] for call in db.approval_events]
    assert event_types == [
        "action_approved",
        "action_execution_succeeded",
    ]


@pytest.mark.parametrize(
    ("send_enabled", "expected_status", "expected_execution_event"),
    (
        (False, "approved", "action_execution_failed"),
        (True, "executed", "action_execution_succeeded"),
    ),
)
async def test_messenger_whatsapp_replay_respects_runtime_send_policy(
    monkeypatch: pytest.MonkeyPatch,
    send_enabled: bool,
    expected_status: str,
    expected_execution_event: str,
) -> None:
    config = load_config(_REPO_ROOT / "roster" / "messenger")
    db = MockDB()
    db.schema = "messenger"
    whatsapp = WhatsAppModule()
    approvals = ApprovalsModule()

    daemon = ButlerDaemon(_REPO_ROOT / "roster" / "messenger", db=db)
    daemon.config = config
    daemon.mcp = RuntimeFastMCP("messenger")
    daemon._modules = [whatsapp, approvals]
    daemon._module_configs = {
        "whatsapp": WhatsAppConfig(send_tools=True, send_enabled=send_enabled),
        "approvals": ApprovalsConfig(**config.modules["approvals"]),
    }

    await daemon._register_module_tools()
    send = AsyncMock(return_value={"status": "sent", "message_id": "wa-7"})
    monkeypatch.setattr(whatsapp, "_send_message", send)
    await daemon._apply_approval_gates()

    action_id = db._insert_action(
        tool_name="whatsapp_send_message",
        tool_args={
            "recipient": "15551234567@s.whatsapp.net",
            "text": "[relationship] Policy check",
        },
        status="pending",
    )
    result = await approvals._approve_action(
        str(action_id),
        actor={
            "type": "human",
            "id": "owner",
            "name": "Owner",
            "authenticated": True,
            "roles": ["owner"],
        },
    )

    assert result["status"] == expected_status
    if send_enabled:
        assert db.pending_actions[action_id]["execution_result"] is not None
        send.assert_awaited_once_with(
            recipient="15551234567@s.whatsapp.net",
            text="[relationship] Policy check",
        )
    else:
        assert db.pending_actions[action_id]["execution_result"] is None
        send.assert_not_awaited()
    event_types = [call["args"][0] for call in db.approval_events]
    assert expected_execution_event in event_types
