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
    prune_retained_exports,
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


def _row(export, objects: bytes, *, pruned: bool = False) -> dict:
    return {
        "base_sha": export.base_sha,
        "head_sha": export.head_sha,
        "commits": list(export.commits),
        "objects": objects,
        "digest": export.digest,
        "pruned": pruned,
    }


def _stored(digest: str, *, pruned: bool = False) -> dict:
    return {"digest": digest, "pruned": pruned}


async def test_retained_bytes_round_trip_to_the_verified_export(tmp_path: Path) -> None:
    export = await _real_export(tmp_path)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_stored(export.digest))
    attempt_id = uuid.uuid4()

    await retain_export(pool, attempt_id, export)
    stored = pool.fetchrow.await_args.args
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


@pytest.mark.parametrize(
    ("stored", "code"),
    [
        (_stored("f" * 64), "export_changed"),
        # The same digest whose bytes were pruned cannot stand in for a retained export.
        (None, "export_pruned"),
    ],
)
async def test_a_changed_or_pruned_export_for_the_same_attempt_is_refused(
    tmp_path: Path, stored: dict | None, code: str
) -> None:
    export = await _real_export(tmp_path)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=stored or _stored(export.digest, pruned=True))

    with pytest.raises(PublicationRefused, match=code):
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
    pool.fetchrow = AsyncMock(side_effect=error)

    with pytest.raises(PublicationRefused, match=code):
        await retain_export(pool, uuid.uuid4(), export)


async def test_no_retained_export_loads_as_none() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)

    assert await load_retained_export(pool, uuid.uuid4()) is None


async def test_a_pruned_export_refuses_on_load_instead_of_reading_as_none(tmp_path: Path) -> None:
    export = await _real_export(tmp_path)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_row(export, b"", pruned=True))

    with pytest.raises(PublicationRefused, match="export_pruned"):
        await load_retained_export(pool, uuid.uuid4())


async def test_prune_reports_the_rows_the_database_pruned() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 3")

    assert await prune_retained_exports(pool, limit=7) == 3
    assert pool.execute.await_args.args[1:] == (7,)
