"""Tests for the public.audit_log primitive API.

Covers:
- Static-check: no destructive statements targeting audit_log exist in repo code.
- audit.append() helper: inserts row, increments Prometheus counter, returns id.
- audit.append() raises AuditTableNotAvailableError on UndefinedTableError.
- GET /api/audit-log: returns PaginatedResponse[AuditLogEntry], filters, ts DESC, offset.
- GET /api/audit-log/{id}: returns ApiResponse[AuditLogEntry] or 404.
- GET /api/audit-log and /{id} return HTTP 503 when table is missing.
- AuditLogEntry.from_record: IP coercion, None fields.
"""

from __future__ import annotations

import ast
import asyncio
import ipaddress
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from asyncpg.exceptions import UndefinedTableError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.models.audit import AuditLogEntry
from butlers.api.routers import audit as audit_module
from butlers.api.routers.audit import AuditTableNotAvailableError, append
from butlers.core.credential_keys import normalize_credential_key
from butlers.testing.records import Record
from tests.api.auth_helpers import create_authenticated_domain_app as create_app
from tests.migrations.test_core_279_audit_writer_integrity import (
    _RUNTIME_ROLES,
)
from tests.migrations.test_core_279_audit_writer_integrity import (
    audit_database as audit_database,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Static-check: append-only invariant
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_DIRS = [
    _REPO_ROOT / "src",
    _REPO_ROOT / "tests",
    _REPO_ROOT / "roster",
]

# Pattern that would violate the append-only contract.
# Detects SQL DELETE targeting audit_log in Python string literals.
_DELETE_PATTERN = re.compile(
    r"DELETE\s+FROM\s+(?:public\.)?audit_log\b",
    re.IGNORECASE,
)

# This file itself describes the pattern in comments — skip it.
_THIS_FILE = Path(__file__).resolve()
# Direct audit producers live in the framework and roster production trees.
_DIRECT_AUDIT_PRODUCER_ROOTS = (
    _REPO_ROOT / "src" / "butlers",
    _REPO_ROOT / "roster",
)
_AUDIT_RESULT_GUARD_FIXTURE_ROOT = _REPO_ROOT / "tests" / "fixtures" / "audit_result_guard"


def _iter_python_files():
    for src_dir in _SOURCE_DIRS:
        if src_dir.exists():
            for path in src_dir.rglob("*.py"):
                if path.resolve() != _THIS_FILE:
                    yield path


_INTERPOLATION = "_expr_"


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Return ids of Constant nodes that occupy Python docstring positions."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _string_literals(source: str, *, filename: str) -> list[tuple[int, str]]:
    """Return non-docstring string literals with f-strings reassembled."""
    tree = ast.parse(source, filename=filename)
    skip = _docstring_node_ids(tree)
    literals: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                skip.add(id(value))
                parts.append(value.value)
            else:
                parts.append(_INTERPOLATION)
        literals.append((node.lineno, "".join(parts)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            literals.append((node.lineno, node.value))

    return literals


def _audit_log_delete_violations(source: str, path: Path) -> list[str]:
    """Return real audit_log DELETE literals while ignoring Python prose."""
    return [
        f"{path}:{lineno}: {literal.strip()}"
        for lineno, literal in _string_literals(source, filename=str(path))
        if _DELETE_PATTERN.search(literal)
    ]


def _direct_audit_result_omissions(source: str, path: Path) -> list[str]:
    """Return source locations for direct audit writers without an outcome.

    The generic router remains compatible with an omitted result. This guard is
    deliberately limited to the production convention ``audit_router.append``
    so direct producers cannot silently lose their own semantic attribution.
    """
    tree = ast.parse(source, filename=str(path))
    omissions: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "audit_router"
        ):
            continue
        result_keyword = next(
            (keyword for keyword in node.keywords if keyword.arg == "result"), None
        )
        if result_keyword is None or (
            isinstance(result_keyword.value, ast.Constant) and result_keyword.value.value is None
        ):
            omissions.append(f"{path}:{node.lineno}")
    return omissions


def _direct_audit_result_omissions_in_roots(
    roots: tuple[Path, ...], *, relative_to: Path
) -> list[str]:
    """Return direct-writer omissions across the supplied production roots."""
    omissions: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8", errors="replace")
            omissions.extend(_direct_audit_result_omissions(source, path.relative_to(relative_to)))
    return omissions


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            'sql = "DELETE FROM audit_log WHERE id = $1"\n',
            ["literal.py:1: DELETE FROM audit_log WHERE id = $1"],
        ),
        (
            'sql = f"DELETE FROM public.audit_log WHERE id = {audit_id}"\n',
            ["literal.py:1: DELETE FROM public.audit_log WHERE id = _expr_"],
        ),
    ],
)
def test_audit_log_delete_guard_reports_sql_literals(source: str, expected: list[str]):
    assert _audit_log_delete_violations(source, Path("literal.py")) == expected


def test_audit_log_delete_guard_ignores_comments_and_docstrings():
    source = '''\
# DELETE FROM audit_log is forbidden.
"""Documentation may mention DELETE FROM audit_log."""

class Guidance:
    """Never issue DELETE FROM public.audit_log."""


def describe_guard():
    """Explain why DELETE FROM audit_log is forbidden."""


async def describe_async_guard():
    """The same DELETE FROM audit_log prose is safe here."""
'''

    assert _audit_log_delete_violations(source, Path("prose.py")) == []


