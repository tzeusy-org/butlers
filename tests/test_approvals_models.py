"""Tests for the approvals module data models and Alembic migration.

Unit tests validate that the Python dataclasses serialise/deserialise correctly.
Integration tests verify the Alembic migration creates the schema and that
models round-trip through the database.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.modules.approvals.models import (
    ActionStatus,
    ApprovalRule,
    PendingAction,
)

# ---------------------------------------------------------------------------
# Unit tests — no Docker, no DB
# ---------------------------------------------------------------------------
pytestmark_unit = pytest.mark.unit


@pytest.mark.unit
class TestPendingActionModel:
    """Test PendingAction dataclass."""

    def test_create_minimal(self):
        """Create a PendingAction with only required fields."""
        action = PendingAction(
            id=uuid.uuid4(),
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "hello"},
            status=ActionStatus.PENDING,
            requested_at=datetime.now(UTC),
        )
        assert action.tool_name == "email_send"
        assert action.status == ActionStatus.PENDING
        assert action.agent_summary is None
        assert action.session_id is None
        assert action.expires_at is None
        assert action.decided_by is None
        assert action.decided_at is None
        assert action.execution_result is None
        assert action.approval_rule_id is None

    def test_create_full(self):
        """Create a PendingAction with all fields populated."""
        now = datetime.now(UTC)
        rule_id = uuid.uuid4()
        session_id = uuid.uuid4()
        action = PendingAction(
            id=uuid.uuid4(),
            tool_name="telegram_send",
            tool_args={"chat_id": 123, "text": "hi"},
            agent_summary="Agent wants to send a telegram message",
            session_id=session_id,
            status=ActionStatus.APPROVED,
            requested_at=now,
            expires_at=now,
            decided_by="user:alice",
            decided_at=now,
            execution_result={"ok": True},
            approval_rule_id=rule_id,
        )
        assert action.status == ActionStatus.APPROVED
        assert action.decided_by == "user:alice"
        assert action.approval_rule_id == rule_id

    def test_to_dict_json_serialisable(self):
        """to_dict() produces a JSON-serialisable dict."""
        action = PendingAction(
            id=uuid.uuid4(),
            tool_name="email_send",
            tool_args={"to": "bob@example.com"},
            status=ActionStatus.PENDING,
            requested_at=datetime.now(UTC),
        )
        d = action.to_dict()
        # Should not raise
        serialised = json.dumps(d)
        assert isinstance(serialised, str)
        # UUID should be string
        assert isinstance(d["id"], str)
        # datetime should be ISO string
        assert isinstance(d["requested_at"], str)

    def test_from_dict_round_trip(self):
        """from_dict() can reconstruct a PendingAction from to_dict() output."""
        original = PendingAction(
            id=uuid.uuid4(),
            tool_name="calendar_create",
            tool_args={"title": "meeting"},
            agent_summary="Create a meeting",
            session_id=uuid.uuid4(),
            status=ActionStatus.EXECUTED,
            requested_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
            decided_by="rule:auto",
            decided_at=datetime.now(UTC),
            execution_result={"event_id": "abc"},
            approval_rule_id=uuid.uuid4(),
        )
        d = original.to_dict()
        restored = PendingAction.from_dict(d)
        assert restored.id == original.id
        assert restored.tool_name == original.tool_name
        assert restored.tool_args == original.tool_args
        assert restored.status == original.status
        assert restored.session_id == original.session_id
        assert restored.approval_rule_id == original.approval_rule_id

    def test_status_enum_values(self):
        """ActionStatus enum has exactly the expected values."""
        assert set(ActionStatus) == {
            ActionStatus.PENDING,
            ActionStatus.APPROVED,
            ActionStatus.REJECTED,
            ActionStatus.EXPIRED,
            ActionStatus.EXECUTED,
        }
        assert ActionStatus.PENDING.value == "pending"
        assert ActionStatus.APPROVED.value == "approved"
        assert ActionStatus.REJECTED.value == "rejected"
        assert ActionStatus.EXPIRED.value == "expired"
        assert ActionStatus.EXECUTED.value == "executed"


@pytest.mark.unit
class TestApprovalRuleModel:
    """Test ApprovalRule dataclass."""

    def test_create_minimal(self):
        """Create an ApprovalRule with only required fields."""
        rule = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="email_send",
            arg_constraints={"to": "alice@example.com"},
            description="Auto-approve emails to Alice",
            created_at=datetime.now(UTC),
        )
        assert rule.tool_name == "email_send"
        assert rule.active is True
        assert rule.use_count == 0
        assert rule.max_uses is None
        assert rule.created_from is None
        assert rule.expires_at is None

    def test_create_full(self):
        """Create an ApprovalRule with all fields populated."""
        now = datetime.now(UTC)
        action_id = uuid.uuid4()
        rule = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="telegram_send",
            arg_constraints={"chat_id": 123},
            description="Auto-approve telegram to chat 123",
            created_from=action_id,
            created_at=now,
            expires_at=now,
            max_uses=10,
            use_count=3,
            active=False,
        )
        assert rule.created_from == action_id
        assert rule.max_uses == 10
        assert rule.use_count == 3
        assert rule.active is False

    def test_to_dict_json_serialisable(self):
        """to_dict() produces a JSON-serialisable dict."""
        rule = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="email_send",
            arg_constraints={},
            description="test",
            created_at=datetime.now(UTC),
        )
        d = rule.to_dict()
        serialised = json.dumps(d)
        assert isinstance(serialised, str)
        assert isinstance(d["id"], str)

    def test_from_dict_round_trip(self):
        """from_dict() can reconstruct an ApprovalRule from to_dict() output."""
        original = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="calendar_create",
            arg_constraints={"title": "standup"},
            description="Auto-approve standup creation",
            created_from=uuid.uuid4(),
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
            max_uses=5,
            use_count=2,
            active=True,
        )
        d = original.to_dict()
        restored = ApprovalRule.from_dict(d)
        assert restored.id == original.id
        assert restored.tool_name == original.tool_name
        assert restored.arg_constraints == original.arg_constraints
        assert restored.max_uses == original.max_uses
        assert restored.use_count == original.use_count
        assert restored.active == original.active
        assert restored.created_from == original.created_from


# ---------------------------------------------------------------------------
# Integration tests — require Docker (testcontainers)
# ---------------------------------------------------------------------------
docker_available = shutil.which("docker") is not None


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for approvals migration tests."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        yield postgres


def _create_db(postgres_container, db_name: str) -> str:
    """Create a fresh database and return its SQLAlchemy URL."""
    from sqlalchemy import create_engine, text

    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()

    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _table_exists(db_url: str, table_name: str) -> bool:
    """Check whether a table exists in the database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = 'public' AND table_name = :t"
                ")"
            ),
            {"t": table_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _index_exists(db_url: str, index_name: str) -> bool:
    """Check whether an index exists in the database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (  SELECT 1 FROM pg_indexes  WHERE indexname = :idx)"),
            {"idx": index_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestApprovalsMigration:
    """Test Alembic migration for approvals tables."""

    def test_migration_creates_tables(self, postgres_container):
        """Running approvals migration creates both tables."""
        import asyncio

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        assert _table_exists(db_url, "pending_actions"), "pending_actions table should exist"
        assert _table_exists(db_url, "approval_rules"), "approval_rules table should exist"
        assert _table_exists(db_url, "approval_events"), "approval_events table should exist"

    def test_migration_creates_indexes(self, postgres_container):
        """Running approvals migration creates required indexes."""
        import asyncio

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        assert _index_exists(db_url, "idx_pending_actions_status_requested")
        assert _index_exists(db_url, "idx_pending_actions_session_id")
        assert _index_exists(db_url, "idx_approval_rules_tool_active")
        assert _index_exists(db_url, "idx_approval_events_action_id")
        assert _index_exists(db_url, "idx_approval_events_rule_id")
        assert _index_exists(db_url, "idx_approval_events_occurred_at")
        assert _index_exists(db_url, "idx_approval_events_event_type")

    def test_migration_idempotent(self, postgres_container):
        """Running the approvals migration twice should not raise."""
        import asyncio

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))
        asyncio.run(run_migrations(db_url, chain="approvals"))

        assert _table_exists(db_url, "pending_actions")
        assert _table_exists(db_url, "approval_rules")

    def test_alembic_version_tracking(self, postgres_container):
        """After migration, alembic_version should have approvals revision."""
        import asyncio

        from sqlalchemy import create_engine, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            versions = [row[0] for row in result]
        engine.dispose()

        assert "approvals_002" in versions

    def test_approval_events_append_only(self, postgres_container):
        """approval_events should reject UPDATE/DELETE mutations."""
        import asyncio

        from sqlalchemy import create_engine, exc, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        action_id = uuid.uuid4()

        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)
                    VALUES (:id, :tool_name, :tool_args, :status, :requested_at)
                """),
                {
                    "id": str(action_id),
                    "tool_name": "email_send",
                    "tool_args": "{}",
                    "status": "pending",
                    "requested_at": datetime.now(UTC),
                },
            )
            conn.execute(
                text("""
                    INSERT INTO approval_events (action_id, event_type, actor, reason)
                    VALUES (:action_id, :event_type, :actor, :reason)
                """),
                {
                    "action_id": str(action_id),
                    "event_type": "action_queued",
                    "actor": "system:test",
                    "reason": "queued for approval",
                },
            )
            conn.commit()

        with pytest.raises(exc.DBAPIError):
            with engine.connect() as conn:
                conn.execute(
                    text("""
                        UPDATE approval_events
                        SET reason = :reason
                        WHERE action_id = :action_id
                    """),
                    {"reason": "mutated", "action_id": str(action_id)},
                )
                conn.commit()

        with pytest.raises(exc.DBAPIError):
            with engine.connect() as conn:
                conn.execute(
                    text("DELETE FROM approval_events WHERE action_id = :action_id"),
                    {"action_id": str(action_id)},
                )
                conn.commit()

        engine.dispose()

    def test_model_round_trip_pending_action(self, postgres_container):
        """PendingAction model round-trips through the database."""
        import asyncio

        from sqlalchemy import create_engine, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        action_id = uuid.uuid4()
        now = datetime.now(UTC)

        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO pending_actions (id, tool_name, tool_args, agent_summary, status,
                                                 requested_at)
                    VALUES (:id, :tool_name, :tool_args, :summary, :status, :requested_at)
                """),
                {
                    "id": str(action_id),
                    "tool_name": "email_send",
                    "tool_args": json.dumps({"to": "alice@example.com"}),
                    "summary": "Send email to Alice",
                    "status": "pending",
                    "requested_at": now,
                },
            )
            conn.commit()

            result = conn.execute(
                text("SELECT * FROM pending_actions WHERE id = :id"),
                {"id": str(action_id)},
            )
            row = result.mappings().one()

        engine.dispose()

        restored = PendingAction.from_row(row)
        assert restored.id == action_id
        assert restored.tool_name == "email_send"
        assert restored.tool_args == {"to": "alice@example.com"}
        assert restored.agent_summary == "Send email to Alice"
        assert restored.status == ActionStatus.PENDING

    def test_model_round_trip_approval_rule(self, postgres_container):
        """ApprovalRule model round-trips through the database."""
        import asyncio

        from sqlalchemy import create_engine, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        rule_id = uuid.uuid4()
        now = datetime.now(UTC)

        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO approval_rules (id, tool_name, arg_constraints, description,
                                                created_at, max_uses, active)
                    VALUES (:id, :tool_name, :arg_constraints, :description,
                            :created_at, :max_uses, :active)
                """),
                {
                    "id": str(rule_id),
                    "tool_name": "telegram_send",
                    "arg_constraints": json.dumps({"chat_id": 123}),
                    "description": "Auto-approve telegram to chat 123",
                    "created_at": now,
                    "max_uses": 10,
                    "active": True,
                },
            )
            conn.commit()

            result = conn.execute(
                text("SELECT * FROM approval_rules WHERE id = :id"),
                {"id": str(rule_id)},
            )
            row = result.mappings().one()

        engine.dispose()

        restored = ApprovalRule.from_row(row)
        assert restored.id == rule_id
        assert restored.tool_name == "telegram_send"
        assert restored.arg_constraints == {"chat_id": 123}
        assert restored.description == "Auto-approve telegram to chat 123"
        assert restored.max_uses == 10
        assert restored.active is True

    def test_status_check_constraint(self, postgres_container):
        """Inserting an invalid status should raise an error."""
        import asyncio

        from sqlalchemy import create_engine, exc, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        with pytest.raises(exc.IntegrityError):
            with engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO pending_actions (id, tool_name, tool_args, status,
                                                     requested_at)
                        VALUES (:id, :tool_name, :tool_args, :status, :requested_at)
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "tool_name": "test",
                        "tool_args": "{}",
                        "status": "invalid_status",
                        "requested_at": datetime.now(UTC),
                    },
                )
                conn.commit()
        engine.dispose()
