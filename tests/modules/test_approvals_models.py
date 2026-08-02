"""Tests for the approvals module data models and Alembic migration.

Unit tests validate dataclass serialisation/deserialisation.
Integration tests verify Alembic migration creates schema and models round-trip.
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


@pytest.mark.unit
class TestPendingActionModel:
    def test_create_minimal_and_full(self):
        """Minimal and full PendingAction construction and field defaults."""
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

        now = datetime.now(UTC)
        rule_id = uuid.uuid4()
        full = PendingAction(
            id=uuid.uuid4(),
            tool_name="telegram_send",
            tool_args={"chat_id": 123, "text": "hi"},
            agent_summary="Send telegram",
            session_id=uuid.uuid4(),
            status=ActionStatus.APPROVED,
            requested_at=now,
            expires_at=now,
            decided_by="user:alice",
            decided_at=now,
            execution_result={"ok": True},
            approval_rule_id=rule_id,
        )
        assert full.status == ActionStatus.APPROVED
        assert full.approval_rule_id == rule_id

    def test_to_dict_and_round_trip(self):
        """to_dict() is JSON-serialisable; from_dict() round-trips."""
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
        assert isinstance(json.dumps(d), str)
        assert isinstance(d["id"], str)
        assert isinstance(d["requested_at"], str)

        restored = PendingAction.from_dict(d)
        assert restored.id == original.id
        assert restored.tool_name == original.tool_name
        assert restored.status == original.status

    def test_status_enum_values(self):
        assert set(ActionStatus) == {
            ActionStatus.PENDING,
            ActionStatus.APPROVED,
            ActionStatus.REJECTED,
            ActionStatus.EXPIRED,
            ActionStatus.EXECUTED,
            ActionStatus.ABANDONED,
        }
        assert ActionStatus.PENDING.value == "pending"


@pytest.mark.unit
class TestApprovalRuleModel:
    def test_create_minimal_and_full(self):
        rule = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="email_send",
            arg_constraints={"to": "alice@example.com"},
            description="Auto-approve emails to Alice",
            created_at=datetime.now(UTC),
        )
        assert rule.active is True
        assert rule.use_count == 0
        assert rule.max_uses is None

        now = datetime.now(UTC)
        full = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="telegram_send",
            arg_constraints={"chat_id": 123},
            description="Auto-approve telegram to chat 123",
            created_from=uuid.uuid4(),
            created_at=now,
            expires_at=now,
            max_uses=10,
            use_count=3,
            active=False,
        )
        assert full.max_uses == 10 and full.active is False

    def test_to_dict_and_round_trip(self):
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
        assert isinstance(json.dumps(d), str)
        restored = ApprovalRule.from_dict(d)
        assert restored.id == original.id
        assert restored.arg_constraints == original.arg_constraints


# ---------------------------------------------------------------------------
# Integration tests — require Docker (testcontainers)
# ---------------------------------------------------------------------------
docker_available = shutil.which("docker") is not None


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


def _create_db(postgres_container, db_name: str) -> str:
    from sqlalchemy import create_engine, text

    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    return f"postgresql://{postgres_container.username}:{postgres_container.password}@{host}:{port}/{db_name}"


def _table_exists(db_url: str, table_name: str) -> bool:
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name=:t)"
            ),
            {"t": table_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _index_exists(db_url: str, index_name: str) -> bool:
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname=:idx)"),
            {"idx": index_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _column_exists(db_url: str, table_name: str, column_name: str) -> bool:
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name=:t AND column_name=:c)"
            ),
            {"t": table_name, "c": column_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestApprovalsMigration:
    def test_migration_creates_tables_indexes_and_is_idempotent(self, postgres_container):
        """Migration creates tables + indexes and is idempotent."""
        import asyncio

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        asyncio.run(run_migrations(db_url, chain="approvals"))

        for tbl in [
            "pending_actions",
            "approval_push_emissions",
            "approval_rules",
            "approval_events",
            "autonomy_approval_history",
            "autonomy_suggestions",
        ]:
            assert _table_exists(db_url, tbl), f"{tbl} should exist"
        for col in ["why", "evidence"]:
            assert _column_exists(db_url, "pending_actions", col), (
                f"pending_actions.{col} should exist"
            )
        for idx in [
            "idx_pending_actions_status_requested",
            "idx_pending_actions_session_id",
            "idx_approval_rules_tool_active",
            "idx_approval_events_action_id",
            "idx_approval_events_rule_id",
            "idx_approval_events_occurred_at",
            "idx_approval_events_event_type",
            "idx_autonomy_history_fingerprint_version",
            "idx_autonomy_suggestions_fingerprint_version",
            "ix_approval_push_emissions_kind_created",
        ]:
            assert _index_exists(db_url, idx), f"{idx} should exist"
        for table_name in ["autonomy_approval_history", "autonomy_suggestions"]:
            assert _column_exists(db_url, table_name, "fingerprint_version")

        # Idempotent
        asyncio.run(run_migrations(db_url, chain="approvals"))
        assert _table_exists(db_url, "pending_actions")

    def test_alembic_version_and_append_only_events(self, postgres_container):
        """Version tracked; approval_events rejects UPDATE/DELETE."""
        import asyncio

        from sqlalchemy import create_engine, exc, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)
        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        with engine.connect() as conn:
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        assert "approvals_013" in versions

        action_id = uuid.uuid4()
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)"
                    " VALUES (:id, :tn, :ta, :s, :r)"
                ),
                {
                    "id": str(action_id),
                    "tn": "email_send",
                    "ta": "{}",
                    "s": "pending",
                    "r": datetime.now(UTC),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO approval_events (action_id, event_type, actor, reason)"
                    " VALUES (:a, :e, :ac, :re)"
                ),
                {"a": str(action_id), "e": "action_queued", "ac": "system:test", "re": "queued"},
            )
            abandoned_action_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)"
                    " VALUES (:id, :tn, :ta, :s, :r)"
                ),
                {
                    "id": str(abandoned_action_id),
                    "tn": "email_send",
                    "ta": "{}",
                    "s": "abandoned",
                    "r": datetime.now(UTC),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO approval_events (action_id, event_type, actor, reason)"
                    " VALUES (:a, :e, :ac, :re)"
                ),
                {
                    "a": str(abandoned_action_id),
                    "e": "action_abandoned",
                    "ac": "user:dashboard:rest-api",
                    "re": "No longer needed",
                },
            )
            conn.commit()

        with pytest.raises(exc.DBAPIError):
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE approval_events SET reason=:r WHERE action_id=:a"),
                    {"r": "mutated", "a": str(action_id)},
                )
                conn.commit()
        with pytest.raises(exc.DBAPIError):
            with engine.connect() as conn:
                conn.execute(
                    text("DELETE FROM approval_events WHERE action_id=:a"), {"a": str(action_id)}
                )
                conn.commit()
        engine.dispose()

    def test_deduplication_migration_converges_former_012_schema(self, postgres_container):
        """A pre-merge semantic-key 012 reaches the current abandonment contract."""
        from sqlalchemy import create_engine, exc, text

        from alembic import command
        from butlers.migrations import _build_alembic_config

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)
        config = _build_alembic_config(db_url, chains=["approvals"])
        command.upgrade(config, "approvals@approvals_011")

        action_id = uuid.uuid4()
        deduplication_key = "relationship:entity-dedup:legacy-source:legacy-target"
        engine = create_engine(db_url)
        with engine.begin() as conn:
            # Simulate the former branch-only approvals_012 migration, whose
            # revision id collided with the later abandonment migration.
            conn.execute(text("ALTER TABLE pending_actions ADD COLUMN deduplication_key TEXT"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX ux_pending_actions_active_deduplication_key "
                    "ON pending_actions (deduplication_key) "
                    "WHERE deduplication_key IS NOT NULL "
                    "AND status IN ('pending', 'approved', 'rejected')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO pending_actions "
                    "(id, tool_name, tool_args, status, requested_at, deduplication_key) "
                    "VALUES (:id, :tool_name, CAST(:tool_args AS jsonb), :status, :requested_at, "
                    ":deduplication_key)"
                ),
                {
                    "id": str(action_id),
                    "tool_name": "memory_entity_merge",
                    "tool_args": "{}",
                    "status": "pending",
                    "requested_at": datetime.now(UTC),
                    "deduplication_key": deduplication_key,
                },
            )
            conn.execute(text("UPDATE alembic_version SET version_num = 'approvals_012'"))

        command.upgrade(config, "approvals@head")

        with engine.begin() as conn:
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
            index_sql = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'ux_pending_actions_active_deduplication_key'"
                )
            ).scalar_one()
            conn.execute(
                text("UPDATE pending_actions SET status = 'abandoned' WHERE id = :id"),
                {"id": str(action_id)},
            )
            conn.execute(
                text(
                    "INSERT INTO approval_events (action_id, event_type, actor, reason) "
                    "VALUES (:action_id, 'action_abandoned', 'user:dashboard:rest-api', 'no retry')"
                ),
                {"action_id": str(action_id)},
            )

        assert versions == ["approvals_013"]
        assert "abandoned" in index_sql

        with pytest.raises(exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pending_actions "
                        "(id, tool_name, tool_args, status, requested_at, deduplication_key) "
                        "VALUES (:id, :tool_name, CAST(:tool_args AS jsonb), :status, "
                        ":requested_at, :deduplication_key)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "tool_name": "memory_entity_merge",
                        "tool_args": "{}",
                        "status": "pending",
                        "requested_at": datetime.now(UTC),
                        "deduplication_key": deduplication_key,
                    },
                )
        engine.dispose()

    def test_migration_updates_existing_approvals_001_pending_actions(self, postgres_container):
        """Existing approvals_001 tables receive dossier columns on upgrade."""
        import asyncio

        from sqlalchemy import create_engine, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)

        engine = create_engine(db_url)
        action_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE pending_actions ("
                    "id UUID PRIMARY KEY,"
                    "tool_name TEXT NOT NULL,"
                    "tool_args JSONB NOT NULL,"
                    "status VARCHAR NOT NULL DEFAULT 'pending',"
                    "requested_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                    ")"
                )
            )
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('approvals_001')"))
            conn.execute(
                text(
                    "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)"
                    " VALUES (:id, :tn, :ta, :s, :r)"
                ),
                {
                    "id": str(action_id),
                    "tn": "email_send",
                    "ta": "{}",
                    "s": "pending",
                    "r": datetime.now(UTC),
                },
            )

        assert not _column_exists(db_url, "pending_actions", "why")
        assert not _column_exists(db_url, "pending_actions", "evidence")

        asyncio.run(run_migrations(db_url, chain="approvals"))

        assert _column_exists(db_url, "pending_actions", "why")
        assert _column_exists(db_url, "pending_actions", "evidence")
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT why, evidence::text AS evidence FROM pending_actions WHERE id=:id"
                    ),
                    {"id": str(action_id)},
                )
                .mappings()
                .one()
            )
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        engine.dispose()

        assert row["why"] is None
        assert row["evidence"] == "[]"
        assert "approvals_013" in versions

    def test_decision_dossier_migration_converts_legacy_evidence_and_enforces_enums(
        self, postgres_container
    ):
        """Real Postgres proves RFC 0021 migrates data before enforcing its schema."""
        import asyncio

        from sqlalchemy import create_engine, exc, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)
        engine = create_engine(db_url)
        action_id = uuid.uuid4()
        legacy_evidence = [
            "Owner asked for a status update",
            {"type": "url", "ref": "https://example.test/request/42", "note": "request"},
        ]

        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE pending_actions ("
                    "id UUID PRIMARY KEY,"
                    "tool_name TEXT NOT NULL,"
                    "tool_args JSONB NOT NULL,"
                    "status VARCHAR NOT NULL DEFAULT 'pending',"
                    "requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                    "why TEXT,"
                    "evidence JSONB NOT NULL DEFAULT '[]'::jsonb"
                    ")"
                )
            )
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('approvals_002')"))
            conn.execute(
                text(
                    "INSERT INTO pending_actions "
                    "(id, tool_name, tool_args, status, requested_at, evidence) "
                    "VALUES (:id, :tool_name, CAST(:tool_args AS jsonb), :status, :requested_at, "
                    "CAST(:evidence AS jsonb))"
                ),
                {
                    "id": str(action_id),
                    "tool_name": "notify",
                    "tool_args": "{}",
                    "status": "pending",
                    "requested_at": datetime.now(UTC),
                    "evidence": json.dumps(legacy_evidence),
                },
            )

        asyncio.run(run_migrations(db_url, chain="approvals"))

        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT evidence::text AS evidence, blast_radius, reversibility "
                        "FROM pending_actions WHERE id = :id"
                    ),
                    {"id": str(action_id)},
                )
                .mappings()
                .one()
            )
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]

        assert json.loads(row["evidence"]) == [
            {
                "type": "text",
                "ref": "Owner asked for a status update",
                "note": "",
            },
            {"type": "url", "ref": "https://example.test/request/42", "note": "request"},
        ]
        assert row["blast_radius"] is None
        assert row["reversibility"] is None
        assert "approvals_013" in versions

        with pytest.raises(exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pending_actions "
                        "(id, tool_name, tool_args, status, requested_at, blast_radius) "
                        "VALUES (:id, :tool_name, CAST(:tool_args AS jsonb), :status, :requested_at, "
                        ":blast_radius)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "tool_name": "notify",
                        "tool_args": "{}",
                        "status": "pending",
                        "requested_at": datetime.now(UTC),
                        "blast_radius": "planet",
                    },
                )
        engine.dispose()

    def test_model_round_trip_pending_action(self, postgres_container):
        import asyncio

        from sqlalchemy import create_engine, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)
        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        action_id = uuid.uuid4()
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO pending_actions"
                    " (id, tool_name, tool_args, agent_summary, status, requested_at)"
                    " VALUES (:id, :tn, :ta, :s, :st, :r)"
                ),
                {
                    "id": str(action_id),
                    "tn": "email_send",
                    "ta": json.dumps({"to": "alice@example.com"}),
                    "s": "Send email",
                    "st": "pending",
                    "r": datetime.now(UTC),
                },
            )
            conn.commit()
            row = (
                conn.execute(
                    text("SELECT * FROM pending_actions WHERE id=:id"), {"id": str(action_id)}
                )
                .mappings()
                .one()
            )
        engine.dispose()
        restored = PendingAction.from_row(row)
        assert restored.id == action_id and restored.tool_name == "email_send"

    def test_model_round_trip_approval_rule(self, postgres_container):
        import asyncio

        from sqlalchemy import create_engine, text

        from butlers.migrations import run_migrations

        db_name = _unique_db_name()
        db_url = _create_db(postgres_container, db_name)
        asyncio.run(run_migrations(db_url, chain="approvals"))

        engine = create_engine(db_url)
        rule_id = uuid.uuid4()
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO approval_rules (id, tool_name, arg_constraints, description,"
                    " created_at, max_uses, active)"
                    " VALUES (:id, :tn, :ac, :d, :c, :m, :a)"
                ),
                {
                    "id": str(rule_id),
                    "tn": "telegram_send",
                    "ac": json.dumps({"chat_id": 123}),
                    "d": "Auto-approve telegram",
                    "c": datetime.now(UTC),
                    "m": 10,
                    "a": True,
                },
            )
            conn.commit()
            row = (
                conn.execute(
                    text("SELECT * FROM approval_rules WHERE id=:id"), {"id": str(rule_id)}
                )
                .mappings()
                .one()
            )
        engine.dispose()
        restored = ApprovalRule.from_row(row)
        assert restored.id == rule_id and restored.max_uses == 10

    def test_status_check_constraint(self, postgres_container):
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
                    text(
                        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)"
                        " VALUES (:id, :tn, :ta, :s, :r)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "tn": "test",
                        "ta": "{}",
                        "s": "invalid_status",
                        "r": datetime.now(UTC),
                    },
                )
                conn.commit()
        engine.dispose()
