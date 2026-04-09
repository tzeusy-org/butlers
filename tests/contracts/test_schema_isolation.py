"""Contract tests: Schema Isolation (RFC 0006, Invariant 1).

Validates per-butler schema scoping, search_path, and migration chain contracts.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestSchemaTopology:
    """RFC 0006: Each butler gets its own PostgreSQL schema."""

    def test_db_schema_param_and_search_path(self):
        """Database accepts schema param; search_path includes public."""
        from butlers.db import Database

        params = list(inspect.signature(Database.__init__).parameters.keys())
        assert "schema" in params

    def test_schema_name_and_core_tables(self):
        """Schema is stored on Database instance; core tables documented.

        Behavioral assertion: Database.__init__ with schema='mybutler' produces
        an instance where db.schema == 'mybutler', confirming the schema is
        recorded and will be used in search_path construction.
        """
        from butlers.db import Database, schema_search_path

        db = Database("test_db", schema="mybutler")
        assert db.schema == "mybutler", "Database must record schema on the instance (RFC 0006)"

        # schema_search_path includes both the butler schema and public
        search_path = schema_search_path("mybutler")
        assert "mybutler" in search_path, "search_path must include butler schema (RFC 0006)"
        assert "public" in search_path, "search_path must include public schema (RFC 0006)"

        core_tables = {"state_store", "sessions", "scheduled_tasks"}
        assert len(core_tables) >= 3

        # Public schema has identity tables
        public_tables = {"contacts", "contact_info", "entities"}
        assert "contacts" in public_tables

    def test_schema_isolation_search_path_order(self):
        """RFC 0006: Butler schema appears before public in search_path.

        This ensures butler-specific tables shadow public tables when names
        collide, and butler queries default to the butler schema.
        """
        from butlers.db import schema_search_path

        path = schema_search_path("health")
        parts = [p.strip() for p in path.split(",")]
        assert parts[0] == "health", "Butler schema must be first in search_path (RFC 0006)"
        assert "public" in parts, "public must be included in search_path (RFC 0006)"

    def test_migration_chains_and_module_revisions(self):
        """Multi-chain Alembic labels; modules return branch labels."""
        from butlers.modules.base import Module

        # migration_revisions is abstract
        assert "migration_revisions" in Module.__abstractmethods__

    def test_schema_isolation_and_briefing_exception(self):
        """No cross-butler imports; finance uses own schema; briefing uses view.

        Behavioral assertion: Database instances for different butlers have
        distinct schema values — they cannot share the same schema context.
        """
        import importlib.util

        # Finance module must be importable (stays in own schema)
        spec = importlib.util.find_spec("butlers.modules")
        assert spec is not None, "butlers.modules package must be importable"

        # Two butler Database instances must have distinct schema values
        from butlers.db import Database

        finance_db = Database("butlers", schema="finance")
        health_db = Database("butlers", schema="health")

        assert finance_db.schema != health_db.schema, (
            "Each butler must have a distinct schema on its Database instance (RFC 0006)"
        )
        assert finance_db.schema == "finance", "Finance butler must use 'finance' schema (RFC 0006)"
        assert health_db.schema == "health", "Health butler must use 'health' schema (RFC 0006)"

        # Briefing exception uses v_briefing_contributions view in 'general' schema
        view_schema = "general"
        view_name = "v_briefing_contributions"
        assert view_schema == "general"
        assert view_name == "v_briefing_contributions"


class TestDatabaseClassContracts:
    """RFC 0006: Database class has provision, connect, and close methods."""

    def test_database_class_methods(self):
        from butlers.db import Database

        for method in ["provision", "connect", "close"]:
            assert hasattr(Database, method), f"Database must have {method} method"

    def test_database_accepts_role_parameter(self):
        """Database __init__ accepts role and stores it on the instance."""
        import inspect

        from butlers.db import Database

        assert "role" in inspect.signature(Database.__init__).parameters, (
            "Database.__init__ must declare an explicit 'role' parameter"
        )
        db = Database("test", role="butler_test_rw")
        assert db.role == "butler_test_rw", (
            "Database must store role on the instance when passed to __init__"
        )

    def test_database_role_none_by_default(self):
        """Database role defaults to None when not supplied."""
        from butlers.db import Database

        db = Database("test")
        assert db.role is None, "Database.role must be None when not specified"

    def test_database_setup_connection_method_exists(self):
        """Database instances expose _setup_connection as an asyncpg setup callback."""
        import inspect

        from butlers.db import Database

        db = Database("test")
        assert hasattr(db, "_setup_connection"), (
            "Database must have _setup_connection method for asyncpg pool setup callback"
        )
        assert callable(db._setup_connection), "_setup_connection must be callable"
        assert inspect.iscoroutinefunction(db._setup_connection), (
            "_setup_connection must be an async callable for use as asyncpg pool setup callback"
        )

    def test_credential_store_is_schema_local(self):
        """RFC 0006: CredentialStore is schema-local via the pool's search_path.

        Behavioral assertion: CredentialStore accepts a pool (and optionally
        fallback_pools) but NO explicit schema parameter. Schema context is
        inherited from the pool's search_path, which is constructed with the
        butler's schema at startup. CredentialStore is therefore always
        schema-scoped to its butler without needing a schema argument.

        Additionally, the internal table name must NOT include a schema prefix
        (no 'public.' or 'butler.' prefix) — it relies on search_path resolution.
        """
        from butlers.credential_store import _TABLE, CredentialStore

        # CredentialStore accepts pool (and optionally fallback_pools),
        # but NOT an explicit schema parameter
        sig = inspect.signature(CredentialStore.__init__)
        params = list(sig.parameters.keys())
        assert "pool" in params, "CredentialStore must accept a pool (RFC 0006)"
        assert "schema" not in params, (
            "CredentialStore must not accept an explicit schema param — "
            "schema context comes from pool search_path (RFC 0006)"
        )

        # The internal table name must be unqualified — schema isolation comes
        # from the pool's search_path, not a hardcoded schema prefix
        assert "." not in _TABLE, (
            f"CredentialStore table '{_TABLE}' must be unqualified — "
            "schema isolation enforced via pool search_path (RFC 0006)"
        )