def test_no_delete_from_audit_log_rejects_injected_repo_file(tmp_path, monkeypatch):
    """The repository traversal, not only the literal helper, must stay causal."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "prose.py").write_text(
        '# DELETE FROM audit_log is forbidden.\n"""Documentation may mention DELETE FROM audit_log."""\n',
        encoding="utf-8",
    )
    (source_root / "violating_audit_log_delete.py").write_text(
        'sql = f"DELETE FROM public.audit_log WHERE id = {audit_id}"\n',
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "_REPO_ROOT", tmp_path)
    monkeypatch.setitem(globals(), "_SOURCE_DIRS", [source_root])

    with pytest.raises(AssertionError) as exc_info:
        test_no_delete_from_audit_log_in_repo()

    message = str(exc_info.value)
    assert "src/violating_audit_log_delete.py:1: DELETE FROM public.audit_log" in message
    assert "src/prose.py" not in message


def test_no_delete_from_audit_log_in_repo():
    """Fail CI if a Python SQL literal deletes from the append-only audit_log.

    Comments never appear in the AST and docstrings are excluded structurally;
    every other literal is scanned, including reconstructed f-strings.
    """
    violations: list[str] = []
    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        violations.extend(_audit_log_delete_violations(text, path.relative_to(_REPO_ROOT)))

    assert not violations, (
        "Found destructive statement(s) targeting public.audit_log — "
        "the table is append-only:\n" + "\n".join(violations)
    )


def test_direct_production_audit_writers_supply_explicit_results():
    """Direct producers in src/butlers and roster own outcomes; aliases remain optional."""
    omissions = _direct_audit_result_omissions_in_roots(
        _DIRECT_AUDIT_PRODUCER_ROOTS,
        relative_to=_REPO_ROOT,
    )

    assert not omissions, (
        "Direct production audit writers must pass a producer-meaningful result:\n"
        + "\n".join(omissions)
    )


def test_direct_audit_result_guard_reports_missing_writer_location():
    omissions = _direct_audit_result_omissions(
        "async def writer():\n    await audit_router.append(pool, 'owner', 'action')\n",
        Path("synthetic_direct_writer.py"),
    )

    assert omissions == ["synthetic_direct_writer.py:2"]


def test_direct_audit_result_guard_scans_roster_fixture():
    """Roster direct writers are covered; generic aliases remain optional."""
    omissions = _direct_audit_result_omissions_in_roots(
        (
            _AUDIT_RESULT_GUARD_FIXTURE_ROOT / "src" / "butlers",
            _AUDIT_RESULT_GUARD_FIXTURE_ROOT / "roster",
        ),
        relative_to=_AUDIT_RESULT_GUARD_FIXTURE_ROOT,
    )

    assert omissions == ["roster/switchboard/violating_audit_writer.py:5"]


# ---------------------------------------------------------------------------
# AuditLogEntry.from_record
# ---------------------------------------------------------------------------


def _audit_row(**kwargs) -> Record:
    defaults = {
        "id": 1,
        "ts": datetime.now(tz=UTC),
        "actor": "owner",
        "action": "test_action",
        "target": None,
        "note": None,
        "ip": None,
        "request_id": None,
    }
    return Record({**defaults, **kwargs})


def test_audit_log_entry_from_record_with_ip_address_object():
    """ip can arrive as an ipaddress.IPv4Address from asyncpg; None fields stay None."""
    row = _audit_row(ip=ipaddress.IPv4Address("10.0.0.1"))
    entry = AuditLogEntry.from_record(row)
    assert entry.ip == "10.0.0.1"
    assert entry.target is None
    assert entry.note is None
    assert entry.request_id is None


def test_audit_log_entry_from_record_projects_core_122_fields():
    """from_record() now reads metadata/result/error when the query selected them
    (JARVIS audit move 6 -- previously they were documented as intentionally
    unwired and always defaulted to None regardless of what the row carried)."""
    row = _audit_row(
        metadata={"path": "/api/x"},
        result="error",
        error="boom",
    )
    entry = AuditLogEntry.from_record(row)
    assert entry.metadata == {"path": "/api/x"}
    assert entry.result == "error"
    assert entry.error == "boom"


class TestFromRecordMetadataJsonbTypeofCases:
    """bu-hmdqz.4: ``from_record`` must tolerate all three ``jsonb_typeof``
    cases actually observed on ``public.audit_log.metadata`` -- object (the
    contractual shape), string (a since-fixed write path double-JSON-encoded
    ~349k rows this way, 2026-06-14 -> 07-05), and null. Before this fix, a
    string-typed row raised a pydantic ``ValidationError`` that surfaced as an
    HTTP 500 and took down the entire audit page for any query that happened
    to touch a poisoned row (e.g. ``GET /api/audit-log?actor=memory``).
    """

    def test_object_typed_metadata_passes_through_unchanged(self) -> None:
        row = _audit_row(metadata={"path": "/api/x"})
        entry = AuditLogEntry.from_record(row)
        assert entry.metadata == {"path": "/api/x"}

    def test_null_typed_metadata_stays_none(self) -> None:
        row = _audit_row(metadata=None)
        entry = AuditLogEntry.from_record(row)
        assert entry.metadata is None

    def test_string_typed_metadata_double_encoding_a_dict_is_decoded(self) -> None:
        """The poisoned shape: metadata column holds the JSON *text* of an
        object (i.e. the original writer effectively called
        ``json.dumps(json.dumps(data))``). asyncpg hands this back as a
        Python ``str``; from_record must decode it back to the dict."""
        row = _audit_row(metadata='{"path": "/api/x", "n": 1}')
        entry = AuditLogEntry.from_record(row)
        assert entry.metadata == {"path": "/api/x", "n": 1}

    def test_string_typed_metadata_that_is_not_json_is_wrapped_not_raised(self) -> None:
        """A string that isn't itself valid JSON (some poisoned rows genuinely
        aren't double-encoded) must not raise -- wrap it losslessly instead."""
        row = _audit_row(metadata="actor started session")
        entry = AuditLogEntry.from_record(row)
        assert entry.metadata == {"_raw": "actor started session"}

    def test_string_typed_metadata_decoding_to_a_non_dict_is_wrapped(self) -> None:
        """A string that IS valid JSON but decodes to a scalar/array (not an
        object) still can't satisfy `dict[str, Any]` -- wrap it too."""
        row = _audit_row(metadata='"just a string"')
        entry = AuditLogEntry.from_record(row)
        assert entry.metadata == {"_raw": '"just a string"'}


def test_audit_log_entry_from_record_missing_core_122_columns_defaults_none():
    """A row whose query never selected metadata/result/error (e.g. a caller
    using the old three-column-shorter SELECT) still deserialises to None for
    each, rather than raising KeyError."""

    class _NoCore122Row:
        _data = {
            "id": 1,
            "ts": datetime.now(tz=UTC),
            "actor": "owner",
            "action": "x",
            "target": None,
            "note": None,
            "ip": None,
            "request_id": None,
        }

        def __getitem__(self, key):
            return self._data[key]

    entry = AuditLogEntry.from_record(_NoCore122Row())
    assert entry.metadata is None
    assert entry.result is None
    assert entry.error is None


def test_audit_log_entry_from_record_full():
    rid = uuid.uuid4()
    ts = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    row = _audit_row(
        id=42,
        ts=ts,
        actor="butler:qa",
        action="setting_change",
        target="rule:7",
        note="Changed threshold",
        ip="10.10.10.10",
        request_id=rid,
    )
    entry = AuditLogEntry.from_record(row)
    assert entry.id == 42
    assert entry.ts == ts
    assert entry.actor == "butler:qa"
    assert entry.action == "setting_change"
    assert entry.target == "rule:7"
    assert entry.note == "Changed threshold"
    assert entry.ip == "10.10.10.10"
    assert entry.request_id == rid


# ---------------------------------------------------------------------------
# audit.append() helper
# ---------------------------------------------------------------------------


async def test_append_inserts_row_and_returns_id():
    pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    pool.fetchval = AsyncMock(return_value=7)

    row_id = await append(pool, "owner", "model_priority_change")
    assert row_id == 7

    pool.fetchval.assert_awaited_once()
    call_args = pool.fetchval.call_args[0]
    assert "INSERT INTO public.audit_log" in call_args[0]
    assert "RETURNING id" in call_args[0]
    assert call_args[1] == "owner"
    assert call_args[2] == "model_priority_change"


async def test_append_binds_target_note_ip_request_id_at_positions_3_through_6():
    """target/note/ip/request_id bind to INSERT positions 3/4/5/6 in that order.

    The INSERT column list is ``(actor, action, target, note, ip, request_id,
    metadata, result, error)``, so the positional args after the SQL string
    (call_args[0]) carry actor at idx 1, action at idx 2, then target/note/ip/
    request_id at idx 3/4/5/6.  Pins the binding order against an accidental
    swap that would silently corrupt append-only rows.
    """
    rid = uuid.uuid4()
    pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    pool.fetchval = AsyncMock(return_value=11)

    await append(
        pool,
        "owner",
        "setting_change",
        target="rule:7",
        note="Changed threshold",
        ip="10.10.10.10",
        request_id=rid,
    )

    call_args = pool.fetchval.call_args[0]
    assert call_args[3] == "rule:7"
    assert call_args[4] == "Changed threshold"
    assert call_args[5] == "10.10.10.10"
    assert call_args[6] == rid


