"""Regression tests for core_203: one home for a condition's ``resolution_reason``.

bu-o4i4j. The backfill lifts a nested ``metadata.identity_payload.
resolution_reason`` -- the shape the identity-version supersede path used to
write -- to the top-level key the explicit resolver has always used, so a
reader never has to know which path closed an episode.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_203_condition_resolution_reason_top_level.py"
)

# The two ledger tables share the same twelve-column shape; only the columns
# this backfill touches or keys off matter here.
_PROVISION_LEDGERS = """
CREATE TABLE public.infra_conditions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'open',
    metadata    JSONB
);
CREATE TABLE public.owner_conditions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'open',
    metadata    JSONB
);
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_203", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool, fn_name: str) -> None:
    """Replay the exact SQL emitted by a migration against real Postgres."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


async def _insert(pool, table: str, fingerprint: str, metadata: dict | None) -> str:
    # The pool carries the production JSONB codec, so a dict goes in as an
    # object -- passing pre-dumped text would land as a JSON *string*.
    return await pool.fetchval(
        f"INSERT INTO public.{table} (source, fingerprint, metadata) "
        "VALUES ('deploy_drift', $1, $2) RETURNING id",
        fingerprint,
        metadata,
    )


async def _metadata(pool, table: str, row_id: str) -> dict | None:
    raw = await pool.fetchval(f"SELECT metadata FROM public.{table} WHERE id = $1", row_id)
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.unit
def test_migration_revision_chain_and_scope() -> None:
    mod = _load_migration()

    assert mod.revision == "core_203"
    assert mod.down_revision == "core_202"
    assert mod.branch_labels is None
    assert mod.depends_on is None
    # Only `metadata` is rewritten; lifecycle columns are never touched.
    for column in ("state", "resolved_at", "recovered_after_s", "fingerprint", "summary"):
        assert f"SET {column}" not in mod.LIFT_RESOLUTION_REASON_SQL


@pytest.mark.unit
def test_downgrade_does_not_re_split_resolution_evidence() -> None:
    mod = _load_migration()
    fake_op = MagicMock()
    with patch.object(mod, "op", fake_op):
        mod.downgrade()
    fake_op.execute.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_noops_when_ledger_tables_are_absent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _apply(pool, "upgrade")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_lifts_nested_reason_and_leaves_everything_else(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_LEDGERS)
        successor = {"condition_id": "c1", "fingerprint": "b" * 64, "version": 2}
        nested_id = await _insert(
            pool,
            "infra_conditions",
            "a" * 64,
            {
                "kind": "drift",
                "identity_payload": {
                    "version": 1,
                    "resolution_reason": "superseded_by_identity_version_bump",
                    "successor": successor,
                },
            },
        )
        owner_nested_id = await _insert(
            pool,
            "owner_conditions",
            "d" * 64,
            {"identity_payload": {"version": 1, "resolution_reason": "superseded"}},
        )
        already_top_level_id = await _insert(
            pool,
            "infra_conditions",
            "e" * 64,
            {"resolution_reason": "satisfied", "identity_payload": {"version": 1}},
        )
        untouched_id = await _insert(
            pool, "infra_conditions", "f" * 64, {"identity_payload": {"version": 3}}
        )
        null_metadata_id = await _insert(pool, "infra_conditions", "0" * 64, None)

        await _apply(pool, "upgrade")

        assert await _metadata(pool, "infra_conditions", nested_id) == {
            "kind": "drift",
            "resolution_reason": "superseded_by_identity_version_bump",
            "identity_payload": {"version": 1, "successor": successor},
        }
        assert await _metadata(pool, "owner_conditions", owner_nested_id) == {
            "resolution_reason": "superseded",
            "identity_payload": {"version": 1},
        }
        assert await _metadata(pool, "infra_conditions", already_top_level_id) == {
            "resolution_reason": "satisfied",
            "identity_payload": {"version": 1},
        }
        assert await _metadata(pool, "infra_conditions", untouched_id) == {
            "identity_payload": {"version": 3}
        }
        assert await _metadata(pool, "infra_conditions", null_metadata_id) is None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_keeps_an_existing_top_level_reason_and_is_idempotent(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_LEDGERS)
        # Both locations populated: the resolver-owned top-level value is the
        # one every reader now consults, so it wins and the nested copy goes.
        both_id = await _insert(
            pool,
            "infra_conditions",
            "a" * 64,
            {
                "resolution_reason": "satisfied",
                "identity_payload": {"version": 1, "resolution_reason": "expired"},
            },
        )

        await _apply(pool, "upgrade")
        first = await _metadata(pool, "infra_conditions", both_id)
        first_xmin = await pool.fetchval(
            "SELECT xmin::text FROM public.infra_conditions WHERE id = $1", both_id
        )
        await _apply(pool, "upgrade")
        second_xmin = await pool.fetchval(
            "SELECT xmin::text FROM public.infra_conditions WHERE id = $1", both_id
        )

        assert first == {
            "resolution_reason": "satisfied",
            "identity_payload": {"version": 1},
        }
        assert second_xmin == first_xmin
