"""Tests for the core_039_dashboard_conversations Alembic migration.

Covers tasks 2.1–2.3 from openspec/changes/dashboard-conversational-input/tasks.md:

  2.1  shared.dashboard_conversations table with all required columns.
  2.2  shared.dashboard_messages table with all required columns.
  2.3  Composite indexes on both tables.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_039_dashboard_conversations.py"


def _load_migration():
    """Dynamically load the core_039 migration module."""
    spec = importlib.util.spec_from_file_location(
        "core_039_dashboard_conversations", MIGRATION_FILE
    )
    assert spec is not None, f"Cannot locate migration at {MIGRATION_FILE}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """core_039_dashboard_conversations.py exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration not found at {MIGRATION_FILE}"


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is 'core_039'."""
        mod = _load_migration()
        assert mod.revision == "core_039"

    def test_down_revision(self) -> None:
        """Migration revises core_038."""
        mod = _load_migration()
        assert mod.down_revision == "core_038"

    def test_branch_labels_none(self) -> None:
        """Migration has no branch label (belongs to linear core chain)."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is defined and callable."""
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        """downgrade() is defined and callable."""
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# Task 2.1 — shared.dashboard_conversations table
# ---------------------------------------------------------------------------


class TestDashboardConversationsTable:
    def test_creates_shared_schema(self) -> None:
        """upgrade() ensures the shared schema exists."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE SCHEMA IF NOT EXISTS shared" in source

    def test_creates_dashboard_conversations_table(self) -> None:
        """upgrade() creates shared.dashboard_conversations."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS shared.dashboard_conversations" in source

    def test_conversations_has_id_column(self) -> None:
        """dashboard_conversations has UUID primary key 'id'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "id UUID PRIMARY KEY" in source

    @pytest.mark.parametrize(
        "column",
        [
            "butler_name",
            "title",
            "status",
            "created_at",
            "updated_at",
            "message_count",
            "total_input_tokens",
            "total_output_tokens",
            "total_duration_ms",
        ],
    )
    def test_conversations_has_required_column(self, column: str) -> None:
        """dashboard_conversations has all spec columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert column in source, f"Column '{column}' missing from migration"

    def test_conversations_status_has_default(self) -> None:
        """dashboard_conversations.status has a default value of 'active'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DEFAULT 'active'" in source


# ---------------------------------------------------------------------------
# Task 2.2 — shared.dashboard_messages table
# ---------------------------------------------------------------------------


class TestDashboardMessagesTable:
    def test_creates_dashboard_messages_table(self) -> None:
        """upgrade() creates shared.dashboard_messages."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS shared.dashboard_messages" in source

    def test_messages_fk_to_conversations(self) -> None:
        """dashboard_messages.conversation_id has FK to dashboard_conversations."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "REFERENCES shared.dashboard_conversations(id)" in source
        assert "ON DELETE CASCADE" in source

    @pytest.mark.parametrize(
        "column",
        [
            "conversation_id",
            "role",
            "content",
            "created_at",
            "session_id",
            "model_name",
            "input_tokens",
            "output_tokens",
            "duration_ms",
            "tool_calls",
            "error",
            "request_id",
        ],
    )
    def test_messages_has_required_column(self, column: str) -> None:
        """dashboard_messages has all spec columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert column in source, f"Column '{column}' missing from migration"

    def test_tool_calls_is_jsonb(self) -> None:
        """dashboard_messages.tool_calls uses JSONB type."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "tool_calls JSONB" in source


# ---------------------------------------------------------------------------
# Task 2.3 — Composite indexes
# ---------------------------------------------------------------------------


class TestCompositeIndexes:
    def test_conversations_butler_status_updated_index(self) -> None:
        """upgrade() creates composite index (butler_name, status, updated_at DESC)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_dashboard_conversations_butler_status_updated" in source
        assert "butler_name, status, updated_at DESC" in source

    def test_conversations_butler_updated_index(self) -> None:
        """upgrade() creates composite index (butler_name, updated_at DESC)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_dashboard_conversations_butler_updated" in source
        assert "butler_name, updated_at DESC" in source

    def test_messages_conversation_created_index(self) -> None:
        """upgrade() creates composite index (conversation_id, created_at ASC)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_dashboard_messages_conversation_created" in source
        assert "conversation_id, created_at ASC" in source


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_drops_dashboard_messages(self) -> None:
        """downgrade() drops shared.dashboard_messages."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS shared.dashboard_messages" in source

    def test_drops_dashboard_conversations(self) -> None:
        """downgrade() drops shared.dashboard_conversations."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS shared.dashboard_conversations" in source

    def test_drops_all_three_indexes(self) -> None:
        """downgrade() drops all three composite indexes."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "idx_dashboard_messages_conversation_created" in source
        assert "idx_dashboard_conversations_butler_updated" in source
        assert "idx_dashboard_conversations_butler_status_updated" in source
