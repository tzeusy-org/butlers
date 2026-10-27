"""Software protocol/transaction controls; real casts live in the migrated native owner."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from scripts import repair_attention_ledger_metadata as repair

pytestmark = pytest.mark.unit
ID1, ID2 = UUID(int=1), UUID(int=2)
CANARY = "PRIVATE_METADATA_SESSION_RECIPIENT_CANARY"


def canonical(text):
    return json.dumps(json.loads(text), sort_keys=True) if text is not None else None


def fingerprint(text):
    return hashlib.sha256((canonical(text) if text is not None else "SQLNULL").encode()).hexdigest()


class Ledger:
    """IO recorder interprets emitted SQL; transaction rollback is independently observable."""

    def __init__(self):
        self.data = {
            ID1: json.dumps(json.dumps({"session_id": CANARY, "x": 1})),
            ID2: json.dumps('{"nested":{"text":"quoted"},"x":2}'),
        }
        self.other = {ID1: "a" * 64, ID2: "b" * 64}
        self.actor = repair.ROLE
        self.fault = None
        self.writes = 0
        self.statements = []
        self.mode = None

    @asynccontextmanager
    async def acquire(self, **kwargs):
        assert kwargs == {"timeout": 30}
        yield self

    @asynccontextmanager
    async def transaction(self, **kwargs):
        before = copy.deepcopy(self.data)
        self.mode = kwargs
        try:
            yield
        except BaseException:
            self.data = before
            raise
        finally:
            self.mode = None

    async def fetchrow(self, sql, **kwargs):
        assert "current_database()" in sql and kwargs == {"timeout": 30}
        return {
            "db": "private_target",
            "login": "ordinary_login",
            "actor": self.actor,
            "address": "127.0.0.1",
            "port": 5432,
            "db_oid": "123",
            "table_oid": "456",
            "kind": "r",
            "elevated": False,
        }

    async def fetch(self, sql, ids, **kwargs):
        assert "WHERE id = ANY($1::uuid[]) ORDER BY id" in sql
        assert kwargs == {"timeout": 30}
        if "FOR UPDATE" in sql:
            assert self.mode == {"isolation": "serializable"}
        else:
            assert self.mode == {"isolation": "repeatable_read", "readonly": True}
        result = []
        for row_id in ids:
            if row_id not in self.data:
                continue
            text = canonical(self.data[row_id])
            obj = json.loads(text) if text is not None else None
            kind = (
                None
                if text is None
                else (
                    "object"
                    if isinstance(obj, dict)
                    else "string"
                    if isinstance(obj, str)
                    else "array"
                )
            )
            result.append(
                {
                    "id": row_id,
                    "kind": kind,
                    "text": text,
                    "fingerprint": fingerprint(text),
                    "other": self.other[row_id],
                }
            )
        return result

    async def fetchval(self, sql, *args, **kwargs):
        assert kwargs == {"timeout": 30}
        if sql == repair.JSON_HASH:
            return fingerprint(args[0])
        assert "metadata IS NOT DISTINCT FROM $2::text::jsonb" in sql
        assert "metadata->>'session_id'" in sql
        return canonical(self.data[args[0]]) == canonical(args[1])

    async def execute(self, sql, row_id, text, **kwargs):
        assert self.mode == {"isolation": "serializable"}
        assert sql == repair.UPDATE and "$2::text::jsonb" in sql and kwargs == {"timeout": 30}
        self.statements.append(sql)
        self.writes += 1
        self.data[row_id] = canonical(text)
        if self.writes == 1 and self.fault is not None:
            raise self.fault
        return "UPDATE 1"


def test_strict_classification_and_private_plan(tmp_path):
    precise = '{"n":0.12345678901234567890123456789,"nested":{"s":"a\\nb"}}'
    assert repair.classify("string", json.dumps(precise)) == ("repair", precise)
    assert repair.classify("object", "{}") == ("object", None)
    assert repair.classify(None, None) == ("sql_null", None)
    bad = [
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
        "[]",
        "null",
        "1",
        '"another encoded layer"',
        "{broken",
        "[" * 33 + "]" * 33,
    ]
    for inner in bad:
        assert repair.classify("string", json.dumps(inner)) == ("refused", None)
    assert repair.classify("object", None) == ("refused", None)
    assert repair.classify("array", "[]") == ("refused", None)
    assert repair.classify("string", json.dumps("x" * repair.MAX_TEXT)) == ("refused", None)
    good = {
        "version": 1,
        "source": "c" * 64,
        "target": "d" * 64,
        "rows": [
            {
                "id": str(ID1),
                "status": "repair",
                "before": "e" * 64,
                "after": "f" * 64,
                "other": "a" * 64,
            }
        ],
    }
    path = tmp_path / "plan.json"
    repair.write_private(path, good)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert repair.read_plan(path) == good
    with pytest.raises(repair.Refused):
        repair.read_plan(path, expected_sha256="0" * 64)
    with pytest.raises(repair.Refused):
        repair.write_private(path, good)
    for raw in (
        '{"version":1,"version":1}',
        json.dumps({**good, "extra": CANARY}),
        json.dumps({**good, "rows": good["rows"] * 2}),
        json.dumps({**good, "version": True}),
        json.dumps(good)[:-1],
        "x" * (repair.MAX_FILE + 1),
    ):
        path.write_text(raw)
        with pytest.raises(repair.Refused):
            repair.read_plan(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for invalid in (link, fifo, tmp_path):
        with pytest.raises(repair.Refused):
            repair.read_plan(invalid)
    path.write_text(json.dumps(good))
    path.chmod(0o644)
    with pytest.raises(repair.Refused):
        repair.read_plan(path)
    for ids in ([], [ID1, ID1], ["not-a-uuid"], [UUID(int=i) for i in range(129)]):
        with pytest.raises(repair.Refused):
            repair.selection(ids)


async def test_transactional_repair_stale_refusal_and_idempotence(tmp_path):
    ledger = Ledger()
    plan = await repair.plan_repair(ledger, [ID2, ID1], target="explicit_target_hash")
    original = copy.deepcopy(ledger.data)
    assert CANARY not in json.dumps(plan) and "private_target" not in json.dumps(plan)
    assert ledger.statements == []
    for name, fault in (("error", RuntimeError(CANARY)), ("cancel", asyncio.CancelledError())):
        ledger.fault, ledger.writes = fault, 0
        with pytest.raises(type(fault)):
            await repair.apply_repair(
                ledger, plan, target="explicit_target_hash", beforeimages=tmp_path / name
            )
        assert ledger.data == original
        assert json.loads((tmp_path / name).read_text())["beforeimages"][0]["text"] == canonical(
            original[ID1]
        )
    ledger.fault, ledger.writes = None, 0
    for drift in ("metadata", "other", "missing", "target", "source"):
        ledger.data, ledger.other = copy.deepcopy(original), {ID1: "a" * 64, ID2: "b" * 64}
        changed = copy.deepcopy(plan)
        if drift == "metadata":
            ledger.data[ID1] = json.dumps(json.dumps({"changed": True}))
        elif drift == "other":
            ledger.other[ID1] = "f" * 64
        elif drift == "missing":
            del ledger.data[ID2]
        else:
            changed[drift] = "0" * 64
        before = copy.deepcopy(ledger.data)
        with pytest.raises(repair.Refused):
            await repair.apply_repair(
                ledger, changed, target="explicit_target_hash", beforeimages=tmp_path / drift
            )
        assert ledger.data == before and ledger.writes == 0
    ledger.data, ledger.other = original, {ID1: "a" * 64, ID2: "b" * 64}
    result, texts = await repair.apply_repair(
        ledger, plan, target="explicit_target_hash", beforeimages=tmp_path / "before"
    )
    assert result == {"updated": 2, "already_applied": 0, "unchanged": 0}
    await repair.verify_repair(ledger, plan, target="explicit_target_hash", expected_texts=texts)
    result, texts = await repair.apply_repair(
        ledger, plan, target="explicit_target_hash", beforeimages=tmp_path / "repeat"
    )
    assert result == {"updated": 0, "already_applied": 2, "unchanged": 0}
    await repair.verify_repair(ledger, plan, target="explicit_target_hash", expected_texts=texts)
    ledger.data[ID1] = '{"different":true}'
    with pytest.raises(repair.Refused):
        await repair.verify_repair(
            ledger, plan, target="explicit_target_hash", expected_texts=texts
        )
    ledger = Ledger()
    ledger.data[ID2] = json.dumps("[]")
    refused = await repair.plan_repair(ledger, [ID1, ID2], target="explicit_target_hash")
    with pytest.raises(repair.Refused):
        await repair.apply_repair(
            ledger, refused, target="explicit_target_hash", beforeimages=tmp_path / "refused"
        )
    assert ledger.writes == 0
    ledger = Ledger()
    ledger.data = {ID1: '{"legitimate":true}', ID2: None}
    unchanged = await repair.plan_repair(ledger, [ID1, ID2], target="explicit_target_hash")
    result, texts = await repair.apply_repair(
        ledger, unchanged, target="explicit_target_hash", beforeimages=tmp_path / "unchanged"
    )
    assert result == {"updated": 0, "already_applied": 0, "unchanged": 2}
    await repair.verify_repair(
        ledger, unchanged, target="explicit_target_hash", expected_texts=texts
    )
    ledger.actor = "unexpected_role"
    with pytest.raises(repair.Refused):
        await repair.plan_repair(ledger, [ID1], target="explicit_target_hash")


async def test_connection_role_cleanup_and_content_blind_errors(monkeypatch, capsys, caplog):
    dsn = f"postgresql://ordinary:{CANARY}@localhost:5432/private_db?sslmode=disable"
    options, target = repair.connection_options(dsn)
    assert options["db_name"] == "private_db" and CANARY not in target
    for invalid in (
        "postgresql:///db",
        dsn.replace(":5432", ""),
        dsn.replace("?sslmode=disable", ""),
        dsn + "&sslmode=require",
        dsn + "&options=anything",
        dsn.replace(CANARY, "%XX"),
    ):
        with pytest.raises(repair.Refused):
            repair.connection_options(invalid)
    events = []

    class FakeDatabase:
        role_enforcement_disabled = False

        def __init__(self, **kwargs):
            assert kwargs == {
                **options,
                "role": repair.ROLE,
                "min_pool_size": 1,
                "max_pool_size": 1,
            }
            self.pool = SimpleNamespace(terminate=lambda: events.append("terminate"))

        async def connect(self):
            events.append("connect")
            return self.pool

        async def close(self):
            events.append("close")
            raise RuntimeError(CANARY)

    monkeypatch.setattr(repair, "Database", FakeDatabase)
    with pytest.raises(asyncio.CancelledError):
        async with repair.open_database(options):
            raise asyncio.CancelledError
    assert events == ["connect", "close", "terminate"]
    monkeypatch.setattr(repair, "read_connection", lambda _deadline: dsn)
    monkeypatch.setattr("sys.argv", ["repair", "--row-id", str(ID1)])

    async def failed(*_args):
        logging.getLogger("butlers.db").error(CANARY)
        raise RuntimeError(CANARY)

    monkeypatch.setattr(repair, "run", failed)
    # main owns asyncio.run, so invoke it outside this test's running loop.
    code = await asyncio.to_thread(repair.main)
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == '{"status":"refused_or_failed"}\n'
    assert CANARY not in captured.err
    assert CANARY not in caplog.text
    monkeypatch.setattr("sys.argv", ["repair", f"--unknown-{CANARY}"])
    assert await asyncio.to_thread(repair.main) == 2
    assert CANARY not in capsys.readouterr().err
    assert not any(t.name == "attention-metadata-deadline" for t in threading.enumerate())
    # Execute the real CLI boundary in its own process. Its transaction-style
    # finalizer ignores every cancellation, including asyncio.run's shutdown.
    # wait_for alone cannot complete this child; the owned hard watchdog must.
    for primary in ("cancel", "error"):
        child = """
