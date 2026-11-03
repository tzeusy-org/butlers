"""Regression coverage for the QA repository organization retarget migration."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_235_qa_repo_org_rename.py"
)
_LEGACY_REPOSITORY_URL = "https://github.com/Tzeusy/butlers"
_CANONICAL_REPOSITORY_URL = "https://github.com/tzeusy-org/butlers"


async def _apply(pool, fn_name: str) -> None:
    """Execute exactly the SQL emitted by one migration entrypoint."""
    module = load_migration(_MIGRATION_PATH, "core_235")
    sqls: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = sqls.append
    with patch.object(module, "op", fake_op):
        getattr(module, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


async def _provision_multi_row_repo_config(pool) -> None:
    # Deliberately not the real core_060 shape: the real table is a singleton,
    # and this test normalizes several legacy URL spellings in one upgrade.
    await pool.execute(
        f"""
        CREATE TABLE public.qa_repo_config (
            id UUID PRIMARY KEY,
            repo_url TEXT NOT NULL DEFAULT '{_LEGACY_REPOSITORY_URL}',
            clone_path TEXT,
            last_synced_at TIMESTAMPTZ,
            last_sync_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


@pytest.fixture
async def core_234_pool(db):
    """Core at core_235's predecessor: the real, unseeded qa_allowed_repositories."""
    return await db("core", revisions={"core": "core_234"})


async def _repo_url_default(pool) -> str | None:
    return await pool.fetchval(
        """
        SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
        FROM pg_attrdef AS default_value
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = default_value.adrelid
         AND attribute.attnum = default_value.adnum
        WHERE default_value.adrelid = 'public.qa_repo_config'::regclass
          AND attribute.attname = 'repo_url'
        """
    )


@pytest.mark.unit
def test_migration_chain_metadata_and_downgrade_preserves_operator_choices() -> None:
    from butlers.migrations import get_chain_revision_ids

    module = load_migration(_MIGRATION_PATH, "core_235")

    assert module.revision == "core_235"
    assert module.down_revision == "core_234"
    assert module.revision in get_chain_revision_ids("core")
    assert module.branch_labels is None
    assert module.depends_on is None
    assert "tzeusy-org/butlers" in module.RETARGET_QA_REPOSITORY_SQL

    fake_op = MagicMock()
    with patch.object(module, "op", fake_op):
        module.downgrade()

    fake_op.execute.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_normalizes_recognised_legacy_urls_and_preserves_custom_default(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _provision_multi_row_repo_config(pool)
        old_https_id = uuid.uuid4()
        old_ssh_id = uuid.uuid4()
        custom_id = uuid.uuid4()
        await pool.executemany(
            "INSERT INTO public.qa_repo_config (id, repo_url) VALUES ($1, $2)",
            [
                (old_https_id, "https://github.com/TZEUSY/butlers.git/"),
                (old_ssh_id, "git@github.com:Tzeusy/butlers.git"),
                (custom_id, "https://github.com/example/other-repository"),
            ],
        )

        await _apply(pool, "upgrade")
        await _apply(pool, "upgrade")

        rows = await pool.fetch("SELECT id, repo_url FROM public.qa_repo_config ORDER BY id")
        urls = {row["id"]: row["repo_url"] for row in rows}
        assert urls == {
            old_https_id: _CANONICAL_REPOSITORY_URL,
            old_ssh_id: _CANONICAL_REPOSITORY_URL,
            custom_id: "https://github.com/example/other-repository",
        }
        assert _CANONICAL_REPOSITORY_URL in (await _repo_url_default(pool) or "")

        await pool.execute(
            "ALTER TABLE public.qa_repo_config "
            "ALTER COLUMN repo_url SET DEFAULT 'https://github.com/example/operator-choice'"
        )
        await _apply(pool, "upgrade")
        assert "https://github.com/example/operator-choice" in (await _repo_url_default(pool) or "")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_preserves_existing_org_whitelist_choice_and_removes_only_stale_duplicates(
    core_234_pool,
) -> None:
    pool = core_234_pool
    existing_org_id = uuid.uuid4()
    old_id = uuid.uuid4()
    old_case_variant_id = uuid.uuid4()
    unrelated_id = uuid.uuid4()
    earlier = datetime(2026, 9, 1, tzinfo=UTC)
    later = datetime(2026, 9, 2, tzinfo=UTC)
    await pool.executemany(
        """
        INSERT INTO public.qa_allowed_repositories (id, owner, repo, enabled, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $5)
        """,
        [
            (old_id, "Tzeusy", "butlers", False, earlier),
            (old_case_variant_id, "tzeusy", "BUTLERS", True, later),
            (existing_org_id, "tzeusy-org", "butlers", True, later),
            (unrelated_id, "example", "butlers", True, earlier),
        ],
    )

    await _apply(pool, "upgrade")
    await _apply(pool, "upgrade")

    target_rows = await pool.fetch(
        """
        SELECT id, owner, repo, enabled
        FROM public.qa_allowed_repositories
        WHERE lower(owner) IN ('tzeusy', 'tzeusy-org')
          AND lower(repo) = 'butlers'
        """
    )
    assert [tuple(row.values()) for row in target_rows] == [
        (existing_org_id, "tzeusy-org", "butlers", True)
    ]
    assert (
        await pool.fetchval(
            "SELECT owner FROM public.qa_allowed_repositories WHERE id = $1", unrelated_id
        )
        == "example"
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_promotes_legacy_whitelist_entry_and_downgrade_does_not_rewrite_it(
    core_234_pool,
) -> None:
    pool = core_234_pool
    legacy_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.qa_allowed_repositories (id, owner, repo, enabled)
        VALUES ($1, 'Tzeusy', 'butlers', FALSE)
        """,
        legacy_id,
    )

    await _apply(pool, "upgrade")
    upgraded = await pool.fetchrow(
        "SELECT id, owner, repo, enabled FROM public.qa_allowed_repositories"
    )
    assert tuple(upgraded.values()) == (legacy_id, "tzeusy-org", "butlers", False)

    await pool.execute(
        "UPDATE public.qa_allowed_repositories SET enabled = TRUE WHERE id = $1", legacy_id
    )
    await _apply(pool, "downgrade")
    after_downgrade = await pool.fetchrow(
        "SELECT id, owner, repo, enabled FROM public.qa_allowed_repositories"
    )
    assert tuple(after_downgrade.values()) == (legacy_id, "tzeusy-org", "butlers", True)