async def test_append_persists_metadata_result_error():
    """append() forwards metadata/result/error (core_122) into the INSERT.

    metadata is bound as a plain Python dict (NOT a pre-serialized JSON
    string) with no ``::jsonb`` cast — every asyncpg pool in this codebase
    registers a jsonb type codec (``register_jsonb_codec``, src/butlers/db.py)
    whose encoder does the one-and-only ``json.dumps()`` itself. Binding an
    already-serialized string here would make that encoder fire a second
    time, double-encoding the value into a jsonb-typed STRING instead of an
    OBJECT (this codebase has hit that exact regression twice before:
    bu-qki26, bu-aaacv — see tests/relationship/test_jsonb_codec.py). result
    and error are passed through as plain TEXT positional args.
    """
    pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    pool.fetchval = AsyncMock(return_value=123)

    row_id = await append(
        pool,
        "owner",
        "model_priority_change",
        metadata={"path": "/api/x", "trigger_source": "dashboard"},
        result="success",
        error=None,
    )
    assert row_id == 123

    call_args = pool.fetchval.call_args[0]
    sql = call_args[0]
    assert "metadata" in sql
    assert "result" in sql
    assert "error" in sql
    assert "$7::jsonb" not in sql
    # metadata is bound as a dict directly, not a pre-serialized JSON string.
    metadata_arg = call_args[7]
    assert isinstance(metadata_arg, dict)
    assert metadata_arg == {"path": "/api/x", "trigger_source": "dashboard"}
    assert call_args[8] == "success"
    assert call_args[9] is None


async def test_append_coerces_non_json_safe_metadata_values_to_strings():
    """UUID/datetime values nested in metadata are coerced to strings (via a
    json.dumps/loads round-trip) before binding, same as before -- only the
    OUTER representation (dict, not a JSON string) changed."""
    pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    pool.fetchval = AsyncMock(return_value=1)
    rid = uuid.uuid4()

    await append(
        pool,
        "owner",
        "model_priority_change",
        metadata={"request_id": rid},
        result="success",
    )

    call_args = pool.fetchval.call_args[0]
    metadata_arg = call_args[7]
    assert isinstance(metadata_arg, dict)
    assert metadata_arg == {"request_id": str(rid)}


async def test_append_without_new_fields_passes_nulls():
    """Omitting the core_122 fields keeps the call backward compatible.

    metadata defaults to None (→ SQL NULL, no JSON serialisation) and
    result/error default to None, so legacy callers are unaffected.
    """
    pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    pool.fetchval = AsyncMock(return_value=5)

    row_id = await append(pool, "owner", "setting_change")
    assert row_id == 5

    call_args = pool.fetchval.call_args[0]
    # metadata (idx 7), result (idx 8), error (idx 9) all None.
    assert call_args[7] is None
    assert call_args[8] is None
    assert call_args[9] is None


def test_audit_log_entry_new_fields_default_none():
    """AuditLogEntry exposes metadata/result/error, defaulting to None."""
    entry = AuditLogEntry(
        id=1,
        ts=datetime.now(tz=UTC),
        actor="owner",
        action="x",
    )
    assert entry.metadata is None
    assert entry.result is None
    assert entry.error is None

    populated = AuditLogEntry(
        id=2,
        ts=datetime.now(tz=UTC),
        actor="owner",
        action="x",
        metadata={"k": "v"},
        result="error",
        error="boom",
    )
    assert populated.metadata == {"k": "v"}
    assert populated.result == "error"
    assert populated.error == "boom"


async def test_append_adds_to_otel_counter_with_action_attribute():
    from opentelemetry import metrics
    from opentelemetry.metrics import _internal as metrics_internal
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.util._once import Once

    reader = InMemoryMetricReader()
    previous = metrics_internal._METER_PROVIDER
    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    try:
        pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
        pool.fetchval = AsyncMock(return_value=1)
        await append(pool, "owner", "test_counter_increment")

        points = [
            (m.name, dp)
            for rm in reader.get_metrics_data().resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
            for dp in m.data.data_points
        ]
    finally:
        metrics_internal._METER_PROVIDER_SET_ONCE = Once()
        metrics_internal._METER_PROVIDER = previous

    assert [(n, dict(dp.attributes), dp.value) for n, dp in points] == [
        ("butlers.audit.log.appended", {"action": "test_counter_increment"}, 1)
    ]


async def test_append_raises_audit_table_not_available_error_on_undefined_table():
    """append() MUST raise AuditTableNotAvailableError, not the raw asyncpg error."""
    pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    pool.fetchval = AsyncMock(side_effect=UndefinedTableError("relation does not exist"))

    with pytest.raises(AuditTableNotAvailableError):
        await append(pool, "owner", "some_action")


async def test_append_accepts_connection_for_atomicity():
    """append() works with an asyncpg connection, enabling same-transaction writes."""
    conn = AsyncMock()
    conn.is_in_transaction = MagicMock(return_value=False)
    conn.fetchval = AsyncMock(return_value=42)

    row_id = await append(conn, "owner", "model_priority_change")
    assert row_id == 42
    conn.fetchval.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /api/audit-log
# ---------------------------------------------------------------------------


def _make_audit_app(rows: list[dict], total: int | None = None) -> tuple:
    """Wire a FastAPI app with mocked DatabaseManager for audit log reads."""
    if total is None:
        total = len(rows)

    def _make_record(row: dict) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    mock_pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=[_make_record(r) for r in rows])

    # The read path queries the canonical public.audit_log via
    # credential_shared_pool() only (bu-j26e8 removed the legacy UNION arm).  A
    # spare switchboard-pool stub is kept wired for any non-read code paths.
    sw_pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    sw_pool.fetchval = AsyncMock(return_value=0)
    sw_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool
    mock_db.pool.return_value = sw_pool

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    return app, mock_pool, mock_db


def _sample_row(
    *,
    row_id: int = 1,
    actor: str = "owner",
    action: str = "setting_change",
    target: str | None = None,
    note: str | None = None,
    ip=None,
    request_id=None,
) -> dict:
    return {
        "id": row_id,
        "ts": datetime(2026, 5, 16, 10, 0, 0, tzinfo=UTC),
        "actor": actor,
        "action": action,
        "target": target,
        "note": note,
        "ip": ip,
        "request_id": request_id,
    }


