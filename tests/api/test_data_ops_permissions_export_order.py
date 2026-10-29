"""Execute config export SQL against native permission keys. REQ-dashboard-permissions-001.

SQLite checks the portable SELECT/ORDER BY against an id-free composite-key
permission relation; the migrated PostgreSQL signed-download proof stays mandatory.
"""

import io
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.data_ops import _build_export_zip

pytestmark = pytest.mark.unit


class _SqlitePool:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    async def fetch(self, query: str):
        return self.connection.execute(query).fetchall()


async def test_config_export_preserves_permissions_in_composite_key_order():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("ATTACH DATABASE ':memory:' AS public")
        connection.execute("CREATE TABLE runtime_config (butler_name TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE public.model_catalog (id TEXT PRIMARY KEY)")
        # core_095 defines this composite key, with no synthetic id column.
        connection.execute(
            "CREATE TABLE public.permissions ("
            "butler TEXT NOT NULL, permission TEXT NOT NULL, granted BOOL NOT NULL, "
            "reason TEXT, updated_at TEXT NOT NULL, updated_by TEXT, "
            "PRIMARY KEY (butler, permission))"
        )
        rows = [
            ("switchboard", "read", 1, "third", "2026-10-20", "owner"),
            ("general", "write", 0, "second", "2026-10-20", "owner"),
            ("general", "read", 1, "first", "2026-10-20", "owner"),
        ]
        connection.executemany("INSERT INTO public.permissions VALUES (?,?,?,?,?,?)", rows)
        archive = await _build_export_zip(_SqlitePool(connection), "config", DatabaseManager())

    with zipfile.ZipFile(io.BytesIO(archive)) as exported:
        assert set(exported.namelist()) == {
            "public_runtime_config.ndjson",
            "public_model_catalog.ndjson",
            "public_permissions.ndjson",
        }
        actual = [
            json.loads(line) for line in exported.read("public_permissions.ndjson").splitlines()
        ]
    fields = ("butler", "permission", "granted", "reason", "updated_at", "updated_by")
    assert actual == [dict(zip(fields, row, strict=True)) for row in sorted(rows)]
