"""Unit tests for daemon DB URL topology behavior."""

from __future__ import annotations

from butlers.daemon import ButlerDaemon
from butlers.db import Database


def test_build_db_url_includes_search_path_options_for_schema(tmp_path):
    """Schema-scoped DBs include libpq options for deterministic search_path."""
    daemon = ButlerDaemon(tmp_path)
    daemon.db = Database(
        db_name="butlers",
        schema="general",
        host="db.internal",
        port=5432,
        user="alice",
        password="secret",
    )

    url = daemon._build_db_url()

    assert (
        url == "postgresql://alice:secret@db.internal:5432/butlers"
        "?options=-csearch_path%3Dgeneral%2Cshared%2Cpublic"
    )


def test_build_db_url_legacy_mode_has_no_search_path_options(tmp_path):
    """Legacy DB URLs remain unchanged when no schema is configured."""
    daemon = ButlerDaemon(tmp_path)
    daemon.db = Database(
        db_name="butler_general",
        host="localhost",
        port=5432,
        user="butlers",
        password="butlers",
    )

    url = daemon._build_db_url()

    assert url == "postgresql://butlers:butlers@localhost:5432/butler_general"
