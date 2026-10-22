"""Exact-source recovery control doubles; not PostgreSQL/MVCC evidence."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from butlers.chronicler.day_close_recovery import (
    RecoveryRefused,
    apply_recovery,
    plan_recovery,
)
from butlers.mcp_wrappers import _tool_input_fingerprint
from tests.chronicler.test_day_close_narration_selection import notification, registered_notify

pytestmark = pytest.mark.unit
DAY = date(2026, 4, 24)
SID = UUID("00000000-0000-4000-8000-000000000001")
NID = UUID("00000000-0000-4000-8000-000000000002")
MESSAGE = "The day settled into focused reading and an evening walk."
NOW = datetime(2026, 4, 25, 12, tzinfo=UTC)


def records():
    calls = [
        {
            "name": "chronicler_day_close_bundle",
            "input": {"date_label": DAY.isoformat(), "timezone": "UTC"},
            "outcome": "success",
            "result": {
                "date": DAY.isoformat(),
                "episodes": [{}],
                "events": [],
                "citations": ["fixture:one"],
            },
        },
        notification(),
    ]
    session = {
        "id": SID,
        "success": True,
        "trigger_source": "schedule:chronicler_day_close",
        "completed_at": NOW,
        "tool_calls": calls,
    }
    deferred = {
        "id": NID,
        "butler_name": "chronicler",
        "channel": "telegram",
        "message": MESSAGE,
        "status": "pending",
        "envelope": {
            "schema_version": "notify.v1",
            "origin_butler": "chronicler",
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "message": MESSAGE,
                "recipient": "fixture-owner",
            },
        },
    }
    ledger = {
        "id": UUID("00000000-0000-4000-8000-000000000003"),
        "origin_butler": "chronicler",
        "source": "notify",
        "outcome": "deferred",
        "channel": "telegram",
        "intent": "send",
        "notification_ref": str(NID),
        "metadata": {"session_id": str(SID)},
    }
    return session, deferred, ledger


class Connection:
    def __init__(self):
        session, self.deferred, ledger = records()
        self.sessions = [session]
        self.ledger = [ledger]
        self.cache = None
        self.rollback_count = 0
        self.write_fail = False
        self.cancel_write = False
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        return deepcopy(self.sessions if "FROM sessions" in sql else self.ledger)

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        return deepcopy(self.deferred if "FROM deferred_notifications" in sql else self.cache)

    async def execute(self, sql, *args):
        self.queries.append((sql, args))
        if "INSERT INTO tier2_cache" in sql:
            if self.cancel_write:
                raise asyncio.CancelledError()
            if self.write_fail:
                raise RuntimeError("dummy private database refusal")
            self.cache = {
                "prose": args[3],
                "provenance_refs": args[4],
                "date_label": args[6],
                "invalid_reason": args[7],
                "start_at": args[1],
                "end_at": args[2],
            }

    @asynccontextmanager
    async def transaction(self, **kwargs):
        assert kwargs == {"isolation": "serializable"}
        before = deepcopy(self.cache)
        try:
            yield
        except BaseException:
            self.cache = before
            self.rollback_count += 1
            raise


class Pool:
    def __init__(self):
        self.conn = Connection()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def plan(pool):
    return await plan_recovery(pool, start=DAY, end=DAY, timezone="UTC", now=NOW)


async def test_exact_provenance_plan_is_content_blind_and_application_idempotent():
    pool = Pool()
    value = await plan(pool)
    assert value.entries[0].reason == "eligible"
    serialized = json.dumps(value.safe_dict())
    assert (
        MESSAGE not in serialized
        and "fixture-owner" not in serialized
        and str(SID) not in serialized
    )
    first = await apply_recovery(pool, value)
    assert first[0].reason == "applied"
    assert pool.conn.cache["prose"] == MESSAGE
    assert pool.conn.cache["provenance_refs"][0] == "fixture:one"
    original = deepcopy(pool.conn.cache)
    assert (await apply_recovery(pool, value))[0].reason == "already_applied"
    assert pool.conn.cache == original
    assert any("FOR SHARE" in sql and "FROM sessions" in sql for sql, _ in pool.conn.queries)
    assert not any(
        "INSERT INTO public.attention_ledger" in sql or "UPDATE deferred_notifications" in sql
        for sql, _ in pool.conn.queries
    )


@pytest.mark.parametrize(
    "species", ["missing", "ambiguous", "recipient", "message", "session-link", "cancelled"]
)
async def test_unproven_sources_refuse_without_guessing_day_or_recipient(species):
    pool = Pool()
    if species == "missing":
        pool.conn.sessions = []
    elif species == "ambiguous":
        pool.conn.sessions *= 2
    elif species == "recipient":
        pool.conn.sessions[0]["tool_calls"][1]["input"]["recipient"] = "fixture-other"
    elif species == "message":
        pool.conn.deferred["message"] = "Different retrospective."
    elif species == "session-link":
        pool.conn.ledger = []
    else:
        pool.conn.deferred["status"] = "cancelled"
    value = await plan(pool)
    assert value.entries[0].reason != "eligible"
    assert value.entries[0].fingerprint is None
    assert (await apply_recovery(pool, value))[0].reason == value.entries[0].reason
    assert pool.conn.cache is None


async def test_drift_and_valid_normal_refresh_are_preserved():
    pool = Pool()
    value = await plan(pool)
    pool.conn.deferred["status"] = "delivered"
    with pytest.raises(RecoveryRefused, match="source_drift"):
        await apply_recovery(pool, value)
    assert pool.conn.cache is None
    pool.conn.deferred["status"] = "pending"
    pool.conn.cache = {
        "prose": "A newer genuine retrospective.",
        "date_label": DAY.isoformat(),
        "invalid_reason": None,
        "provenance_refs": [],
        "start_at": datetime(2026, 4, 24, tzinfo=UTC),
        "end_at": datetime(2026, 4, 25, tzinfo=UTC),
    }
    original = deepcopy(pool.conn.cache)
    assert (await apply_recovery(pool, value))[0].reason == "preserved_valid"
    assert pool.conn.cache == original


@pytest.mark.parametrize("wrong_window", [False, True])
async def test_invalid_destination_recovery_retains_audit_digest(wrong_window):
    pool = Pool()
    value = await plan(pool)
    pool.conn.cache = {
        "prose": MESSAGE if wrong_window else "Delivery deferred.",
        "date_label": DAY.isoformat(),
        "invalid_reason": None,
        "provenance_refs": [],
        "start_at": datetime(2026, 4, 23 if wrong_window else 24, tzinfo=UTC),
        "end_at": datetime(2026, 4, 24 if wrong_window else 25, tzinfo=UTC),
    }
    assert (await apply_recovery(pool, value))[0].reason == "applied"
    assert pool.conn.cache["prose"] == MESSAGE
    assert any(
        ref.startswith("chronicler.day_close_recovery_previous:")
        for ref in pool.conn.cache["provenance_refs"]
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_or_cancellation_rolls_back_without_converting_it_to_success(cancel):
    pool = Pool()
    value = await plan(pool)
    pool.conn.cancel_write = cancel
    pool.conn.write_fail = not cancel
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await apply_recovery(pool, value)
    assert pool.conn.rollback_count == 1
    assert pool.conn.cache is None


@pytest.mark.parametrize(
    "start,end,timezone",
    [
        (date(2026, 3, 1), DAY, "UTC"),
        (DAY, date(2026, 4, 25), "UTC"),
        (DAY, DAY, "Invalid/Fixture"),
        (DAY, date(2026, 4, 23), "UTC"),
    ],
)
async def test_range_refuses_before_any_source_read(start, end, timezone):
    pool = Pool()
    with pytest.raises(ValueError):
        await plan_recovery(pool, start=start, end=end, timezone=timezone, now=NOW)
    assert pool.conn.queries == []


def _operator():
    path = Path(__file__).resolve().parents[2] / "scripts/recover_chronicler_day_close_cache.py"
    spec = importlib.util.spec_from_file_location("day_close_recovery_operator_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "species", ["duplicate", "unknown", "oversize", "authority", "digest", "reason-type"]
)
def test_operator_plan_refuses_unbounded_or_forged_metadata(tmp_path, species):
    operator = _operator()
    value = {
        "timezone": "UTC",
        "entries": [{"day": DAY.isoformat(), "reason": "eligible", "fingerprint": "a" * 64}],
    }
    if species == "unknown":
        value["private_payload"] = "dummy forbidden message"
    elif species == "authority":
        value["entries"][0]["reason"] = "already_applied"
    elif species == "digest":
        value["entries"][0]["fingerprint"] = None
    elif species == "reason-type":
        value["entries"][0]["reason"] = []
    text = json.dumps(value)
    if species == "duplicate":
        text = '{"timezone":"UTC",' + text[1:]
    elif species == "oversize":
        text += " " * 65537
    path = tmp_path / "safe-plan.json"
    path.write_text(text)
    with pytest.raises(RecoveryRefused, match="invalid_plan"):
        operator.read_plan(path)


def test_operator_argument_refusal_does_not_echo_caller_value(monkeypatch, capsys):
    operator = _operator()
    sentinel = "dummy-private-argument"
    monkeypatch.setattr("sys.argv", ["operator", "--start", sentinel])
    with pytest.raises(SystemExit) as error:
        operator.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "invalid_arguments"}
    assert sentinel not in captured.out + captured.err


@pytest.mark.parametrize("channel", ["telegram", "email"])
async def test_recovery_uses_same_bound_carrier_and_actual_deferred_channel(channel):
    pool = Pool()
    calls = pool.conn.sessions[0]["tool_calls"]
    captured = calls[1]
    payload = dict(captured.pop("input"))
    if channel == "telegram":
        # Omitted channel is bound using the real declared default, while the
        # actual deferred result supplies the resolved durable channel.
        del payload["channel"]
    else:
        payload["channel"] = channel
    captured["input_fingerprint"] = _tool_input_fingerprint(registered_notify(), (), payload)
    captured["result"]["channel"] = channel
    calls.append({"name": "notify", "input": payload})
    pool.conn.deferred["channel"] = channel
    pool.conn.deferred["envelope"]["delivery"]["channel"] = channel
    pool.conn.ledger[0]["channel"] = channel
    value = await plan(pool)
    assert value.entries[0].reason == "eligible"
    assert (await apply_recovery(pool, value))[0].reason == "applied"
    assert pool.conn.cache["prose"] == MESSAGE
    assert "input" not in captured


async def test_immediate_sent_result_cannot_be_rebound_to_a_deferred_row():
    pool = Pool()
    pool.conn.sessions[0]["tool_calls"][1]["result"] = {
        "status": "ok",
        "result": {"status": "sent", "notification_id": str(NID)},
    }
    value = await plan(pool)
    assert value.entries[0].reason == "unproven_notification"
    assert not any("FROM deferred_notifications" in sql for sql, _ in pool.conn.queries)


def test_operator_connection_input_obeys_the_overall_deadline(monkeypatch):
    import os
    import time

    operator = _operator()
    reader, writer = os.pipe()
    try:
        os.write(writer, b"dummy-unfinished-input")
        with os.fdopen(reader, "rb", buffering=0) as stream:
            monkeypatch.setattr("sys.stdin", stream)
            started = time.monotonic()
            with pytest.raises(RecoveryRefused, match="connection_input_timeout"):
                operator._read_connection(deadline=started + 0.03)
            assert time.monotonic() - started < 1
    finally:
        os.close(writer)
    # A complete injected line still returns its actual value; no DB/model call.
    reader, writer = os.pipe()
    try:
        os.write(writer, b"dummy-complete-input\nnot-part-of-the-line")
        with os.fdopen(reader, "rb", buffering=0) as stream:
            monkeypatch.setattr("sys.stdin", stream)
            assert (
                operator._read_connection(deadline=time.monotonic() + 1) == "dummy-complete-input"
            )
    finally:
        os.close(writer)


def test_operator_plan_rejects_special_files_without_waiting(tmp_path):
    import os
    import subprocess
    import sys

    fifo = tmp_path / "plan.fifo"
    os.mkfifo(fifo)
    operator = Path(__file__).resolve().parents[2] / "scripts/recover_chronicler_day_close_cache.py"
    code = """
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("operator_control", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    module.read_plan(Path(sys.argv[2]))
except module.RecoveryRefused as error:
    assert str(error) == "invalid_plan"
else:
    raise AssertionError("special plan accepted")
"""
    # The child executes the actual reader, unchanged. An old blocking FIFO
    # reader fails this bound and is killed/reaped by subprocess.run.
    result = subprocess.run(
        [sys.executable, "-c", code, str(operator), str(fifo)],
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""
    valid = tmp_path / "plan.json"
    valid.write_text(
        json.dumps(
            {
                "timezone": "UTC",
                "entries": [
                    {"day": DAY.isoformat(), "reason": "eligible", "fingerprint": "a" * 64}
                ],
            }
        )
    )
    assert _operator().read_plan(valid).entries[0].reason == "eligible"
    link = tmp_path / "plan-link.json"
    link.symlink_to(valid)
    with pytest.raises(RecoveryRefused, match="invalid_plan"):
        _operator().read_plan(link)


async def test_recovery_provenance_excludes_unexecuted_or_unrelated_source_results():
    pool = Pool()
    pool.conn.sessions[0]["tool_calls"].extend(
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-04-23", "timezone": "Europe/London"},
                "result": {"date": "2026-04-23", "citations": ["fixture:foreign-parser"]},
            },
            {
                "name": "chronicler_list_events",
                "outcome": "success",
                "result": {"data": [{"source_ref": "fixture:unrelated-list"}]},
            },
        ]
    )
    value = await plan(pool)
    assert value.entries[0].reason == "eligible"
    assert (await apply_recovery(pool, value))[0].reason == "applied"
    refs = pool.conn.cache["provenance_refs"]
    assert "fixture:one" in refs
    assert "fixture:foreign-parser" not in refs
    assert "fixture:unrelated-list" not in refs
