"""Config sources and the stable archive format. REQ-dashboard-permissions-001."""

import io
import json
import zipfile
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.routers.data_ops import _build_export_zip


async def test_config_export_reads_own_pools_into_legacy_member():
    db = DatabaseManager()
    sources = {}
    # Register in reverse order: archive source order must be deterministic.
    for name, schema in (("switchboard", None), ("general", "general_core")):
        pool = AsyncMock()
        pool.fetch.return_value = [{"butler_name": name, "runtime_type": "codex"}]
        db._pools[name] = pool
        db._butler_schemas[name] = schema
        db.register_configured_modules(name, frozenset())
        sources[name] = pool
    shared = AsyncMock()
    shared.fetch.return_value = []
    archive = await _build_export_zip(shared, "config", db)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.namelist() == [
            "public_runtime_config.ndjson",
            "public_model_catalog.ndjson",
            "public_permissions.ndjson",
        ]
        assert [json.loads(line) for line in zf.read(zf.namelist()[0]).splitlines()] == [
            {"butler_name": "general", "runtime_type": "codex"},
            {"butler_name": "switchboard", "runtime_type": "codex"},
        ]
    sources["general"].fetch.assert_awaited_once_with(
        'SELECT * FROM "general_core"."runtime_config" ORDER BY butler_name'
    )
    sources["switchboard"].fetch.assert_awaited_once_with(
        "SELECT * FROM runtime_config ORDER BY butler_name"
    )
    assert [c.args[0] for c in shared.fetch.await_args_list] == [
        "SELECT * FROM public.model_catalog ORDER BY id",
        "SELECT * FROM public.permissions ORDER BY butler, permission",
    ]
    # A genuinely census-free helper still uses its caller's local relation.
    shared.reset_mock()
    await _build_export_zip(shared, "config", DatabaseManager())
    assert shared.fetch.await_args_list[0].args[0] == (
        "SELECT * FROM runtime_config ORDER BY butler_name"
    )
    db.register_configured_modules("unavailable", frozenset())
    with pytest.raises(HTTPException) as missing:
        await _build_export_zip(shared, "config", db)
    assert missing.value.status_code == 503
    assert "unavailable" in missing.value.detail
    db._configured_butler_modules.pop("unavailable")
    db._butler_schemas["general"] = 'unsafe"schema'
    with pytest.raises(HTTPException) as unsafe:
        await _build_export_zip(shared, "config", db)
    assert unsafe.value.status_code == 500

    db._butler_schemas["general"] = "general_core"
    sources["switchboard"].fetch.side_effect = RuntimeError("synthetic source failure")
    shared.reset_mock()
    sources["general"].fetch.reset_mock()
    with pytest.raises(HTTPException) as failed:
        await _build_export_zip(shared, "config", db)
    assert failed.value.status_code == 500
    assert "public.runtime_config" in failed.value.detail
    sources["general"].fetch.assert_awaited_once()
    # Neither a foreign-pool fallback nor a later shared-table query may run.
    shared.fetch.assert_not_awaited()
