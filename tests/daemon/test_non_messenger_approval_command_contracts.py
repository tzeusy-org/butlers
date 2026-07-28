"""Replay contracts for direct non-Messenger approval producers.

These regressions exercise the real owner-daemon approval path rather than
calling the dashboard submission handlers a second time.  A direct producer
must either park an exact registered command or reject before it creates a
durable action that cannot be replayed after human approval.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from butlers.config import load_config
from butlers.daemon import ButlerDaemon
from butlers.modules.approvals.command_contracts import (
    EXECUTABLE_DIRECT_COMMANDS,
    NON_MESSENGER_PRODUCER_INVENTORY,
    ApprovalCommandContractError,
    validate_owner_command_registry,
)
from butlers.modules.approvals.module import ApprovalsConfig, ApprovalsModule
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from butlers.modules.registry import default_registry
from tests.modules.test_module_approvals import MockDB

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Roster modules are installed dynamically by ModuleRegistry.  Load that
# registry before importing the switchboard module's generated alias.
default_registry()
from butlers.modules._roster_switchboard import (  # noqa: E402
    SwitchboardModule,
    SwitchboardModuleConfig,
)


class _ConnectorRegistryPool:
    """Tiny connector_registry double for the native Switchboard handler."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {
            ("gmail", "owner@example.com"): {
                "connector_type": "gmail",
                "endpoint_identity": "owner@example.com",
                "deleted_at": None,
            }
        }

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        connector_type, endpoint_identity = str(args[0]), str(args[1])
        row = self.rows.get((connector_type, endpoint_identity))

        if "UPDATE connector_registry" in query:
            if row is None or row["deleted_at"] is not None:
                return None
            row["deleted_at"] = "now"
            return dict(row)

        if "FROM connector_registry" in query:
            return dict(row) if row is not None else None

        raise AssertionError(f"Unexpected connector_registry query: {query}")


class _SwitchboardApprovalDB(MockDB):
    """One pool double for approval persistence and connector lifecycle state."""

    def __init__(self, lifecycle_pool: _ConnectorRegistryPool) -> None:
        super().__init__()
        self._lifecycle_pool = lifecycle_pool

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "connector_registry" in query:
            return await self._lifecycle_pool.fetchrow(query, *args)
        return await super().fetchrow(query, *args)


