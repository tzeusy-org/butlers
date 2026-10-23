"""Key-absent Switchboard startup and its refusal-only network boundary."""

from __future__ import annotations

import ast
import asyncio
import subprocess
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import FastMCP

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


async def test_key_absent_session_listener_refuses_while_service_keeps_serving():
    from butlers.control import SessionRefusalListener

    listener = SessionRefusalListener(host="127.0.0.1", port=0)
    service = ButlerDaemon._build_mcp_http_app(FastMCP("switchboard"), butler_name="switchboard")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service), base_url="http://service"
    ) as current:
        await listener.start()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.port}") as session:
                health = await session.get("/health")
                assert health.status_code == 503
                assert health.content == b'{"status":"unavailable"}'
                for method, path in (
                    ("GET", "/"),
                    ("POST", "/health"),
                    ("HEAD", "/health"),
                    ("POST", "/mcp"),
                    ("GET", "/sse"),
                    ("POST", "/goal"),
                    ("GET", "/internal/control-plane/identity"),
                ):
                    response = await session.request(method, path)
                    assert response.status_code == 404
                    assert not response.is_redirect
                assert (await current.get("/health")).json() == {"status": "ok"}
                assert (await current.get("/health")).status_code == 200
        finally:
            await listener.close()
        assert listener.task.done()
        # A stopped refusal server must not leave a listener or task behind.
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient(timeout=1) as client:
                await client.get(f"http://127.0.0.1:{listener.port}/health")


async def test_key_absent_startup_refuses_private_configuration_without_reading_values(monkeypatch):
    from butlers.control import (
        KEY_ABSENT_STAGE,
        StageConfigurationError,
        check_private_key_absent,
        check_stored_private_key_absent,
    )

    class NamesOnly(Mapping):
        def __init__(self, names):
            self.names = names

        def __iter__(self):
            return iter(self.names)

        def __len__(self):
            return len(self.names)

        def __getitem__(self, key):
            raise AssertionError("private configuration values must never be read")

    for key in (
        "GOAL_BROKER_SIGNING_KEY",
        "GOAL_BROKER_SIGNING_KEY_FILE",
        "GOAL_BROKER_SIGNING_KEY_PATH",
    ):
        with pytest.raises(StageConfigurationError, match="private-key configuration is forbidden"):
            check_private_key_absent(KEY_ABSENT_STAGE, environment=NamesOnly([key]))
    with pytest.raises(StageConfigurationError, match="unsupported control profile"):
        check_private_key_absent("ISOLATED_CANDIDATE", environment=NamesOnly([]))
    # Presence only: no material, file fixture, file opening or decoding.
    monkeypatch.setattr(Path, "iterdir", lambda self: iter([Path("GOAL_BROKER_SIGNING_KEY")]))
    with pytest.raises(StageConfigurationError, match="private-key configuration is forbidden"):
        check_private_key_absent(KEY_ABSENT_STAGE, environment=NamesOnly([]))
    monkeypatch.setattr(Path, "iterdir", lambda self: iter([]))
    check_private_key_absent(KEY_ABSENT_STAGE, environment=NamesOnly([]))

    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[True, True])
    with pytest.raises(StageConfigurationError, match="private-key configuration is forbidden"):
        await check_stored_private_key_absent(pool)
    assert all("secret_value" not in call.args[0] for call in pool.fetchval.call_args_list)
    assert "secret_key" in pool.fetchval.call_args_list[-1].args[0]
    pool.fetchval = AsyncMock(side_effect=RuntimeError("metadata unavailable"))
    with pytest.raises(StageConfigurationError, match="cannot verify private-key absence"):
        await check_stored_private_key_absent(pool)

    # Exercise the public control command's actual pre-start boundary.
    from click.testing import CliRunner

    from butlers.cli import cli

    monkeypatch.setenv("GOAL_BROKER_SIGNING_KEY", "")
    start = AsyncMock()
    monkeypatch.setattr(ButlerDaemon, "start", start)
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["control", "--profile", KEY_ABSENT_STAGE, "--config", "roster/switchboard"],
    )
    assert result.exit_code != 0
    assert "private-key configuration is forbidden" in str(result.exception)
    start.assert_not_awaited()


def test_key_absent_stage_source_has_no_private_material_resolution_or_mount():
    root = Path(__file__).resolve().parents[2]
    stage = ast.parse((root / "src/butlers/control.py").read_text())
    # The stage is deliberately incapable of obtaining a signer: no credential
    # resolver, cryptography import, file read, or environment-value lookup.
    forbidden = {
        "read",
        "read_text",
        "read_bytes",
        "open",
        "getenv",
        "resolve",
        "load",
        "load_secret",
    }
    for node in ast.walk(stage):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
            assert not any(
                word in name for name in names for word in ("credential", "crypto", "signer")
            )
        if isinstance(node, ast.Subscript):
            assert not (isinstance(node.value, ast.Attribute) and node.value.attr == "environ")
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in forbidden
            assert not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"
            )
    import yaml

    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert "butlers-control" in compose["services"]
    for name, service in compose["services"].items():
        assert "goal_broker_signing_key" not in yaml.safe_dump(service).lower(), name
    assert (
        "goal_broker_signing_key"
        not in (root / "docker-compose.control-stage.yml").read_text().lower()
    )
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    # Tracked sources only: never traverse an operator's ignored local values.
    sources = tracked + ["deploy/helm/butlers/templates/butlers-control.yaml"]
    for name in sources:
        assert "GOAL_BROKER_SIGNING_KEY" not in name
        if name.startswith("deploy/helm/butlers/") and name.endswith(".yaml"):
            assert "goal_broker_signing_key" not in (root / name).read_text().lower(), name