def test_list_audit_log_returns_paginated_response():
    row = _sample_row()
    app, _, _ = _make_audit_app([row])
    client = TestClient(app)
    resp = client.get("/api/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert len(body["data"]) == 1
    assert body["data"][0]["actor"] == "owner"
    assert body["data"][0]["action"] == "setting_change"


def test_list_audit_log_default_limit():
    """Default limit is 100."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    # Trailing positional args to pool.fetch are (limit, offset); limit defaults
    # to 100 and offset to 0.
    call_args = mock_pool.fetch.call_args[0]
    assert call_args[-2] == 100
    assert call_args[-1] == 0


def test_list_audit_log_limit_clamped():
    """limit > 1000 is rejected with HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?limit=9999")
    assert resp.status_code == 422


def test_list_audit_log_filter_by_actor():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?actor=butler%3Aqa")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "actor = " in sql


def test_list_audit_log_filter_by_action():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?action=rule_delete")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "action = " in sql


def test_list_audit_log_filter_by_result():
    """?result=error filters on the result column (JARVIS audit move 6)."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?result=error")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "result = " in sql
    args = fetch_call[1:]
    assert "error" in args


def test_list_audit_log_no_result_param_no_result_condition_in_sql():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "result = " not in sql


def test_list_audit_log_empty_result_param_treated_as_no_filter():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?result=")
    assert resp.status_code == 200
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "result = " not in sql


def test_list_audit_log_filter_by_since():
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?since=2026-01-01T00:00:00")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "ts >= " in sql


def test_list_audit_log_owner_timezone_day_bounds(monkeypatch):
    """Bare audit From/To date keys span a full owner-local calendar day.

    The values are intentionally fixed rather than derived from `now()`: this
    remains deterministic under the repository's libfaketime matrix.
    """
    owner_tz = ZoneInfo("Asia/Singapore")
    monkeypatch.setattr(
        audit_module,
        "owner_zoneinfo",
        AsyncMock(return_value=owner_tz),
        raising=False,
    )
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)

    resp = client.get("/api/audit-log?from_date=2026-07-11&to_date=2026-07-11")

    assert resp.status_code == 200
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    args = fetch_call[1:]
    assert "ts >= " in sql
    assert "ts <= " in sql
    assert args[0] == datetime(2026, 7, 11, 0, 0, 0, tzinfo=owner_tz)
    assert args[1] == datetime(2026, 7, 11, 23, 59, 59, 999999, tzinfo=owner_tz)


def test_list_audit_log_iso_date_bounds_pass_through_unchanged(monkeypatch):
    """Full ISO bounds remain timestamps rather than being rebucketed by timezone."""
    monkeypatch.setattr(
        audit_module,
        "owner_zoneinfo",
        AsyncMock(return_value=ZoneInfo("Asia/Singapore")),
        raising=False,
    )
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)

    resp = client.get("/api/audit-log?from_date=2026-07-11T06:30:00&to_date=2026-07-11T08:45:00")

    assert resp.status_code == 200
    args = mock_pool.fetch.call_args[0][1:]
    assert args[0] == datetime(2026, 7, 11, 6, 30, 0)
    assert args[1] == datetime(2026, 7, 11, 8, 45, 0)


def test_list_audit_log_invalid_owner_day_bound_returns_422(monkeypatch):
    monkeypatch.setattr(
        audit_module,
        "owner_zoneinfo",
        AsyncMock(return_value=ZoneInfo("Asia/Singapore")),
        raising=False,
    )
    app, _, _ = _make_audit_app([])
    client = TestClient(app)

    resp = client.get("/api/audit-log?from_date=2026-13-40")

    assert resp.status_code == 422


def test_list_audit_log_order_ts_desc():
    """SQL contains ORDER BY ts DESC."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "ORDER BY ts DESC" in sql


def test_list_audit_log_paginates_with_sql_limit_offset():
    """Post audit-unify (bu-j26e8) reads come solely from public.audit_log, so
    pagination is a plain SQL ``LIMIT $N OFFSET $N+1`` against the canonical
    table — no in-memory merge, no over-fetch.  The last two positional args to
    pool.fetch are (limit, offset)."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?offset=50&limit=25")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "LIMIT" in sql
    assert "OFFSET" in sql
    args = fetch_call[1:]
    # Trailing positional args: limit then offset.
    assert args[-2] == 25
    assert args[-1] == 50


def test_list_audit_log_offset_reflected_in_meta():
    """offset value is returned in the pagination meta."""
    row = _sample_row()
    app, _, _ = _make_audit_app([row])
    client = TestClient(app)
    resp = client.get("/api/audit-log?offset=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["offset"] == 10


def test_list_audit_log_table_missing_returns_503():
    """UndefinedTableError on count query surfaces as HTTP 503."""
    mock_pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    mock_pool.fetchval = AsyncMock(side_effect=UndefinedTableError("relation does not exist"))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)
    resp = client.get("/api/audit-log")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/audit-log/{id}
# ---------------------------------------------------------------------------


def _make_audit_app_single(row: dict | None) -> tuple:
    """Wire a FastAPI app with mocked DatabaseManager for single-row reads."""
    mock_pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))

    def _make_record(r: dict) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: r[key])
        return m

    mock_pool.fetchrow = AsyncMock(return_value=_make_record(row) if row is not None else None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    return app, mock_pool, mock_db


def test_get_audit_log_entry_by_id_found():
    row = _sample_row(row_id=5, action="model_change")
    app, _, _ = _make_audit_app_single(row)
    client = TestClient(app)
    resp = client.get("/api/audit-log/5")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["id"] == 5
    assert body["data"]["action"] == "model_change"


def test_get_audit_log_entry_by_id_not_found():
    app, _, _ = _make_audit_app_single(None)
    client = TestClient(app)
    resp = client.get("/api/audit-log/9999")
    assert resp.status_code == 404


def test_get_audit_log_entry_table_missing_returns_503():
    """UndefinedTableError on fetchrow surfaces as HTTP 503, not 404."""
    mock_pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    mock_pool.fetchrow = AsyncMock(side_effect=UndefinedTableError("relation does not exist"))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)
    resp = client.get("/api/audit-log/1")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/audit-log?key= — credential-key filter (bu-2rdyc)
# ---------------------------------------------------------------------------
# The ?key= param normalises the input via normalize_key_param() before
# filtering on the `target` column using ix_audit_log_target_ts.
# ---------------------------------------------------------------------------


def test_filter_by_canonical_key_returns_matching_rows():
    """?key=u:google returns rows whose target equals the normalised key."""
    row = _sample_row(target="u:google")
    app, mock_pool, _ = _make_audit_app([row])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=u:google&limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["target"] == "u:google"


def test_filter_by_canonical_key_sql_uses_target_condition():
    """SQL generated for ?key= contains a target= filter condition."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?key=u:google")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " in sql


def test_filter_by_long_scope_form_normalised():
    """?key=user:google is equivalent to ?key=u:google after normalisation."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?key=user:google")
    fetch_call = mock_pool.fetch.call_args[0]
    args = fetch_call[1:]
    assert "u:google" in args


def test_unknown_key_returns_empty_page():
    """?key=u:does-not-exist returns empty PaginatedResponse with total=0."""
    app, mock_pool, _ = _make_audit_app([], total=0)
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=u:does-not-exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["has_more"] is False


def test_key_param_combined_with_all_filters():
    """?key=, ?since=, ?actor=, and ?action= all applied together."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get(
        "/api/audit-log?key=s:MY_SECRET&since=2026-01-01T00:00:00&actor=owner&action=updated"
    )
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " in sql
    assert "ts >= " in sql
    assert "actor = " in sql
    assert "action = " in sql


def test_key_param_malformed_returns_422():
    """?key= without a colon separator returns HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=invalid-no-colon")
    assert resp.status_code == 422


def test_key_param_unknown_scope_returns_422():
    """?key= with an unrecognised scope prefix returns HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=admin:foo")
    assert resp.status_code == 422