class _RelationshipMemoryDB(MockDB):
    """Approval test store plus the one fact mutation used by reclassification."""

    def __init__(self, fact_id: uuid.UUID) -> None:
        super().__init__()
        self.facts = {
            fact_id: {
                "id": fact_id,
                "permanence": "stable",
                "decay_rate": 0.002,
                "validity": "active",
            }
        }

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "UPDATE facts" in query:
            fact_id = args[0]
            row = self.facts.get(fact_id)
            if row is None or row["validity"] != "active":
                return None
            row["permanence"] = args[1]
            row["decay_rate"] = args[2]
            return dict(row)
        return await super().fetchrow(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        """Preserve the gate's full pending-row shape for approval replay."""
        if "INSERT INTO pending_actions" in query:
            action_id = args[0]
            self.pending_actions[action_id] = {
                "id": action_id,
                "tool_name": args[1],
                "tool_args": args[2],
                "status": "pending",
                "requested_at": args[5],
                "agent_summary": args[3],
                "session_id": args[4],
                "expires_at": args[6],
                "decided_by": None,
                "decided_at": None,
                "execution_result": None,
                "approval_rule_id": None,
                "why": args[7],
                "evidence": args[8],
                "blast_radius": args[9],
                "reversibility": args[10],
            }
            return "INSERT 0 1"
        return await super().execute(query, *args)


def _human_actor() -> dict[str, Any]:
    return {
        "type": "human",
        "id": str(uuid.uuid4()),
        "authenticated": True,
    }


async def _switchboard_approval_daemon() -> tuple[
    ButlerDaemon, ApprovalsModule, _SwitchboardApprovalDB, _ConnectorRegistryPool
]:
    config = load_config(_REPO_ROOT / "roster" / "switchboard")
    approvals = ApprovalsModule()
    switchboard = SwitchboardModule()
    lifecycle_pool = _ConnectorRegistryPool()
    approval_db = _SwitchboardApprovalDB(lifecycle_pool)
    approval_db.schema = "switchboard"

    daemon = ButlerDaemon(_REPO_ROOT / "roster" / "switchboard", db=approval_db)
    daemon.config = config
    daemon.mcp = FastMCP("switchboard")
    daemon._modules = [switchboard, approvals]
    daemon._module_configs = {
        "switchboard": SwitchboardModuleConfig(**config.modules["switchboard"]),
        "approvals": ApprovalsConfig(**config.modules["approvals"]),
    }

    await daemon._register_module_tools()
    await daemon._apply_approval_gates()
    return daemon, approvals, approval_db, lifecycle_pool


async def _relationship_approval_daemon(
    fact_id: uuid.UUID,
) -> tuple[ButlerDaemon, ApprovalsModule, _RelationshipMemoryDB]:
    config = load_config(_REPO_ROOT / "roster" / "relationship")
    approvals = ApprovalsModule()
    memory = MemoryModule()
    approval_db = _RelationshipMemoryDB(fact_id)
    approval_db.schema = "relationship"

    daemon = ButlerDaemon(_REPO_ROOT / "roster" / "relationship", db=approval_db)
    daemon.config = config
    daemon.mcp = FastMCP("relationship")
    daemon._modules = [memory, approvals]
    daemon._module_configs = {
        "memory": MemoryModuleConfig(**config.modules["memory"]),
        "approvals": ApprovalsConfig(**config.modules["approvals"]),
    }

    await daemon._register_module_tools()
    await daemon._apply_approval_gates()
    return daemon, approvals, approval_db


async def test_approved_connector_disconnect_executes_exact_switchboard_command() -> None:
    """A parked connector action becomes executed only after its native soft delete."""
    _, approvals, approval_db, lifecycle_pool = await _switchboard_approval_daemon()
    action_id = approval_db._insert_action(
        tool_name="connector_disconnect",
        tool_args={"connector_type": "gmail", "endpoint_identity": "owner@example.com"},
        status="pending",
    )

    result = await approvals._approve_action(str(action_id), actor=_human_actor())

    assert result["status"] == "executed"
    assert approval_db.pending_actions[action_id]["status"] == "executed"
    assert lifecycle_pool.rows[("gmail", "owner@example.com")]["deleted_at"] == "now"


async def test_approved_memory_reclassification_executes_exact_relationship_command() -> None:
    """Curation's parked fact operation updates only the approved active fact."""
    fact_id = uuid.uuid4()
    _, approvals, approval_db = await _relationship_approval_daemon(fact_id)
    action_id = approval_db._insert_action(
        tool_name="memory_reclassify",
        tool_args={
            "memory_type": "fact",
            "memory_id": str(fact_id),
            "permanence_target": "volatile",
        },
        status="pending",
    )

    result = await approvals._approve_action(str(action_id), actor=_human_actor())

    assert result["status"] == "executed"
    assert approval_db.pending_actions[action_id]["status"] == "executed"
    assert approval_db.facts[fact_id]["permanence"] == "volatile"


async def test_memory_reclassification_rejects_non_volatile_target_without_mutation() -> None:
    """The curation replay command cannot become a general permanence editor."""
    fact_id = uuid.uuid4()
    _, approvals, approval_db = await _relationship_approval_daemon(fact_id)
    action_id = approval_db._insert_action(
        tool_name="memory_reclassify",
        tool_args={
            "memory_type": "fact",
            "memory_id": str(fact_id),
            "permanence_target": "permanent",
        },
        status="pending",
    )

    result = await approvals._approve_action(str(action_id), actor=_human_actor())

    assert result["status"] == "approved"
    assert approval_db.pending_actions[action_id]["status"] == "approved"
    assert approval_db.pending_actions[action_id]["execution_result"] is None
    assert approval_db.facts[fact_id]["permanence"] == "stable"


async def test_memory_reclassification_broad_rule_parks_instead_of_auto_executing() -> None:
    """An unpinned standing rule cannot authorize a different fact mutation."""
    fact_id = uuid.uuid4()
    daemon, _, approval_db = await _relationship_approval_daemon(fact_id)
    approval_db._insert_rule(
        tool_name="memory_reclassify",
        arg_constraints={},
    )
    tool = await daemon.mcp.get_tool("memory_reclassify")

    result = await tool.fn(
        memory_type="fact",
        memory_id=str(fact_id),
        permanence_target="volatile",
        _why="Episodic-predicate curation proposed this bounded reclassification.",
        _evidence=[],
    )

    assert result["status"] == "pending_approval"
    assert approval_db.facts[fact_id]["permanence"] == "stable"


async def test_direct_memory_reclassification_is_parked_then_replayed_by_the_gate() -> None:
    """A normal Relationship call cannot mutate a fact before owner approval."""
    fact_id = uuid.uuid4()
    daemon, approvals, approval_db = await _relationship_approval_daemon(fact_id)
    tool = await daemon.mcp.get_tool("memory_reclassify")

    assert tool is not None
    parked = await tool.fn(
        memory_type="fact",
        memory_id=str(fact_id),
        permanence_target="volatile",
        _why="The owner requested a reviewable memory reclassification.",
        _evidence=[],
        _blast_radius="self",
        _reversibility="reversible",
    )

    assert parked["status"] == "pending_approval"
    action_id = uuid.UUID(parked["action_id"])
    stored = approval_db.pending_actions[action_id]
    assert stored["status"] == "pending"
    assert stored["tool_name"] == "memory_reclassify"
    assert stored["tool_args"] == {
        "memory_type": "fact",
        "memory_id": str(fact_id),
        "permanence_target": "volatile",
    }
    assert approval_db.facts[fact_id]["permanence"] == "stable"

    result = await approvals._approve_action(str(action_id), actor=_human_actor())

    assert result["status"] == "executed"
    assert approval_db.pending_actions[action_id]["status"] == "executed"
    assert approval_db.facts[fact_id]["permanence"] == "volatile"


async def test_historic_unknown_command_stays_approved_and_truthful() -> None:
    """No alias or argument guess turns historic evidence into a new command."""
    _, approvals, approval_db, _ = await _switchboard_approval_daemon()
    action_id = approval_db._insert_action(
        tool_name="connector_rotate_token",
        tool_args={"connector_type": "gmail", "endpoint_identity": "owner@example.com"},
        status="approved",
    )

    result = await approvals._dispatch_approved_action_by_id(str(action_id))

    assert "error" in result
    stored = approval_db.pending_actions[action_id]
    assert stored["tool_name"] == "connector_rotate_token"
    assert stored["tool_args"] == (
        '{"connector_type": "gmail", "endpoint_identity": "owner@example.com"}'
    )
    assert stored["status"] == "approved"
    assert stored["execution_result"] is None


async def test_owner_command_registry_rejects_handler_signature_drift() -> None:
    """A declared producer cannot be left behind a renamed or reshaped handler."""
    mcp = FastMCP("switchboard-signature-drift")

    @mcp.tool()
    async def connector_disconnect(connector_type: str) -> dict[str, Any]:
        return {"success": True, "connector_type": connector_type}

    with pytest.raises(ApprovalCommandContractError, match="connector_disconnect"):
        await validate_owner_command_registry(mcp, "switchboard")


async def test_owner_command_registry_rejects_non_keyword_handler() -> None:
    """The executor uses ``handler(**stored_kwargs)``, never positional replay."""

    async def connector_disconnect(
        connector_type: str,
        /,
        endpoint_identity: str,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
        }

    mcp = SimpleNamespace(
        get_tool=lambda _name: SimpleNamespace(fn=connector_disconnect),
    )

    with pytest.raises(ApprovalCommandContractError, match="explicit keyword"):
        await validate_owner_command_registry(mcp, "switchboard")


def _switchboard_validation_daemon(mcp: Any) -> ButlerDaemon:
    """Build the real daemon gate-wiring path around a candidate MCP registry."""
    config = load_config(_REPO_ROOT / "roster" / "switchboard")
    daemon = ButlerDaemon(_REPO_ROOT / "roster" / "switchboard", db=MockDB())
    daemon.config = config
    daemon.mcp = mcp
    daemon._modules = [ApprovalsModule()]
    return daemon


async def test_daemon_startup_rejects_missing_declared_handler() -> None:
    """The real daemon gate-wiring path fails before serving a missing command."""
    daemon = _switchboard_validation_daemon(FastMCP("switchboard-missing-handler"))

    with pytest.raises(ApprovalCommandContractError, match="connector_disconnect"):
        await daemon._apply_approval_gates()


async def test_daemon_startup_rejects_variadic_declared_handler() -> None:
    """The real daemon gate-wiring path rejects handler kwargs it cannot prove."""

    async def connector_disconnect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "args": args, "kwargs": kwargs}

    mcp = SimpleNamespace(
        get_tool=lambda _name: SimpleNamespace(fn=connector_disconnect),
    )
    daemon = _switchboard_validation_daemon(mcp)

    with pytest.raises(ApprovalCommandContractError, match="explicit keyword"):
        await daemon._apply_approval_gates()


