"""The owner-facing QA proposal export route (bu-ejow5p.7.2)."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.core.qa.publication.diff_snapshot import export_touched_paths
from butlers.core.qa.publication.export import (
    ExportedObject,
    ExportRefused,
    SealedExport,
    _export_digest,
    _object_id,
)
from butlers.core.qa.publication.retention import decode_objects
from butlers.core.qa.publication.state import PublicationRefused
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_CASE = uuid.uuid4()


def _obj(kind: str, data: bytes) -> ExportedObject:
    return ExportedObject(_object_id(kind, data), kind, data)


def _tree(*entries: tuple[bytes, bytes, str]) -> bytes:
    return b"".join(mode + b" " + name + b"\0" + bytes.fromhex(oid) for mode, name, oid in entries)


def _export_touching(*files: str) -> SealedExport:
    """An export whose head commit adds ``files`` (one directory level at most)."""
    blobs = {name: _obj("blob", f"content of {name}\n".encode()) for name in files}
    top: list[tuple[bytes, bytes, str]] = []
    nested: dict[str, list[tuple[bytes, bytes, str]]] = {}
    objects = list(blobs.values())
    for name, blob in blobs.items():
        if "/" in name:
            directory, leaf = name.split("/", 1)
            nested.setdefault(directory, []).append((b"100644", leaf.encode(), blob.oid))
        else:
            top.append((b"100644", name.encode(), blob.oid))
    for directory, entries in nested.items():
        subtree = _obj("tree", _tree(*sorted(entries, key=lambda e: e[1])))
        objects.append(subtree)
        top.append((b"40000", directory.encode(), subtree.oid))
    root = _obj("tree", _tree(*sorted(top, key=lambda e: e[1])))
    ident = b"QA <qa@example.test> 1 +0000"
    commit = _obj(
        "commit",
        b"tree "
        + root.oid.encode()
        + b"\nparent "
        + b"b" * 40
        + b"\nauthor "
        + ident
        + b"\ncommitter "
        + ident
        + b"\n\nfix\n",
    )
    objects += [root, commit]
    commits = (commit.oid,)
    return SealedExport(
        "b" * 40,
        commit.oid,
        commits,
        tuple(objects),
        _export_digest("b" * 40, commit.oid, commits, tuple(objects)),
    )


_DIFF = [
    {"kind": "meta", "text": "diff --git a/src/a.py b/src/a.py"},
    {"kind": "+", "text": "x = 1"},
    {"kind": "meta", "text": "diff --git a/docs/b.md b/docs/b.md"},
]


def _app(*, attempt: Any, evidence: Any = None):
    pool = MagicMock()

    async def fetchrow(sql: str, *_args: Any):
        if "FROM public.healing_attempts" in sql:
            return attempt
        return evidence

    pool.fetchrow = AsyncMock(side_effect=fetchrow)
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return app


async def _get(app: Any, query: str = "") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/qa/cases/{_CASE}/proposal{query}")


def _attempt(**overrides: Any) -> dict[str, Any]:
    return {"branch_name": "qa/finance/abc123", "sealed_before_isolation": False, **overrides}


async def test_the_route_serves_the_verified_sealed_patch() -> None:
    export = _export_touching("src/a.py", "docs/b.md")
    app = _app(
        attempt=_attempt(),
        evidence={"finding_structured_evidence": {"investigation_notes": {"diff_snapshot": _DIFF}}},
    )
    with patch("butlers.api.routers.qa.load_retained_export", new=AsyncMock(return_value=export)):
        metadata = (await _get(app)).json()["data"]
        response = await _get(app, "?bundle=true")

    assert metadata["bundle_b64"] is None and metadata["bundle_bytes"] >= 0
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["branch_name"] == "qa/finance/abc123"
    assert (data["base_sha"], data["head_sha"], data["digest"]) == (
        export.base_sha,
        export.head_sha,
        export.digest,
    )
    assert data["paths"] == ["docs/b.md", "src/a.py"]
    assert data["diff_truncated"] is False and data["sealed_before_isolation"] is False
    assert decode_objects(base64.b64decode(data["bundle_b64"])) == export.objects
    assert data["bundle_bytes"] == len(base64.b64decode(data["bundle_b64"]))


async def test_a_pre_isolation_export_is_labelled_and_truncation_is_reported() -> None:
    app = _app(
        attempt=_attempt(sealed_before_isolation=True),
        evidence={
            "finding_structured_evidence": json.dumps(
                {
                    "investigation_notes": {
                        "diff_snapshot": [*_DIFF, {"kind": "meta", "text": "... (diff truncated)"}]
                    }
                }
            )
        },
    )
    with patch(
        "butlers.api.routers.qa.load_retained_export",
        new=AsyncMock(return_value=_export_touching("x.py")),
    ):
        data = (await _get(app)).json()["data"]

    assert data["sealed_before_isolation"] is True and data["diff_truncated"] is True


@pytest.mark.parametrize(
    ("loaded", "status"),
    [
        (None, 404),
        (PublicationRefused("export_pruned"), 410),
        (ExportRefused("export_tampered"), 409),
        (PublicationRefused("datastore_unavailable"), 503),
    ],
)
async def test_unavailable_exports_get_the_honest_status(loaded: Any, status: int) -> None:
    app = _app(attempt=_attempt())
    load = AsyncMock(return_value=loaded) if loaded is None else AsyncMock(side_effect=loaded)
    with patch("butlers.api.routers.qa.load_retained_export", new=load):
        response = await _get(app)

    assert response.status_code == status


async def test_an_unknown_case_is_not_found_and_never_touches_the_store() -> None:
    app = _app(attempt=None)
    load = AsyncMock()
    with patch("butlers.api.routers.qa.load_retained_export", new=load):
        response = await _get(app)

    assert response.status_code == 404
    load.assert_not_awaited()


def test_touched_paths_come_from_the_exports_own_objects() -> None:
    export = _export_touching("src/a.py", "docs/b.md", "top.txt")

    assert export_touched_paths(export) == (["docs/b.md", "src/a.py", "top.txt"], False)
    assert export_touched_paths(export, limit=2) == (["docs/b.md", "src/a.py"], True)


async def test_the_route_lists_paths_from_the_export_not_the_snapshot() -> None:
    export = _export_touching("src/real.py")
    app = _app(
        attempt=_attempt(),
        evidence={"finding_structured_evidence": {"investigation_notes": {"diff_snapshot": _DIFF}}},
    )
    with patch("butlers.api.routers.qa.load_retained_export", new=AsyncMock(return_value=export)):
        data = (await _get(app)).json()["data"]

    assert data["paths"] == ["src/real.py"]  # the display diff names other files


async def test_a_diff_that_fails_the_anonymizer_is_never_served() -> None:
    leaky = [{"kind": "+", "text": "key sk-ant-api03-" + "a" * 40}]
    app = _app(
        attempt=_attempt(),
        evidence={"finding_structured_evidence": {"investigation_notes": {"diff_snapshot": leaky}}},
    )
    with patch(
        "butlers.api.routers.qa.load_retained_export",
        new=AsyncMock(return_value=_export_touching("x.py")),
    ):
        response = await _get(app)

    assert response.status_code == 409 and "sk-ant" not in response.text


async def test_a_database_error_is_a_503_not_a_500() -> None:
    import asyncpg

    pool_app = _app(attempt=_attempt())
    db = pool_app.dependency_overrides[get_db]()
    db.credential_shared_pool.return_value.fetchrow = AsyncMock(
        side_effect=asyncpg.InterfaceError("gone")
    )

    assert (await _get(pool_app)).status_code == 503