def test_no_key_param_no_target_condition_in_sql():
    """Without ?key=, the SQL must NOT contain a target= filter."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " not in sql


def test_empty_key_param_treated_as_no_filter():
    """?key= (empty string) is treated as if the parameter was not provided."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=")
    assert resp.status_code == 200
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " not in sql


def test_whitespace_key_param_treated_as_no_filter():
    """?key=   (whitespace-only) is treated as if the parameter was not provided."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=   ")
    assert resp.status_code == 200
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "target = " not in sql


# ---------------------------------------------------------------------------
# Regression: audit-write callsite → ?key= filter round-trip (bu-h6x8q)
#
# Validates that a target written via normalize_credential_key() is found by
# the ?key= filter.  The write side uses the same helper that production
# callsites in secrets_v2.py and oauth.py use; the read side goes through the
# full list_audit_log() handler with ?key= normalisation.
# ---------------------------------------------------------------------------


async def test_audit_write_target_found_by_key_filter():
    """Target written via normalize_credential_key() is returned by ?key= filter.

    Simulates the full round-trip:
    1. A write callsite produces target = normalize_credential_key("user", "google")
    2. The row is stored with that canonical target ("u:google").
    3. GET /api/audit-log?key=u:google returns the row.
    4. GET /api/audit-log?key=user:google (long-scope form) also returns the row.
    """
    # Step 1: derive the canonical target exactly as a write callsite would.
    canonical_target = normalize_credential_key("user", "google")
    assert canonical_target == "u:google"  # belt-and-suspenders: confirm the contract

    # Step 2: seed the mock DB with a row whose target equals the canonical key.
    row = _sample_row(target=canonical_target, action="rotated")
    app, mock_pool, _ = _make_audit_app([row])
    client = TestClient(app)

    # Step 3: query with canonical short-prefix form — must match.
    resp = client.get("/api/audit-log?key=u:google")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["target"] == "u:google"
    # Verify the SQL filter argument was the normalised key.
    fetch_args = mock_pool.fetch.call_args[0]
    assert "u:google" in fetch_args

    # Step 4: query with long-scope form — normalised to same value before SQL.
    resp_long = client.get("/api/audit-log?key=user:google")
    assert resp_long.status_code == 200
    fetch_args_long = mock_pool.fetch.call_args[0]
    assert "u:google" in fetch_args_long  # normalised, not "user:google"


async def test_audit_write_target_system_scope_found_by_key_filter():
    """System-scope target written via normalize_credential_key() found by ?key=."""
    canonical_target = normalize_credential_key("system", "BUTLER_TELEGRAM_TOKEN")
    assert canonical_target == "s:BUTLER_TELEGRAM_TOKEN"

    row = _sample_row(target=canonical_target, action="set")
    app, mock_pool, _ = _make_audit_app([row])
    client = TestClient(app)

    resp = client.get("/api/audit-log?key=s:BUTLER_TELEGRAM_TOKEN")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["target"] == "s:BUTLER_TELEGRAM_TOKEN"
    fetch_args = mock_pool.fetch.call_args[0]
    assert "s:BUTLER_TELEGRAM_TOKEN" in fetch_args


def test_non_normalised_target_not_returned_for_canonical_key_filter():
    """A row stored with a raw un-normalised target is NOT matched by ?key= with canonical form.

    This regression test documents the invariant: if a callsite bypasses
    normalize_credential_key() and writes "google" instead of "u:google",
    the ?key=u:google filter will not find it.  All production callsites
    MUST use normalize_credential_key() to avoid silent filter misses.
    """
    # Row written without normalisation (simulating a defective callsite).
    raw_target_row = _sample_row(target="google", action="rotated")
    app, mock_pool, _ = _make_audit_app(
        [raw_target_row],
        # count mock returns 0 — the WHERE target='u:google' clause excludes the raw row
        total=0,
    )
    client = TestClient(app)

    # The ?key= filter normalises "user:google" → "u:google" and passes "u:google" to SQL.
    # The mock pool is set up to return total=0 rows, confirming the raw "google" row
    # is not returned.
    resp = client.get("/api/audit-log?key=u:google")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/audit-log?kind=privileged — consequence allowlist
# ---------------------------------------------------------------------------
# kind=privileged is an explicit consequence allowlist, rather than an
# ever-growing denylist of known machine-cadence shapes. All explicit errors
# remain visible even if their action family is new.
# ---------------------------------------------------------------------------


def test_kind_privileged_sql_is_consequence_allowlist():
    """The privileged predicate explicitly selects the allowed action families."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?kind=privileged")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    for action_family in (
        "approval.%",
        "approvals.policy",
        "model.%",
        "permission.%",
        "data.%",
        "webhook.%",
        "spend.%",
        "runtime_config_patch",
        "PUT /api/butlers/%/model-overrides",
    ):
        assert action_family in sql
    assert "result = 'error'" in sql
    assert "NOT LIKE" not in sql


def test_kind_privileged_returns_200():
    """kind=privileged is a valid parameter and returns HTTP 200."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=privileged")
    assert resp.status_code == 200


def test_kind_unknown_returns_422():
    """An unrecognised kind value is rejected with HTTP 422."""
    app, _, _ = _make_audit_app([])
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=foobar")
    assert resp.status_code == 422


def test_kind_absent_does_not_add_noise_filters():
    """Without ?kind=, the full-history request has no allowlist predicate."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "approval.%" not in sql
    assert "result = 'error'" not in sql


def test_kind_privileged_combined_with_limit():
    """kind=privileged works alongside limit; trailing args remain (limit, offset)."""
    app, mock_pool, _ = _make_audit_app([])
    client = TestClient(app)
    client.get("/api/audit-log?kind=privileged&limit=15")
    fetch_call = mock_pool.fetch.call_args[0]
    sql = fetch_call[0]
    assert "approval.%" in sql
    args = fetch_call[1:]
    assert args[-2] == 15  # limit
    assert args[-1] == 0  # offset


def test_kind_privileged_empty_state_returns_empty_page():
    """kind=privileged with no matching rows returns an empty data list, not an error."""
    app, _, _ = _make_audit_app([], total=0)
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=privileged&limit=15")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_kind_privileged_returns_mutation_rows():
    """kind=privileged returns mutation rows (permission.set, data.export, webhook.*)."""
    mutation_rows = [
        _sample_row(row_id=1, action="permission.set"),
        _sample_row(row_id=2, action="data.export"),
        _sample_row(row_id=3, action="webhook.create"),
        _sample_row(row_id=4, action="runtime_config_patch"),
        _sample_row(row_id=5, action="PUT /api/butlers/qa/model-overrides"),
    ]
    app, _, _ = _make_audit_app(mutation_rows, total=5)
    client = TestClient(app)
    resp = client.get("/api/audit-log?kind=privileged&limit=15")
    assert resp.status_code == 200
    body = resp.json()
    actions = [e["action"] for e in body["data"]]
    assert "permission.set" in actions
    assert "data.export" in actions
    assert "webhook.create" in actions
    assert "runtime_config_patch" in actions
    assert "PUT /api/butlers/qa/model-overrides" in actions


