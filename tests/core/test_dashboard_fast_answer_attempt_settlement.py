"""Actual adapter cancellation and lock-clock placement; real SQL is separate."""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from butlers.core import dashboard_fast_answer_adapters as adapters
from butlers.core.dashboard_fast_answer_catalog import CatalogOutcome
from butlers.core.dashboard_fast_answer_runtime import FenceLost
from butlers.core.dashboard_fast_answer_store import FastAnswerFence

pytestmark = pytest.mark.unit


async def test_cancelled_provider_attempt_settles_exact_start_or_stays_unknown(monkeypatch):
    # REQ-dashboard-fast-answer-006 / REQ-dashboard-fast-answer-002.
    for phase in ("classification", "phrasing"):
        for refusal in (False, True):
            records, entered, clock = [], asyncio.Event(), [10.0]
            fence = FastAnswerFence(uuid4(), uuid4(), uuid4(), 1)

            class Store:
                pool = object()

                async def control(self, received_fence, action, data):
                    assert received_fence == fence
                    if action == "attribution":
                        assert data["outcome"] == "started"
                        records.append(dict(data))
                        return {"outcome": "accepted", "attempt_index": len(records) - 1}
                    assert action == "attempt_cancelled"
                    assert data["attempt_index"] == 0
                    assert {
                        k: v
                        for k, v in data.items()
                        if k not in {"outcome", "duration_ms", "attempt_index"}
                    } == {
                        k: v for k, v in records[0].items() if k not in {"outcome", "duration_ms"}
                    }
                    if refusal:
                        return {"outcome": "fenced"}
                    records.append(dict(data))
                    return {"outcome": "accepted"}

            async def provider(**kwargs):
                entered.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(adapters, "time", SimpleNamespace(monotonic=lambda: clock[0]))
            deps = adapters.RegisteredFastAnswerDependencies(SimpleNamespace(), Store())
            deps._adapter = SimpleNamespace(invoke=provider, invoke_structured=provider)
            models = tuple(
                adapters._Model("stub", uuid4(), 31, purpose, fence)
                for purpose in (
                    "dashboard_fast_answer_classification",
                    "dashboard_fast_answer_phrasing",
                )
            )
            work = (
                deps.classify(
                    models, "private operand", CatalogOutcome("no_match"), SimpleNamespace(tools={})
                )
                if phase == "classification"
                else deps.phrase(models, "private operand", [])
            )
            task = asyncio.create_task(work)
            await asyncio.wait_for(entered.wait(), 2)
            clock[0] += 0.5
            task.cancel()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            if refusal:
                assert isinstance(outcome, FenceLost)
                assert len(records) == 1
            else:
                assert isinstance(outcome, asyncio.CancelledError)
                assert [r["outcome"] for r in records] == ["started", "cancelled"]
                assert records[-1]["duration_ms"] == 500
                assert (
                    records[-1]["purpose"] == models[0 if phase == "classification" else 1].purpose
                )
            assert all("private operand" not in str(r) for r in records)


def test_clock_is_refreshed_after_each_locked_runtime_state():
    # REQ-dashboard-fast-answer-002. Source-order guard, not migrated SQL credit.
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/core/core_267_dashboard_fast_answer_runtime.py"
    )
    spec = importlib.util.spec_from_file_location("fast_lease_clock_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    chunks = []
    module.op = SimpleNamespace(execute=chunks.append)
    module.upgrade()
    sql = "\n".join(chunks)
    for name, locks in (("register", 1), ("control", 2), ("reconcile", 2)):
        body = re.search(
            r"FUNCTION public.dashboard_fast_answer_" + name + r"\(.*?END \$\$;", sql, re.S
        ).group()
        assert "stamp timestamptz :=" not in body
        last_lock = [m.end() for m in re.finditer("FOR UPDATE;", body)][locks - 1]
        refreshed = body.index("stamp := public.dashboard_fast_answer_now();", last_lock)
        assert refreshed > last_lock
        if name == "reconcile":
            assert refreshed > body.index("ORDER BY message_id LOOP")
            assert refreshed < body.index("IF r.state NOT IN")
        else:
            assert refreshed < body.index(
                "INSERT INTO" if name == "register" else "IF NOT FOUND OR r."
            )
