"""Safety and privacy contracts for the WhatsApp reconciliation operator CLI."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest

from butlers.tools.relationship.whatsapp_reconciliation import (
    ContentBlindReconciliationReport,
    PartialApplyError,
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
    """REQ-entity-identity-002: the operator command defaults to a write-free plan."""
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
    """REQ-entity-identity-002: apply forwards only the explicitly reviewed digest."""
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
async def test_main_distinguishes_partial_commits_content_blindly(
    script_module,
    monkeypatch,
    capsys,
    caplog,
) -> None:
    """REQ-entity-identity-002: committed partial apply has a distinct safe result."""
    sentinel = "SENSITIVE_PARTIAL_6599999999@s.whatsapp.net"
    stopped = PartialApplyError(
        applied=1,
        planned=3,
        stop_category="postcondition_failed",
        plan_digest="f" * 64,
    )
    stopped.__cause__ = RuntimeError(sentinel)
    monkeypatch.setattr(script_module, "run", AsyncMock(side_effect=stopped))

    exit_code = await script_module.main(["--apply", "--plan-digest", "f" * 64])

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert exit_code == 3
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "partial_apply",
        "applied": 1,
        "planned": 3,
        "stop_category": "postcondition_failed",
        "plan_digest": "f" * 64,
    }
    assert sentinel not in combined


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
    """REQ-entity-identity-002: no daemon, scheduler, or migration can auto-apply."""
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
        ["uv", "run", "--isolated", "--no-project", str(_SCRIPT_PATH), "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_pep_723_apply_path_runs_with_only_declared_dependencies(
    postgres_container,
) -> None:
    """The isolated operator environment must reach and complete the real merge path."""
    admin_url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )
    parsed = urlsplit(admin_url)
    database_name = f"reconciliation_apply_{uuid4().hex[:12]}"
    database_url = urlunsplit(parsed._replace(path=f"/{database_name}"))

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await admin.close()

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            """
            CREATE SCHEMA relationship;
            CREATE TABLE public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'person',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE public.whatsmeow_lid_map (
                lid TEXT PRIMARY KEY,
                pn TEXT NOT NULL
            );
            CREATE TABLE relationship.entity_predicate_registry (
                predicate TEXT PRIMARY KEY,
                cardinality TEXT NOT NULL DEFAULT 'multi'
            );
            CREATE TABLE relationship.entity_facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject UUID NOT NULL REFERENCES public.entities(id),
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                src TEXT NOT NULL,
                conf FLOAT NOT NULL DEFAULT 1.0,
                last_seen TIMESTAMPTZ,
                observed_at TIMESTAMPTZ,
                verified BOOL NOT NULL DEFAULT false,
                "primary" BOOL,
                validity TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE relationship.facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id UUID,
                object_entity_id UUID,
                predicate TEXT NOT NULL,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                valid_at TIMESTAMPTZ,
                supersedes_id UUID,
                scope TEXT NOT NULL DEFAULT 'relationship',
                validity TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE relationship.contact_entity_map (
                contact_id UUID PRIMARY KEY,
                entity_id UUID NOT NULL
            );
            CREATE TABLE relationship.merge_reviews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_a UUID NOT NULL REFERENCES public.entities(id),
                entity_b UUID NOT NULL REFERENCES public.entities(id),
                shared_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
                divergent_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
                outcome TEXT NOT NULL,
                reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE relationship.pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        source_id = await conn.fetchval(
            """
            INSERT INTO public.entities (canonical_name, metadata)
            VALUES (
                '6591234567@s.whatsapp.net',
                '{"unidentified":true,"source_channel":"whatsapp_user_client",'
                '"source_value":"structured-provider-value"}'::jsonb
            )
            RETURNING id
            """
        )
        target_id = await conn.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Confirmed target') RETURNING id"
        )
        await conn.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src)
            VALUES ($1, 'has-phone', '+6591234567', 'literal', 'test')
            """,
            target_id,
        )
    finally:
        await conn.close()

    env = {**os.environ, "BUTLERS_DATABASE_URL": database_url}

    def _run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--with",
                "asyncpg",
                "python",
                str(_SCRIPT_PATH),
                *args,
            ],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    dry_run = await asyncio.to_thread(_run)
    assert dry_run.returncode == 0, dry_run.stderr
    digest = json.loads(dry_run.stdout)["plan_digest"]

    apply = await asyncio.to_thread(_run, "--apply", "--plan-digest", digest)

    assert apply.returncode == 0, apply.stderr
    assert json.loads(apply.stdout)["applied"] == 1
    verify = await asyncpg.connect(database_url)
    try:
        assert await verify.fetchval(
            "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1",
            source_id,
        ) == str(target_id)
    finally:
        await verify.close()
