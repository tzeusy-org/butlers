"""Durable retention of a sealed QA export outside agent control (bu-mc01y5.19).

The spec scenario "Committed proposal cannot be published" requires the sealed
artifact to survive worktree teardown.  These tests pin the byte codec against a
real git export and the store's fail-closed answers; the table's row security is
proven against real PostgreSQL in
``tests/migrations/test_qa_publication_exports_migration.py``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.core.qa.publication.export import ExportRefused, build_sealed_export
from butlers.core.qa.publication.retention import (
    decode_objects,
    encode_objects,
    load_retained_export,
    retain_export,
)
from butlers.core.qa.publication.state import PublicationRefused
from tests.core.qa.publication_fakes import QA_IDENTITY, commit_file, init_repo

pytestmark = pytest.mark.unit


async def _real_export(tmp_path: Path):
    repo = tmp_path / "repo"
    base = init_repo(repo)
    commit_file(repo, "src/app.py", "value = 2\n", "fix: guard value")
    head = commit_file(repo, "tests/test_app.py", "def test_value():\n    pass\n", "test: pin")
    return await build_sealed_export(
        repo / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
    )


def _row(export, objects: bytes) -> dict:
    return {
        "base_sha": export.base_sha,
        "head_sha": export.head_sha,
        "commits": list(export.commits),
        "objects": objects,
        "digest": export.digest,
    }


async def test_retained_bytes_round_trip_to_the_verified_export(tmp_path: Path) -> None:
    export = await _real_export(tmp_path)
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=export.digest)
    attempt_id = uuid.uuid4()

    await retain_export(pool, attempt_id, export)
    stored = pool.fetchval.await_args.args
    pool.fetchrow = AsyncMock(return_value=_row(export, stored[5]))

    assert stored[1:5] == (attempt_id, export.base_sha, export.head_sha, list(export.commits))
    assert await load_retained_export(pool, attempt_id) == export
    assert len(export.commits) == 2 and export.objects


async def test_altered_retained_bytes_refuse_on_load(tmp_path: Path) -> None:
    export = await _real_export(tmp_path)
    blob = next(obj for obj in export.objects if obj.kind == "blob")
    tampered = encode_objects(export.objects).replace(blob.data, blob.data.upper())
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_row(export, tampered))

    with pytest.raises(ExportRefused, match="export_tampered"):
        await load_retained_export(pool, uuid.uuid4())
    with pytest.raises(ExportRefused, match="export_tampered"):
        decode_objects(encode_objects(export.objects)[:-3])


async def test_a_different_export_for_the_same_attempt_is_refused(tmp_path: Path) -> None:
    export = await _real_export(tmp_path)
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="f" * 64)

    with pytest.raises(PublicationRefused, match="export_changed"):
        await retain_export(pool, uuid.uuid4(), export)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (asyncpg.ForeignKeyViolationError("fk"), "unknown_attempt"),
        (OSError("down"), "export_unretained"),
    ],
)
async def test_store_failures_refuse_closed(tmp_path: Path, error, code: str) -> None:
    export = await _real_export(tmp_path)
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=error)

    with pytest.raises(PublicationRefused, match=code):
        await retain_export(pool, uuid.uuid4(), export)


async def test_no_retained_export_loads_as_none() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)

    assert await load_retained_export(pool, uuid.uuid4()) is None
