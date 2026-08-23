"""Safety and privacy contracts for the WhatsApp reconciliation operator CLI."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from butlers.tools.relationship.whatsapp_reconciliation import (
    ContentBlindReconciliationReport,
    PlanDigestMismatch,
    ReconciliationCategory,
    WhatsAppReconciliationPlan,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "reconcile_whatsapp_entities.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "reconcile_whatsapp_entities_under_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script_module():
    return _load_script()


class _Pool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _counts() -> dict[str, int]:
    return {category.value: 0 for category in ReconciliationCategory}


def _dry_report(digest: str = "a" * 64) -> ContentBlindReconciliationReport:
    return ContentBlindReconciliationReport(
        mode="dry_run",
        counts=_counts(),
        planned=0,
        applied=0,
        plan_digest=digest,
    )


@pytest.mark.asyncio
async def test_run_defaults_to_write_free_plan(script_module, monkeypatch) -> None:
    pool = _Pool()
    create_pool = AsyncMock(return_value=pool)
    plan = WhatsAppReconciliationPlan(
        pairs=(), counts={category: 0 for category in ReconciliationCategory}, digest="b" * 64
    )
    build = AsyncMock(return_value=plan)
    apply = AsyncMock()
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://operator-only")
    monkeypatch.setattr(script_module.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(script_module, "build_whatsapp_reconciliation_plan", build)
    monkeypatch.setattr(script_module, "apply_whatsapp_reconciliation", apply)

    report = await script_module.run(apply=False, plan_digest=None)

    assert report == _dry_report("b" * 64)
    build.assert_awaited_once_with(pool)
    apply.assert_not_awaited()
    assert pool.closed is True
    assert create_pool.await_args.args == ("postgresql://operator-only",)
    assert create_pool.await_args.kwargs["server_settings"] == {
        "search_path": "relationship,public"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("apply", "digest"),
    [(True, None), (False, "c" * 64)],
)
async def test_run_requires_apply_and_digest_as_a_pair(
    script_module, monkeypatch, apply: bool, digest: str | None
) -> None:
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://unused")

    with pytest.raises(script_module.OperatorUsageError, match="^authorization_arguments$"):
        await script_module.run(apply=apply, plan_digest=digest)


@pytest.mark.asyncio
async def test_run_rejects_missing_environment_dsn(script_module, monkeypatch) -> None:
    monkeypatch.delenv("BUTLERS_DATABASE_URL", raising=False)

    with pytest.raises(script_module.OperatorConfigurationError, match="^missing_database_url$"):
        await script_module.run(apply=False, plan_digest=None)


@pytest.mark.asyncio
async def test_run_apply_passes_only_the_authorized_digest(script_module, monkeypatch) -> None:
    pool = _Pool()
    applied = ContentBlindReconciliationReport(
        mode="apply",
        counts=_counts(),
        planned=2,
        applied=2,
        plan_digest="d" * 64,
    )
    apply_fn = AsyncMock(return_value=applied)
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://operator-only")
    monkeypatch.setattr(script_module.asyncpg, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(script_module, "apply_whatsapp_reconciliation", apply_fn)

    report = await script_module.run(apply=True, plan_digest="d" * 64)

    assert report is applied
    apply_fn.assert_awaited_once_with(pool, authorized_digest="d" * 64)
    assert pool.closed is True


@pytest.mark.asyncio
async def test_main_emits_deterministic_allowlisted_json(
    script_module, monkeypatch, capsys
) -> None:
    report = _dry_report("e" * 64)
    monkeypatch.setattr(script_module, "run", AsyncMock(return_value=report))

    exit_code = await script_module.main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert set(payload) == {"mode", "counts", "planned", "applied", "plan_digest"}
    assert payload == {
        "mode": "dry_run",
        "counts": _counts(),
        "planned": 0,
        "applied": 0,
        "plan_digest": "e" * 64,
    }
    assert captured.out.strip() == json.dumps(payload, sort_keys=True, separators=(",", ":"))


@pytest.mark.asyncio
async def test_main_maps_digest_mismatch_without_raw_exception(
    script_module, monkeypatch, capsys, caplog
) -> None:
    monkeypatch.setattr(
        script_module,
        "run",
        AsyncMock(side_effect=PlanDigestMismatch()),
    )

    exit_code = await script_module.main(["--apply", "--plan-digest", "f" * 64])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "plan_digest_mismatch"}
    assert not caplog.records


@pytest.mark.asyncio
async def test_main_never_echoes_dsn_or_exception_sentinels(
    script_module, monkeypatch, capsys, caplog
) -> None:
    sentinel = "SENSITIVE_PERSON_6599999999@s.whatsapp.net"
    monkeypatch.setenv("BUTLERS_DATABASE_URL", f"postgresql://{sentinel}")
    monkeypatch.setattr(
        script_module.asyncpg,
        "create_pool",
        AsyncMock(side_effect=RuntimeError(f"database failure {sentinel}")),
    )

    exit_code = await script_module.main([])

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert exit_code == 1
    assert sentinel not in combined
    assert json.loads(captured.err) == {"error": "reconciliation_failed"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argv",
    [["--apply"], ["--plan-digest", "0" * 64]],
)
async def test_main_rejects_unpaired_authorization_arguments(
    script_module, monkeypatch, capsys, argv: list[str]
) -> None:
    run = AsyncMock()
    monkeypatch.setattr(script_module, "run", run)

    exit_code = await script_module.main(argv)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "authorization_arguments"}
    run.assert_not_awaited()


def test_script_has_pep_723_metadata_and_no_automatic_runtime_imports() -> None:
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()

    assert lines[1] == "# /// script"
    assert any(line.startswith("# requires-python =") for line in lines[:10])
    assert any(line.startswith("# dependencies =") for line in lines[:10])
    assert "# ///" in lines[2:10]
    assert "butlers.daemon" not in source
    assert "butlers.scheduled_jobs" not in source
    assert "migrations" not in source


def test_pep_723_command_can_import_the_repository_package() -> None:
    """The documented uv invocation must not isolate the script from Butlers."""
    result = subprocess.run(
        ["uv", "run", str(_SCRIPT_PATH), "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