def test_switchboard_disconnect_marks_the_full_resource_identity_critical() -> None:
    """A standing rule cannot blanket-approve a different connector identity."""
    metadata = SwitchboardModule().tool_metadata()["connector_disconnect"]

    assert metadata.arg_sensitivities == {
        "connector_type": True,
        "endpoint_identity": True,
    }


def test_relationship_reclassification_marks_full_command_safety_critical() -> None:
    """Standing rules must pin the fact and the only supported mutation."""
    metadata = MemoryModule().tool_metadata()["memory_reclassify"]

    assert metadata.arg_sensitivities == {
        "memory_type": True,
        "memory_id": True,
        "permanence_target": True,
    }


def test_non_messenger_direct_park_sites_stay_declared() -> None:
    """New direct producers must join the inventory before they can park actions."""
    declared_sources = {command.producer_source for command in EXECUTABLE_DIRECT_COMMANDS}
    expected_inventory = {
        "connector_disconnect",
        "connector_rotate_token",
        "memory_reclassify",
    }
    assert {item.name for item in NON_MESSENGER_PRODUCER_INVENTORY} == expected_inventory

    generic_park_files = {
        "src/butlers/core_tools/_notifications.py",
        "src/butlers/daemon.py",
        "src/butlers/modules/approvals/email_guard.py",
        "src/butlers/modules/approvals/gate.py",
    }
    # These producers already call registered relationship tools whose original
    # implementations are retained by the standard approval gate.  They are
    # deliberately explicit here so a new direct park site cannot silently
    # bypass the durable-command inventory added for this regression.
    established_replayable_sources = {
        (
            "roster/relationship/jobs/relationship_jobs.py",
            "run_fact_retraction_curation._ensure_pending_action",
        ),
        (
            "roster/relationship/jobs/relationship_jobs.py",
            "run_entity_dedup_curation._ensure_dedup_pending_action",
        ),
        ("roster/relationship/jobs/relationship_jobs.py", "run_email_identity_enrichment"),
        ("roster/relationship/tools/relationship_assert_fact.py", "_create_pending_action"),
    }
    found_sources: set[tuple[str, str]] = set()
    for root in (_REPO_ROOT / "src", _REPO_ROOT / "roster"):
        for path in root.rglob("*.py"):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            if relative in generic_park_files:
                continue
            found_sources.update(_direct_park_sources(path, relative))

    assert found_sources == declared_sources | established_replayable_sources


def _direct_park_sources(path: Path, relative: str) -> set[tuple[str, str]]:
    """Return direct ``park_pending_action`` call sites as (file, function)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sources: set[tuple[str, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.functions: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name == "park_pending_action":
                owner = ".".join(self.functions) if self.functions else "<module>"
                sources.add((relative, owner))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sources


def test_declared_command_handlers_have_exact_keyword_signatures() -> None:
    """The source-level declaration itself cannot hide extra replay kwargs."""
    for command in EXECUTABLE_DIRECT_COMMANDS:
        assert command.argument_names
        assert len(command.argument_names) == len(set(command.argument_names))
