"""Real-Postgres regression: cursor_store.py's save_connector_settings must not
double-encode ``switchboard.connector_registry.settings`` (bu-dycxq — sibling
sweep to bu-cymc4/bu-x92jw/bu-bstqu/bu-c8b8e/bu-xfcpf/bu-x92jw).

``save_connector_settings`` used to ``json.dumps()`` the settings dict and bind
it through an explicit ``$3::jsonb`` cast. Every asyncpg pool in this codebase
registers a JSONB type codec (``register_jsonb_codec``, ``src/butlers/db.py``)
whose encoder calls ``json.dumps()`` on the bound Python object itself — so the
old code path double-encoded ``settings`` into a jsonb-typed STRING instead of
an OBJECT. Downstream, ``load_connector_settings``/``save_connector_settings``
carried an ``isinstance(value, str)`` workaround to tolerate the corrupted
shape on read.

The mocked-pool unit tests in ``tests/connectors/test_steam_config_store.py``
cannot catch this class of bug — they never round-trip a value through
asyncpg's real JSONB codec. These tests exercise the real
``save_connector_settings``/``load_connector_settings`` functions against a
real, migrated (``switchboard`` chain) Postgres instance (testcontainers).

Live-data audit (read-only, butlers-dev, 2026-07-05): 0 of 21
``switchboard.connector_registry`` rows (2 with non-NULL settings) have
``jsonb_typeof(settings) = 'string'`` — no production corruption has occurred.
The ``isinstance(value, str)`` read-side workarounds in ``cursor_store.py``
were therefore removed as part of this fix (see bead notes).
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.connectors.cursor_store import load_connector_settings, save_connector_settings
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + switchboard chains — switchboard.connector_registry.

    ``core`` is required alongside ``switchboard`` as of migration ``sw_019``
    (bu-aga08): ``switchboard.routing_verdict_log`` FKs to
    ``public.ingestion_events``, so the switchboard chain no longer migrates
    standalone.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE switchboard.connector_registry")
    yield p
    await p.close()


async def test_save_then_load_round_trips_as_object(pool: asyncpg.Pool) -> None:
    """save_connector_settings + load_connector_settings round-trip a dict, not
    a jsonb-typed string, and no ``isinstance(..., str)`` parsing is needed."""
    merged = await save_connector_settings(pool, "steam", "steam:config", {"account_rescan_s": 120})
    assert merged == {"account_rescan_s": 120}

    row = await pool.fetchrow(
        "SELECT settings FROM switchboard.connector_registry "
        "WHERE connector_type = $1 AND endpoint_identity = $2",
        "steam",
        "steam:config",
    )
    stored = row["settings"]
    assert isinstance(stored, dict), (
        f"Expected settings to be stored as a jsonb OBJECT but got "
        f"{type(stored).__name__!r}: {stored!r}"
    )
    assert stored == {"account_rescan_s": 120}

    loaded = await load_connector_settings(pool, "steam", "steam:config")
    assert loaded == {"account_rescan_s": 120}


async def test_save_shallow_merges_existing_settings(pool: asyncpg.Pool) -> None:
    """A second save shallow-merges into the existing settings object (JSONB
    ``||``), rather than corrupting it into an array or a nested string."""
    await save_connector_settings(pool, "steam", "steam:config", {"account_rescan_s": 60})
    merged = await save_connector_settings(
        pool, "steam", "steam:config", {"heartbeat_interval_s": 30}
    )

    assert merged == {"account_rescan_s": 60, "heartbeat_interval_s": 30}

    row = await pool.fetchrow(
        "SELECT settings FROM switchboard.connector_registry "
        "WHERE connector_type = $1 AND endpoint_identity = $2",
        "steam",
        "steam:config",
    )
    assert isinstance(row["settings"], dict)
    assert row["settings"] == {"account_rescan_s": 60, "heartbeat_interval_s": 30}


async def test_save_sanitizes_non_json_native_values(pool: asyncpg.Pool) -> None:
    """Non-JSON-native values (e.g. tuples) are sanitized (default=str fallback
    for anything json can't natively serialize) rather than raising or binding
    an unencodable Python object straight through to asyncpg."""
    merged = await save_connector_settings(
        pool, "steam", "steam:config", {"poll_intervals": {"recently_played": 300}}
    )
    assert merged == {"poll_intervals": {"recently_played": 300}}


async def test_buggy_write_path_would_have_corrupted_settings_into_a_string(
    pool: asyncpg.Pool,
) -> None:
    """Documents the pre-fix failure mode: json.dumps() + ``::jsonb`` double
    encodes the settings dict into a jsonb-typed STRING instead of an OBJECT,
    the exact anti-pattern this bead removes from ``save_connector_settings``."""
    import json

    buggy_json_string = json.dumps({"account_rescan_s": 120})
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, settings)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (connector_type, endpoint_identity)
        DO UPDATE SET settings = EXCLUDED.settings
        """,
        "buggy",
        "buggy:config",
        buggy_json_string,
    )

    row = await pool.fetchrow(
        "SELECT settings FROM switchboard.connector_registry "
        "WHERE connector_type = $1 AND endpoint_identity = $2",
        "buggy",
        "buggy:config",
    )
    stored = row["settings"]
    assert isinstance(stored, str), (
        "Expected the buggy write path to corrupt settings into a jsonb "
        f"STRING but got {type(stored).__name__!r}: {stored!r}"
    )
    assert json.loads(stored) == {"account_rescan_s": 120}
