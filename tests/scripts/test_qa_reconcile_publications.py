"""Operator entry point for read-only QA publication reconciliation (bu-mc01y5.30).

The sweep itself is covered in ``tests/core/qa/test_publication_reconcile.py``
and against real PostgreSQL in ``tests/integration/test_qa_publication_state.py``;
these tests pin the entry point's credential handling and report shape.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.publication.reconcile import SweepEntry

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "qa_reconcile_publications.py"
_TOKEN = "synthetic-token-value"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("qa_reconcile_publications", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("argv", "token", "code"),
    [
        (["--repository", "acme/butlers"], None, "token_missing"),
        (["--repository", "acme/butlers/x"], _TOKEN, "invalid_repository"),
        (["--repository", "acme/butlers", "--limit", "0"], _TOKEN, "invalid_arguments"),
        (["--repository", "acme/butlers"], _TOKEN, "connection_input_missing"),
    ],
    ids=["no-token", "bad-origin", "bad-limit", "no-dsn"],
)
def test_refusals_print_only_a_closed_code(script, monkeypatch, capsys, argv, token, code) -> None:
    monkeypatch.setattr(sys, "argv", ["qa_reconcile_publications.py", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    if token is None:
        monkeypatch.delenv("BUTLERS_QA_GH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BUTLERS_QA_GH_TOKEN", token)

    assert script.main() == 2
    assert json.loads(capsys.readouterr().out) == {"error": code}


async def test_run_assumes_the_qa_role_and_reports_ids_and_categories(
    script, monkeypatch, capsys
) -> None:
    pool = MagicMock()
    context = MagicMock(
        __aenter__=AsyncMock(return_value=pool), __aexit__=AsyncMock(return_value=None)
    )
    create_pool = MagicMock(return_value=context)
    held = SweepEntry(uuid.uuid4(), uuid.uuid4(), "pr_create", "held", "absence_settling")
    sweep = AsyncMock(return_value=(held,))
    monkeypatch.setattr(script.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(script, "sweep_held_operations", sweep)
    reader = script.GithubPublicationReader("acme/butlers", token=_TOKEN)

    assert await script.run(dsn="postgresql://synthetic", reader=reader, limit=5) == 2

    connection = MagicMock(execute=AsyncMock())
    await create_pool.call_args.kwargs["setup"](connection)
    connection.execute.assert_awaited_once_with("SET ROLE butler_qa_rw")
    sweep.assert_awaited_once_with(pool, reader, limit=5)
    out = capsys.readouterr().out
    assert json.loads(out)["entries"] == [
        {
            "operation_id": str(held.operation_id),
            "attempt_id": str(held.attempt_id),
            "kind": "pr_create",
            "status": "held",
            "category": "absence_settling",
        }
    ]
    assert _TOKEN not in out and "synthetic" not in out
