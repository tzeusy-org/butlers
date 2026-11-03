"""health_006 recovers statuses lost when a stripped condition was updated (bu-08zldr).

``condition_update`` on a status-less row wrote ``"<name>: active"`` and no status, so the
real status survives only in the superseded predecessor's content.  ``health_006`` walks
``supersedes_id`` back and restores it.

Real PostgreSQL (Docker), CI only.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "006_recover_updated_condition_statuses.py"
)


@pytest.fixture
async def memory_pool(db):
    return await db("core", "memory")


def _migration_sql(step: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("health_006", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("health_006", "health_005")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = sqls.append
    with patch.object(mod, "op", mock_op):
        getattr(mod, step)()
    return sqls


async def _run(pool, step: str) -> None:
    for sql in _migration_sql(step):
        await pool.execute(sql)


async def _fact(
    pool,
    subject: str,
    content: str,
    metadata: dict,
    *,
    supersedes: uuid.UUID | None = None,
    validity: str = "active",
) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler, metadata, supersedes_id) "
        "VALUES ($1, 'condition', $2, 'health', 'permanent', 0.0, $3, 1.0, 5.0, 'health', "
        "$4::text::jsonb, $5) RETURNING id",
        subject,
        content,
        validity,
        json.dumps(metadata),
        supersedes,
    )


async def _metadata(pool, fact_id: uuid.UUID) -> dict:
    value = await pool.fetchval("SELECT metadata FROM facts WHERE id = $1", fact_id)
    return json.loads(value) if isinstance(value, str) else dict(value)


async def test_status_lost_in_an_update_is_recovered_from_the_predecessor(memory_pool) -> None:
    name = {"name": "Asthma"}
    # Stripped by the decay sweep, then updated without a status: the status-less latest row.
    old = await _fact(memory_pool, "a", "Asthma: managed", name, validity="superseded")
    await _fact(memory_pool, "a", "Asthma: active", name, supersedes=old)
    # Same, but health_004 already stamped the latest row 'active' in place; a second update
    # without a status in between leaves two active-reading rows.
    old2 = await _fact(memory_pool, "b", "Flu: resolved", {"name": "Flu"}, validity="superseded")
    mid = await _fact(
        memory_pool, "b", "Flu: active", {"name": "Flu"}, supersedes=old2, validity="superseded"
    )
    stamped = await _fact(
        memory_pool,
        "b",
        "Flu: active",
        {"name": "Flu", "status": "active", "status_restored_by": "health_004"},
        supersedes=mid,
    )

    await _run(memory_pool, "upgrade")

    latest_a = await memory_pool.fetchval(
        "SELECT id FROM facts WHERE subject = 'a' AND validity = 'active'"
    )
    meta_a = await _metadata(memory_pool, latest_a)
    assert (meta_a["status"], meta_a["status_recovered_by"]) == ("managed", "health_006")
    meta_b = await _metadata(memory_pool, stamped)
    assert (meta_b["status"], meta_b["status_recovered_by"]) == ("resolved", "health_006")


async def test_rows_without_a_recoverable_status_are_untouched(memory_pool) -> None:
    name = {"name": "Eczema"}
    # The owner chose a status on the latest row: it has one, so it is not a candidate.
    old = await _fact(memory_pool, "chosen", "Eczema: managed", name, validity="superseded")
    chosen = await _fact(
        memory_pool, "chosen", "Eczema: active", {**name, "status": "active"}, supersedes=old
    )
    # No predecessor at all, and a predecessor that agrees with the current status.
    lone = await _fact(memory_pool, "lone", "Eczema: active", name)
    same_old = await _fact(memory_pool, "same", "Eczema: active", name, validity="superseded")
    same = await _fact(memory_pool, "same", "Eczema: active", name, supersedes=same_old)
    # A predecessor that carries its own status stops the walk.
    st_old = await _fact(
        memory_pool, "st", "Eczema: managed", {**name, "status": "managed"}, validity="superseded"
    )
    st = await _fact(memory_pool, "st", "Eczema: active", name, supersedes=st_old)
    # health_004 stamp inherited through an owner update: the owner may have chosen it.
    inh_root = await _fact(memory_pool, "inh", "Eczema: managed", name, validity="superseded")
    tagged_old = await _fact(
        memory_pool,
        "inh",
        "Eczema: active",
        {**name, "status": "active", "status_restored_by": "health_004"},
        supersedes=inh_root,
        validity="superseded",
    )
    inherited = await _fact(
        memory_pool,
        "inh",
        "Eczema: active",
        {**name, "status": "active", "status_restored_by": "health_004"},
        supersedes=tagged_old,
    )
    # A status-less row whose content already says managed is authoritative, not a candidate.
    auth_old = await _fact(memory_pool, "auth", "Eczema: resolved", name, validity="superseded")
    auth = await _fact(memory_pool, "auth", "Eczema: managed", name, supersedes=auth_old)
    ids = [chosen, lone, same, st, inherited, auth]
    before = {i: await _metadata(memory_pool, i) for i in ids}

    await _run(memory_pool, "upgrade")
    await _run(memory_pool, "upgrade")

    assert {i: await _metadata(memory_pool, i) for i in ids} == before
