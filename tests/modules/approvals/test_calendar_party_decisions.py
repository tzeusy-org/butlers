"""Calendar owner submissions are decisions, not delegated-success evidence."""

import tomllib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.config import parse_approval_config
from butlers.modules.approvals import operations
from butlers.modules.approvals.models import ActionStatus, PendingAction

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("eligible", [False, True])
async def test_owner_submission_excludes_only_promotion_history(monkeypatch, eligible):
    action = PendingAction(
        id=uuid4(),
        tool_name="calendar_create_event",
        tool_args={"attendees": ["guest@example.test"]},
        status=ActionStatus.PENDING,
        requested_at=datetime.now(UTC),
    )
    state = action.to_dict()
    pool = SimpleNamespace(
        fetchrow=AsyncMock(side_effect=lambda *a: dict(state)), execute=AsyncMock()
    )

    async def transition(*args, **kwargs):
        assert kwargs["target_status"] is ActionStatus.APPROVED
        state["status"] = "approved"
        state["decided_at"] = datetime.now(UTC).isoformat()
        approved = PendingAction.from_row(state)
        return SimpleNamespace(action=approved, changed=True, expired_instead=False)

    @asynccontextmanager
    async def savepoint(actual):
        assert actual is pool
        yield actual

    monkeypatch.setattr(operations, "transition_pending_action", transition)
    monkeypatch.setattr(operations, "descriptive_savepoint", savepoint)
    threshold = AsyncMock()
    monkeypatch.setattr(operations, "_check_promotion_threshold", threshold)
    result = await operations.approve_action(pool, str(action.id), promotion_eligible=eligible)
    assert result["status"] == "approved"
    assert pool.execute.await_count == int(eligible)
    if eligible:
        assert "INSERT INTO autonomy_approval_history" in pool.execute.await_args.args[0]
    assert threshold.await_count == int(eligible)


def test_every_existing_calendar_owner_has_approval_capability_without_new_rules():
    root = Path(__file__).resolve().parents[3]
    found = set()
    for path in (root / "roster").glob("*/butler.toml"):
        config = tomllib.loads(path.read_text())
        modules = config.get("modules", {})
        if "calendar" not in modules:
            continue
        found.add(path.parent.name)
        approval = parse_approval_config(modules.get("approvals"))
        assert approval is not None, path.parent.name
        assert approval.enabled or "calendar" in approval.domain_producers, path.parent.name
        if path.parent.name in {"general", "finance", "lifestyle", "travel", "health"}:
            assert not approval.gated_tools
    assert found == {
        "general",
        "finance",
        "lifestyle",
        "travel",
        "health",
        "messenger",
        "relationship",
    }


@pytest.mark.parametrize("value", ["calendar", ["spotify"], ["calendar", "calendar"], [True], None])
def test_domain_producer_configuration_refuses_untyped_or_unknown_authority(value):
    from butlers.config import ConfigError

    with pytest.raises(ConfigError):
        parse_approval_config({"enabled": False, "domain_producers": value})


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("state", ["missing", "failed", "no_executor", "no_pool", "ready"])
def test_domain_producer_requires_active_owning_approvals(enabled, state):
    from butlers.daemon import ButlerDaemon
    from butlers.modules.calendar import CalendarModule

    module = CalendarModule()
    callback = []
    module.set_approval_enqueuer = callback.append
    daemon = object.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(
        name="general",
        modules={"approvals": {"enabled": enabled, "domain_producers": ["calendar"]}},
    )
    daemon.db = SimpleNamespace(pool=object())
    daemon._approval_push_runtime = None
    daemon._module_statuses = {}
    approvals = SimpleNamespace(
        name="approvals", _tool_executor=None if state == "no_executor" else lambda *a: None
    )
    daemon._modules = [module] + ([] if state == "missing" else [approvals])
    if state == "failed":
        daemon._module_statuses["approvals"] = SimpleNamespace(status="failed")
    if state == "no_pool":
        daemon.db.pool = None
    daemon._wire_calendar_approval_enqueuer()
    assert len(callback) == int(state == "ready")


def test_producer_defaults_are_empty_and_do_not_relax_enabled_reverse_coverage():
    from butlers.config import ConfigError, validate_approval_config
    from butlers.modules.base import ToolMeta

    default = parse_approval_config({"enabled": False})
    assert default.__dict__.get("domain_producers") == ()
    policy = parse_approval_config({"enabled": True, "domain_producers": ["calendar"]})
    with pytest.raises(ConfigError, match="not covered"):
        validate_approval_config(
            policy, {"spotify_play"}, {"spotify_play": ToolMeta(arg_sensitivities={"_write": True})}
        )