# ---------------------------------------------------------------------------
# Credential-target free text is withheld on read (bu-ove06)
#
# public.audit_log rows whose target names a credential (u:/s:/c:) carry the
# same provider free text the secrets endpoints stopped publishing (bu-nz4sn,
# bu-rh8z5, bu-m9s61): _write_credential_audit persists a "Probe failed: ..."
# note and the raw failure message in `error`. The secrets audit endpoint's own
# meta.deep_link points at /audit-log?key=<canonical-key>, so leaving that text
# on this surface would signpost the path around the fix. Withholding is
# enforced on the AuditLogEntry model itself so every reader of the table —
# GET /api/audit-log, GET /api/audit-log/{id}, and the Issues occurrences
# drill-down — is covered by one chokepoint.
# ---------------------------------------------------------------------------


def _credential_row(
    *,
    row_id: int = 1,
    target: str = "u:google",
    note: str | None = None,
    error: str | None = None,
    metadata: dict | None = None,
    result: str | None = "error",
) -> dict:
    return {
        "id": row_id,
        "ts": datetime(2026, 8, 22, 10, 0, 0, tzinfo=UTC),
        "actor": "owner",
        "action": "failed",
        "target": target,
        "note": note,
        "ip": None,
        "request_id": None,
        "metadata": metadata,
        "result": result,
        "error": error,
    }


def test_list_audit_log_withholds_credential_row_free_text():
    """GET /api/audit-log does not publish note/error for a credential target."""
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    app, _, _ = _make_audit_app(
        [_credential_row(note=sentinel, error=sentinel, metadata={"detail": sentinel})]
    )
    client = TestClient(app)
    resp = client.get("/api/audit-log?key=u:google")

    assert resp.status_code == 200
    assert sentinel not in resp.text, "audit list response published withheld free text"
    entry = resp.json()["data"][0]
    assert entry["note"] is None
    assert entry["error"] is None
    assert entry["metadata"] is None
    assert entry["redacted"] is True
    # Non-content columns still identify the row for an operator.
    assert entry["target"] == "u:google"
    assert entry["action"] == "failed"
    assert entry["result"] == "error"


def test_get_audit_log_entry_withholds_credential_row_free_text():
    """GET /api/audit-log/{id} does not publish note/error for a credential target."""
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    app, _, _ = _make_audit_app_single(
        _credential_row(
            row_id=5,
            target="s:BUTLER_TELEGRAM_TOKEN",
            note=sentinel,
            error=sentinel,
            metadata={"detail": sentinel},
        )
    )
    client = TestClient(app)
    resp = client.get("/api/audit-log/5")

    assert resp.status_code == 200
    assert sentinel not in resp.text, "audit detail response published withheld free text"
    entry = resp.json()["data"]
    assert entry["note"] is None
    assert entry["error"] is None
    assert entry["metadata"] is None
    assert entry["redacted"] is True


@pytest.mark.parametrize(
    "target",
    ["u:google", "s:BUTLER_TELEGRAM_TOKEN", "c:claude", "user:google", "system:X", "cli:claude"],
)
def test_audit_log_entry_withholds_every_credential_namespace(target):
    """Every accepted credential-key spelling is withheld, not just the short form."""
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    entry = AuditLogEntry.from_record(
        _audit_row(target=target, note=sentinel, error=sentinel, metadata={"detail": sentinel})
    )
    assert entry.note is None
    assert entry.error is None
    assert entry.metadata is None
    assert entry.redacted is True


def test_audit_log_entry_cannot_be_constructed_with_credential_free_text():
    """The model itself is the chokepoint — direct construction is withheld too.

    A future reader that builds AuditLogEntry without going through
    from_record must not be able to reintroduce the leak.
    """
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    entry = AuditLogEntry(
        id=1,
        ts=datetime(2026, 8, 22, 10, 0, 0, tzinfo=UTC),
        actor="owner",
        action="failed",
        target="u:google",
        note=sentinel,
        error=sentinel,
        metadata={"detail": sentinel},
        result="error",
    )
    assert entry.note is None
    assert entry.error is None
    assert entry.metadata is None
    assert entry.redacted is True


def test_audit_log_keeps_free_text_for_non_credential_rows():
    """Non-credential operator rows keep their diagnostics — this is not a blanket gag."""
    app, _, _ = _make_audit_app(
        [
            _credential_row(
                row_id=9,
                target="butler:qa",
                note="Changed threshold",
                error="boom",
                metadata={"path": "/api/x"},
            )
        ]
    )
    client = TestClient(app)
    resp = client.get("/api/audit-log")

    entry = resp.json()["data"][0]
    assert entry["note"] == "Changed threshold"
    assert entry["error"] == "boom"
    assert entry["metadata"] == {"path": "/api/x"}
    assert entry["redacted"] is False


def test_audit_log_credential_row_without_free_text_is_not_marked_redacted():
    """`redacted` reports a real withholding, never a decorative flag."""
    entry = AuditLogEntry.from_record(
        _audit_row(target="u:google", note=None, error=None, metadata=None)
    )
    assert entry.redacted is False


