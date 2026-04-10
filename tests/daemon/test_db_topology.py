"""Unit tests for daemon DB URL topology behavior."""

from __future__ import annotations

import pytest

from butlers.daemon import ButlerDaemon
from butlers.db import Database

pytestmark = pytest.mark.unit


def test_build_db_url(tmp_path):
    """Schema-scoped URLs include search_path options; legacy URLs do not; special chars encoded."""
    # Schema-scoped: includes libpq search_path options
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
        url
        == "postgresql://alice:secret@db.internal:5432/butlers?options=-csearch_path%3Dgeneral%2Cpublic"
    )

    # Legacy mode: no schema, no search_path options
    daemon2 = ButlerDaemon(tmp_path)
    daemon2.db = Database(
        db_name="butlers",
        host="localhost",
        port=5432,
        user="butlers",
        password="butlers",
    )
    assert daemon2._build_db_url() == "postgresql://butlers:butlers@localhost:5432/butlers"

    # Special characters URL-encoded
    daemon3 = ButlerDaemon(tmp_path)
    daemon3.db = Database(
        db_name="butlers prod",
        schema="general",
        host="db.internal",
        port=5432,
        user="alice+ops",
        password="s ec/re:t@#",
    )
    url3 = daemon3._build_db_url()
    assert url3.startswith(
        "postgresql://alice%2Bops:s%20ec%2Fre%3At%40%23@db.internal:5432/butlers%20prod"
    )
    assert url3.endswith("?options=-csearch_path%3Dgeneral%2Cpublic")
