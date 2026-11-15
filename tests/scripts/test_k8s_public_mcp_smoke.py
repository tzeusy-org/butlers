"""scripts/k8s/public-mcp-smoke.py against the real host (task 2.5 smoke, run before go-live).

The smoke signs its own test-key assertions and renders its own keyring. Running it against a
real :class:`CapabilityHost` (fake projection, real verifier, real static MCP handlers) proves
its keyring and assertions are the ones the server accepts, and that every check can pass and
can fail.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.capability_host import Tool
from butlers.knowledge_read.core import ReadError
from butlers.knowledge_read.describe import TOOLS
from butlers.mcp_public.catalogue import STATIC_HANDLERS
from tests.capability_host.helpers import AUD, BEARER, SUB, Env

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "public_mcp_smoke", _ROOT / "scripts" / "k8s" / "public-mcp-smoke.py"
)
assert _spec and _spec.loader
smoke_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_module)

_SEED = os.urandom(32)
_KID = "public-mcp-smoke-test"


def _tools(refuse: dict[str, str] | None = None) -> dict[str, Tool]:
    refuse = refuse or {}

    def reader(name: str):
        async def read(conn, context, arguments):
            if name in refuse:
                raise ReadError(refuse[name])
            await conn.fetchval("SELECT data")
            return {"results": []} if name == "search" else {"matches": [], "items": []}

        return read

    return {name: Tool(name, None, reader(name), dlp=False) for name in TOOLS}


def _env(tmp_path: Path, **kw) -> Env:
    env = Env(tmp_path, tools=_tools(**kw), static=STATIC_HANDLERS)
    now = datetime.fromtimestamp(env.clock.now, UTC)
    env.write("keyring/keyring.json", json.dumps(smoke_module.keyring(_SEED, _KID, now)))
    env.reload()
    return env


def _smoke(env: Env, **cfg) -> object:
    def send(method: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        async def read_body(limit: int) -> bytes:
            return body[:limit]

        response = asyncio.run(env.host.handle(method, list(headers.items()), "", read_body))
        return response.status, response.body

    config = {"seed_hex": _SEED.hex(), "kid": _KID, "bearer": BEARER, "aud": AUD, "sub": SUB}
    config.update(cfg)
    return smoke_module.Smoke(config, send, clock=lambda: env.clock.now, pace=0)


def _run(smoke) -> None:
    smoke.first_call()
    smoke.transport_and_auth()
    smoke.catalogue()
    smoke.timing(4)


def test_every_check_passes_against_a_correctly_configured_host(tmp_path, capsys):
    smoke = _smoke(_env(tmp_path))
    _run(smoke)
    out = capsys.readouterr().out
    assert smoke.failures == 0, out
    assert "FAIL" not in out
    assert "TOOL propose_fact" not in out  # it writes an inbox row; never called
    assert out.count("ok   TOOL ") == len(TOOLS) - 1


def test_setup_state_refusals_count_as_served_but_other_codes_fail(tmp_path, capsys):
    refuse = {"finance_ledger": "domain_disabled", "trips": "invalid_argument"}
    smoke = _smoke(_env(tmp_path, refuse=refuse))
    smoke.catalogue()
    out = capsys.readouterr().out
    assert "ok   TOOL finance_ledger refuses domain_disabled" in out
    assert "FAIL TOOL trips refuses invalid_argument" in out
    assert smoke.failures == 1


def test_a_wrong_bearer_or_foreign_key_is_reported_not_passed(tmp_path, capsys):
    smoke = _smoke(_env(tmp_path), bearer="not-the-configured-bearer")
    smoke.transport_and_auth()
    assert "FAIL AUTH a signed describe call is admitted" in capsys.readouterr().out

    smoke = _smoke(_env(tmp_path), seed_hex=os.urandom(32).hex())
    smoke.transport_and_auth()
    assert "FAIL AUTH a signed describe call is admitted" in capsys.readouterr().out


def test_output_never_carries_the_bearer_or_the_seed(tmp_path, capsys):
    smoke = _smoke(_env(tmp_path))
    _run(smoke)
    out = capsys.readouterr().out
    assert BEARER not in out
    assert _SEED.hex() not in out


def test_p95_is_the_nearest_rank():
    assert smoke_module.p95([float(i) for i in range(1, 101)]) == 95.0
    assert smoke_module.p95([5.0]) == 5.0