# Real migrated append-only refusal; the unit owner excludes these DB instances.
@pytest.mark.integration
@pytest.mark.db
@pytest.mark.parametrize("role", _RUNTIME_ROLES)
def test_runtime_writer_and_append_only_survive_bootstrap(audit_database, role):
    url, _ = audit_database
    engine = create_engine(url)
    marker = str(uuid.uuid4())
    try:
        with engine.begin() as connection:
            # Role comes exclusively from this closed test matrix, not caller data.
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert connection.execute(text("SELECT current_user")).scalar_one() == role
            assert (
                connection.execute(
                    text(
                        "SELECT pg_has_role(session_user,'restore_drill_executor_audit_writer','SET')"
                    )
                ).scalar_one()
                is False
            )
            row = connection.execute(
                text("""
                INSERT INTO public.audit_log(actor, action, target)
                VALUES ('logical-owner', 'writer-boundary', :marker)
                RETURNING id, writer_role
            """),
                {"marker": marker},
            ).one()
            assert row.writer_role == role
        # An independent acquisition proves commit, not this transaction's view.
        with engine.connect() as observer:
            assert (
                observer.execute(
                    text("SELECT writer_role FROM public.audit_log WHERE id=:id"), {"id": row.id}
                ).scalar_one()
                == role
            )
        for sql in (
            "UPDATE public.audit_log SET actor='forged' WHERE id=:id",
            "DELETE FROM public.audit_log WHERE id=:id",
            "TRUNCATE TABLE public.audit_log",
            "INSERT INTO public.audit_log(actor,action,writer_role) "
            "VALUES ('logical-owner','forged','restore_drill_writer')",
            "INSERT INTO public.audit_log(actor,action,writer_role) "
            "VALUES ('logical-owner','forged','restore_drill_executor_audit_writer')",
        ):
            with pytest.raises(DBAPIError) as refused, engine.begin() as connection:
                connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                connection.execute(text(sql), {"id": row.id})
            assert refused.value.orig.pgcode == "42501"
        with engine.connect() as observer:
            after = observer.execute(
                text("SELECT actor,writer_role FROM public.audit_log WHERE id=:id"), {"id": row.id}
            ).one()
            assert tuple(after) == ("logical-owner", role)
            assert (
                observer.execute(
                    text("SELECT count(*) FROM public.audit_log WHERE action='forged'")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


class _AuditConnection:
    """Software protocol fixture; native cases below prove SQL durability."""

    def __init__(self):
        self.depth = 0
        self.commit_error = None
        self.probe_error = None
        self.inserts = 0
        self.commits = 0
        self.rollbacks = 0

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self, **kwargs):
        connection = self

        class Transaction:
            async def start(self):
                connection.depth += 1

            async def commit(self):
                if connection.commit_error is not None:
                    raise connection.commit_error
                connection.depth -= 1
                connection.commits += 1

            async def rollback(self):
                connection.depth -= 1
                connection.rollbacks += 1

            async def __aenter__(self):
                await self.start()
                return self

            async def __aexit__(self, kind, value, traceback):
                if kind is None:
                    await self.commit()
                else:
                    await self.rollback()

        return Transaction()

    async def fetchval(self, sql, *args):
        self.inserts += 1
        return self.inserts

    async def execute(self, sql, *args):
        assert sql == "SELECT 1"
        if self.probe_error is not None:
            raise self.probe_error
        return "SELECT 1"


async def test_append_counter_waits_for_owner_and_discards_rollback(monkeypatch):
    from butlers.core import audit
    from butlers.core.audit_transaction import audit_transaction

    connection = _AuditConnection()
    recorded = []
    monkeypatch.setattr(audit, "_record_appended", recorded.append)
    # A successful implicit transaction remains the positive companion.
    assert (
        await audit.append(
            connection, "owner", "request-derived-private", metric_action="committed"
        )
        == 1
    )
    assert recorded == ["committed"]
    recorded.clear()
    with pytest.raises(ValueError, match="rollback"):
        async with audit_transaction(connection):
            assert await audit.append(connection, "owner", "pending") == 2
            assert recorded == [], "uncommitted row must not increment the counter"
            raise ValueError("rollback")
    assert connection.rollbacks == 1
    assert recorded == []


@pytest.fixture
def audit_counter(monkeypatch):
    """An isolated real OTel reader; no process-global provider replacement."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from butlers.core import audit

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    counter = provider.get_meter("butlers").create_counter("butlers.audit.log.appended")
    monkeypatch.setattr(
        audit, "_record_appended", lambda action: counter.add(1, {"action": action})
    )

    def values():
        data = reader.get_metrics_data()
        return (
            {
                point.attributes["action"]: point.value
                for resource in data.resource_metrics
                if data is not None
                for scope in resource.scope_metrics
                for metric in scope.metrics
                for point in metric.data.data_points
            }
            if data is not None
            else {}
        )

    try:
        yield values
    finally:
        provider.shutdown()


async def test_audit_scope_refuses_unmanaged_or_inherited_owner_before_insert(monkeypatch):
    from types import SimpleNamespace

    from butlers.core import audit
    from butlers.core.audit_transaction import AuditTransactionError, audit_transaction

    for state in (None, lambda: None, AsyncMock(return_value=False)):
        malformed = SimpleNamespace(is_in_transaction=state, fetchval=AsyncMock())
        with pytest.raises(AuditTransactionError):
            await audit.append(malformed, "owner", "invalid-state")
        malformed.fetchval.assert_not_awaited()
    connection = _AuditConnection()
    recorded = []
    monkeypatch.setattr(audit, "_record_appended", recorded.append)
    async with connection.transaction():
        with pytest.raises(AuditTransactionError):
            await audit.append(connection, "owner", "unmanaged")
        with pytest.raises(AuditTransactionError):
            async with audit_transaction(connection):
                pytest.fail("unmanaged outer transaction was accepted")
    async with audit_transaction(connection):

        async def inherited():
            with pytest.raises(AuditTransactionError):
                await audit.append(connection, "owner", "inherited")
            assert connection.inserts == 0
            with pytest.raises(AuditTransactionError):
                async with audit_transaction(connection):
                    pytest.fail("inherited owner was accepted")

        await asyncio.create_task(inherited())
        assert connection.inserts == 0
        await audit.append(connection, "owner", "request-path", metric_action="owned")
        assert recorded == []
    assert recorded == ["owned"]
    # Same connection after release gets a fresh implicit transaction, not a sealed frame.
    await audit.append(connection, "owner", "reused")
    assert recorded == ["owned", "reused"]


async def test_audit_scope_probe_commit_cancellation_and_recorder_failure(monkeypatch):
    from butlers.core import audit
    from butlers.core.audit_transaction import (
        AuditMetricRecordingError,
        _frames,
        audit_transaction,
    )

    for stage in ("body", "probe", "commit", "recorder"):
        connection = _AuditConnection()
        recorded = []

        def record(action, *, stage=stage, recorded=recorded):
            if stage == "recorder":
                raise ValueError("synthetic private recorder error")
            recorded.append(action)

        monkeypatch.setattr(audit, "_record_appended", record)
        expected = asyncio.CancelledError if stage == "body" else RuntimeError
        if stage == "probe":
            connection.probe_error = RuntimeError("synthetic aborted transaction")
        if stage == "commit":
            connection.commit_error = RuntimeError("synthetic ambiguous commit")
        if stage == "recorder":
            expected = AuditMetricRecordingError
        with pytest.raises(expected) as refused:
            async with audit_transaction(connection):
                await audit.append(connection, "owner", "pending")
                assert recorded == []
                if stage == "body":
                    raise asyncio.CancelledError
        assert recorded == []
        assert _frames.get() == ()
        assert connection.commits == (1 if stage == "recorder" else 0)
        assert connection.rollbacks == (1 if stage in {"body", "probe"} else 0)
        if stage == "recorder":
            assert str(refused.value) == "Committed audit metric recording failed"
        # No closed/inherited frame survives even an ambiguous commit. Fixture cleanup
        # models pool disposal here; this does not assert SQL rollback of an unknown commit.
        connection.depth = 0
        connection.commit_error = connection.probe_error = None
        monkeypatch.setattr(audit, "_record_appended", recorded.append)
        async with audit_transaction(connection):
            await audit.append(connection, "owner", "next")
        assert recorded == ["next"]
    # An implicit successful INSERT is committed too; recorder errors remain
    # content-blind and must never trigger a SQL retry or rollback.
    implicit = _AuditConnection()

    def broken_recorder(action):
        raise ValueError("synthetic private recorder error")

    monkeypatch.setattr(audit, "_record_appended", broken_recorder)
    with pytest.raises(AuditMetricRecordingError) as implicit_failure:
        await audit.append(implicit, "owner", "implicit-recording-failure")
    assert str(implicit_failure.value) == "Committed audit metric recording failed"
    assert implicit.inserts == 1
    assert implicit.rollbacks == 0


@pytest.mark.integration
@pytest.mark.db
async def test_audit_commit_metrics_and_real_permission_owner(db, audit_counter, monkeypatch):
    from butlers.api.routers import permissions
    from butlers.core import audit
    from butlers.core.audit_transaction import audit_transaction

    pool = await db("core", "switchboard")
    async with pool.acquire() as connection:
        async with audit_transaction(connection):
            row_id = await audit.append(connection, "owner", "committed", result="error")
            assert type(row_id) is int
            assert audit_counter() == {}
            async with pool.acquire() as observer:
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM public.audit_log WHERE id=$1", row_id
                    )
                    == 0
                )
        async with pool.acquire() as observer:
            assert (
                await observer.fetchval("SELECT result FROM public.audit_log WHERE id=$1", row_id)
                == "error"
            )
    assert audit_counter() == {"committed": 1}
    await audit.append(
        pool, "owner", "request-derived-private-path", metric_action="POST /api/route/{id}"
    )
    async with pool.acquire() as connection:
        await audit.append(connection, "owner", "implicit-connection")
    assert audit_counter() == {"committed": 1, "POST /api/route/{id}": 1, "implicit-connection": 1}
    await pool.execute(
        "INSERT INTO butler_registry(name,endpoint_url) VALUES('chronicler','http://synthetic.test') ON CONFLICT(name) DO NOTHING"
    )
    manager = MagicMock(spec=DatabaseManager)
    manager.pool.return_value = pool
    monkeypatch.setattr(permissions, "dispatch_event", lambda *args: None)
    response = await permissions.set_permission(
        "chronicler",
        "spawn",
        permissions.PermissionUpdate(granted=True, reason="audit commit proof"),
        db=manager,
    )
    assert response.data.granted is True
    async with pool.acquire() as observer:
        assert (
            await observer.fetchval(
                "SELECT granted FROM public.permissions WHERE butler='chronicler' AND permission='spawn'"
            )
            is True
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM public.audit_log WHERE action='permission.set'"
            )
            == 1
        )
    assert audit_counter()["permission.set"] == 1


@pytest.mark.integration
@pytest.mark.db
@pytest.mark.parametrize("outer_rollback", [False, True])
async def test_audit_nested_savepoints_merge_or_discard(db, audit_counter, outer_rollback):
    from butlers.core import audit
    from butlers.core.audit_transaction import audit_transaction

    pool = await db("core")
    ids = []

    async def write():
        async with pool.acquire() as connection, audit_transaction(connection):
            ids.append(await audit.append(connection, "owner", "outer"))
            async with audit_transaction(connection):
                ids.append(await audit.append(connection, "owner", "released"))
                assert audit_counter() == {}
            with pytest.raises(ValueError, match="child"):
                async with audit_transaction(connection):
                    ids.append(await audit.append(connection, "owner", "discarded"))
                    raise ValueError("child")
            assert audit_counter() == {}
            if outer_rollback:
                raise ValueError("outer")

    if outer_rollback:
        with pytest.raises(ValueError, match="outer"):
            await write()
    else:
        await write()
    async with pool.acquire() as observer:
        rows = await observer.fetch(
            "SELECT action FROM public.audit_log WHERE id=ANY($1::bigint[]) ORDER BY id", ids
        )
    expected = [] if outer_rollback else ["outer", "released"]
    assert [row["action"] for row in rows] == expected
    assert audit_counter() == ({name: 1 for name in expected})


@pytest.mark.integration
@pytest.mark.db
async def test_audit_native_failed_transaction_and_missing_storage(db, audit_counter):
    import asyncpg

    from butlers.core import audit
    from butlers.core.audit_transaction import AuditTransactionError, audit_transaction

    pool = await db("core")
    async with pool.acquire() as connection:
        async with connection.transaction():
            with pytest.raises(AuditTransactionError):
                await audit.append(connection, "owner", "unmanaged")
        with pytest.raises(asyncpg.InFailedSQLTransactionError):
            async with audit_transaction(connection):
                row_id = await audit.append(connection, "owner", "aborted")
                try:
                    await connection.execute("SELECT 1/0")
                except asyncpg.DivisionByZeroError:
                    pass
        async with pool.acquire() as observer:
            assert (
                await observer.fetchval("SELECT count(*) FROM public.audit_log WHERE id=$1", row_id)
                == 0
            )
        # A deferred constraint fails at the genuine COMMIT, after SELECT 1
        # succeeds. The scope must not infer that a healthy probe is commit proof.
        await connection.execute(
            "CREATE TEMP TABLE audit_commit_failure_test ("
            "id integer PRIMARY KEY, parent integer REFERENCES audit_commit_failure_test(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )
        try:
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                async with audit_transaction(connection):
                    commit_row_id = await audit.append(connection, "owner", "commit-failed")
                    await connection.execute("INSERT INTO audit_commit_failure_test VALUES(1, 2)")
            async with pool.acquire() as observer:
                assert (
                    await observer.fetchval(
                        "SELECT count(*) FROM public.audit_log WHERE id=$1", commit_row_id
                    )
                    == 0
                )
            assert audit_counter() == {}
        finally:
            await connection.execute("DROP TABLE audit_commit_failure_test")
        with pytest.raises(audit.AuditTableNotAvailableError):
            async with audit_transaction(connection):
                await connection.execute(
                    "ALTER TABLE public.audit_log RENAME TO unavailable_audit_test"
                )
                await audit.append(connection, "owner", "unavailable")
        assert (
            await connection.fetchval("SELECT to_regclass('public.audit_log') IS NOT NULL") is True
        )
    assert audit_counter() == {}


@pytest.mark.integration
@pytest.mark.db
async def test_audit_concurrent_owners_and_cancelled_task(db, audit_counter):
    from butlers.core import audit
    from butlers.core.audit_transaction import AuditTransactionError, audit_transaction

    pool = await db("core", max_size=3)
    committed = asyncio.Event()
    pending = asyncio.Event()
    release = asyncio.Event()
    ids = {}

    async def writer(action, ready):
        async with pool.acquire() as connection, audit_transaction(connection):
            ids[action] = await audit.append(connection, "owner", action)

            async def inherited():
                with pytest.raises(AuditTransactionError):
                    await audit.append(connection, "owner", "inherited")

            await asyncio.create_task(inherited())
            ready.set()
            await release.wait()

    tasks = [
        asyncio.create_task(writer("commit", committed)),
        asyncio.create_task(writer("cancel", pending)),
    ]
    try:
        await asyncio.wait_for(asyncio.gather(committed.wait(), pending.wait()), timeout=10)
        assert audit_counter() == {}
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        release.set()
        await tasks[0]
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with pool.acquire() as observer:
        rows = await observer.fetch(
            "SELECT action FROM public.audit_log WHERE id=ANY($1::bigint[])", list(ids.values())
        )
    assert [row["action"] for row in rows] == ["commit"]
    assert audit_counter() == {"commit": 1}


@pytest.mark.integration
@pytest.mark.db
async def test_audit_stepup_owning_transaction_waits_for_outer_commit(db, audit_counter):
    from butlers.core import audit
    from butlers.core.audit_transaction import audit_transaction
    from butlers.modules.approvals.executor import _approval_write_transaction

    pool = await db("core")
    async with _approval_write_transaction(pool, audit_owned=True) as connection:
        row_id = await audit.append(connection, "owner", "stepup-owner")
        async with audit_transaction(connection):
            await audit.append(connection, "owner", "stepup-savepoint")
        assert audit_counter() == {}
        async with pool.acquire() as observer:
            assert (
                await observer.fetchval("SELECT count(*) FROM public.audit_log WHERE id=$1", row_id)
                == 0
            )
    assert audit_counter() == {"stepup-owner": 1, "stepup-savepoint": 1}
    async with pool.acquire() as observer:
        assert (
            await observer.fetchval("SELECT count(*) FROM public.audit_log WHERE id=$1", row_id)
            == 1
        )
