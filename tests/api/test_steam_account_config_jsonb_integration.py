"""Real-Postgres regression for the Steam per-account config jsonb bug (bu-yha6c).

``api/routers/steam.py``'s ``update_steam_account_config`` used to pre-serialize
the metadata patch with ``json.dumps()`` and bind it through ``$1::jsonb``.
Every asyncpg pool in this codebase registers a JSONB type codec
(``register_jsonb_codec``, ``src/butlers/db.py``) whose encoder calls
``json.dumps()`` itself -- so the old code path double-encoded the patch into a
jsonb-typed STRING instead of an OBJECT. Postgres's ``||`` operator between a
jsonb object and a jsonb scalar coerces *both* operands into an array,
corrupting ``steam_accounts.metadata`` into
``[{...previous...}, "<json string>"]`` on every config update -- the same
anti-pattern fixed for ``calendar_workspace.py``'s undo marker in bu-x92jw
(PR #2925).

The mocked-pool unit tests in ``tests/api/test_steam_connector_config.py``
cannot catch this class of bug -- they only assert on the Python value handed
to the mock, never round-trip through asyncpg's real JSONB codec. These tests
call the real production endpoint functions (``update_steam_account_config``,
``get_steam_account_config``) against a real Postgres-backed
``public.steam_accounts`` table to prove:

1. The fixed write path (bind the metadata patch as a plain dict, no
   ``json.dumps``, no ``::jsonb`` cast) round-trips ``metadata`` as a jsonb
   OBJECT across repeated PATCH calls, preserving previously-set keys via the
   ``||`` merge.
2. GET reads the stored overrides back correctly after a PATCH.
3. The old buggy write path (``json.dumps()`` + ``::jsonb`` cast) reproduces
   the exact array-corruption failure mode, documenting why the fix matters.

Live-data audit (read-only, butlers-dev, 2026-07-05): ``public.steam_accounts``
has exactly 1 row; ``jsonb_typeof(metadata) = 'object'`` -- no corruption has
occurred in production. No self-heal/repair path is needed.
"""

from __future__ import annotations

import json
import shutil
import uuid

import pytest

from butlers.api.models.steam import SteamAccountConfigOverrides, SteamPollIntervals
from butlers.api.routers.steam import get_steam_account_config, update_steam_account_config

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Minimal shape of public.steam_accounts (core_008_external_accounts.py) needed
# for the config read/write path under test. entity_id intentionally drops the
# public.entities FK/NOT NULL -- resolve_steam_account() never touches entities.
_CREATE_STEAM_ACCOUNTS_TABLE_SQL = """
CREATE TABLE public.steam_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID,
    steam_id BIGINT UNIQUE NOT NULL,
    display_name VARCHAR,
    profile_url VARCHAR,
    avatar_url VARCHAR,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    status VARCHAR NOT NULL DEFAULT 'active',
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_poll_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
)
"""

# The pre-fix pattern: json.dumps() the patch, then bind through an explicit
# ::jsonb cast. Reproduced here (not imported from prod code, which no longer
# contains it) purely to document and lock in the failure mode being guarded
# against.
_BUGGY_UPDATE_SQL = """
UPDATE public.steam_accounts
SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb
WHERE id = $2
"""


class _RealDBManager:
    """Minimal DatabaseManager stand-in exposing credential_shared_pool()."""

    def __init__(self, pool):
        self._pool = pool

    def credential_shared_pool(self):
        return self._pool


async def _insert_account(pool, *, steam_id: int, account_id: uuid.UUID | None = None) -> uuid.UUID:
    account_id = account_id or uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.steam_accounts (id, steam_id, display_name) VALUES ($1, $2, $3)",
        account_id,
        steam_id,
        "TestUser",
    )
    return account_id


async def test_update_steam_account_config_roundtrips_metadata_as_dict(provisioned_postgres_pool):
    """PATCH must store metadata as a jsonb OBJECT, and merge across calls."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_STEAM_ACCOUNTS_TABLE_SQL)
        account_id = await _insert_account(pool, steam_id=76561198000000123)
        db_manager = _RealDBManager(pool)

        body = SteamAccountConfigOverrides(
            poll_intervals=SteamPollIntervals(recently_played=600),
            max_tracked_games=5,
        )
        resp = await update_steam_account_config(
            account_id=account_id, body=body, db_manager=db_manager
        )
        assert resp.success is True

        row = await pool.fetchrow(
            "SELECT metadata FROM public.steam_accounts WHERE id = $1", account_id
        )
        stored = row["metadata"]
        assert isinstance(stored, dict), (
            f"metadata arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string/array."
        )
        assert stored["max_tracked_games"] == 5
        assert stored["poll_intervals"] == {"recently_played": 600}

        # GET must read the same overrides back.
        get_resp = await get_steam_account_config(account_id=account_id, db_manager=db_manager)
        assert get_resp.overrides.max_tracked_games == 5
        assert get_resp.overrides.poll_intervals is not None
        assert get_resp.overrides.poll_intervals.recently_played == 600

        # A second PATCH (merge semantics) must preserve the first patch's
        # keys instead of corrupting metadata into an array.
        body2 = SteamAccountConfigOverrides(max_tracked_games=10)
        resp2 = await update_steam_account_config(
            account_id=account_id, body=body2, db_manager=db_manager
        )
        assert resp2.success is True

        row2 = await pool.fetchrow(
            "SELECT metadata FROM public.steam_accounts WHERE id = $1", account_id
        )
        stored2 = row2["metadata"]
        assert isinstance(stored2, dict), (
            f"metadata arrived as {type(stored2).__name__!r}, not a dict after a second "
            f"PATCH — the jsonb column was double-encoded into a string/array: {stored2!r}"
        )
        assert stored2["max_tracked_games"] == 10
        assert stored2["poll_intervals"] == {"recently_played": 600}


async def test_buggy_write_path_corrupts_metadata_into_array(provisioned_postgres_pool):
    """Documents the pre-fix failure mode: json.dumps() + ::jsonb double-encodes
    the patch, and Postgres's object-||-scalar coercion turns metadata into an
    array instead of a merged object."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_STEAM_ACCOUNTS_TABLE_SQL)
        account_id = await _insert_account(pool, steam_id=76561198000000456)

        meta_patch_json_string = json.dumps({"max_tracked_games": 5})
        await pool.execute(_BUGGY_UPDATE_SQL, meta_patch_json_string, account_id)

        row = await pool.fetchrow(
            "SELECT metadata FROM public.steam_accounts WHERE id = $1", account_id
        )
        stored = row["metadata"]
        assert isinstance(stored, list), (
            "Expected the buggy path to corrupt metadata into a jsonb ARRAY but got "
            f"{type(stored).__name__!r}: {stored!r}"
        )
        # The empty base object and the double-encoded patch both get coerced
        # into single-element arrays and concatenated: the second element is
        # the raw JSON *string* (not re-parsed into a nested object), proving
        # the patch was double-encoded rather than merged as an object.
        assert stored == [{}, meta_patch_json_string]