import asyncio
from scripts import repair_attention_ledger_metadata as repair
repair.OPERATION_TIMEOUT = 0.05
repair.CLEANUP_TIMEOUT = 0.05
repair.read_connection = lambda deadline: "private input not emitted"
async def stalled(*args):
    try:
        if PRIMARY == "error":
            raise RuntimeError("PRIVATE_DRIVER_ERROR_CANARY")
        await asyncio.sleep(10)
    finally:
        while True:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass
repair.run = stalled
raise SystemExit(repair.main())
""".replace("PRIMARY", repr(primary))
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", child],
            capture_output=True,
            text=True,
            cwd=repair.ROOT,
            timeout=3,
        )
        assert completed.returncode == 124
        assert completed.stdout == ""
        assert completed.stderr == '{"status":"timeout_outcome_unknown"}\n'


async def test_cli_default_readonly_and_independent_postcommit_lease(monkeypatch, tmp_path):
    ledger = Ledger()
    leases = []

    @asynccontextmanager
    async def factory(_options):
        leases.append("open")
        try:
            yield ledger
        finally:
            leases.append("close")

    monkeypatch.setattr(repair, "open_database", factory)
    dsn = "postgresql://ordinary:private@localhost:5432/db?sslmode=disable"
    args = SimpleNamespace(
        apply_plan=None, row_id=[str(ID1), str(ID2)], beforeimages=None, plan_sha256=None
    )
    plan = await repair.run(args, dsn)
    assert leases == ["open", "close"] and ledger.writes == 0
    path = tmp_path / "approved-plan"
    repair.write_private(path, plan)
    args.apply_plan, args.row_id, args.beforeimages = path, [], tmp_path / "before"
    args.plan_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    leases.clear()
    result = await repair.run(args, dsn)
    assert leases == ["open", "close", "open", "close"]
    assert result == {
        "status": "committed_readback_verified",
        "updated": 2,
        "already_applied": 0,
        "unchanged": 0,
    }
