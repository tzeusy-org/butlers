"""D3 host consumer contracts; process-double outcomes are software evidence only."""

import dataclasses
import hashlib
from uuid import uuid4

import pytest

from butlers.session_host.launch import LaunchRequest, Refused, SessionHost, TupleProof

pytestmark = pytest.mark.unit


class Sandbox:
    def __init__(self):
        self.calls = []

    async def run(self, prepared, *, timeout_s):
        self.calls.append((prepared, timeout_s))
        return b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'


def request():
    return LaunchRequest(
        invocation_id=uuid4(),
        runtime="codex",
        model="fixture-model",
        provider="openai",
        prompt="Reply briefly.",
        system_prompt="A fixed fixture.",
        credential=b'{"OPENAI_API_KEY":"fixture-only"}',
        broker_token="fixture-session-token",
        timeout_s=10,
    )


async def test_host_requires_exact_server_held_tuple_before_staging(tmp_path):
    binary = tmp_path / "codex"
    binary.write_bytes(b"fixture executable; never launched")
    sandbox = Sandbox()
    host = SessionHost(
        binary=binary, broker_url="http://broker.invalid:41199/mcp", proofs=(), sandbox=sandbox
    )
    with pytest.raises(Refused, match="runtime_tuple_unproven"):
        await host.launch(request())
    assert sandbox.calls == []
    proof = TupleProof(
        runtime="codex",
        model="fixture-model",
        provider="openai",
        executable_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        evidence_sha256="a" * 64,
    )
    host = SessionHost(
        binary=binary,
        broker_url="http://broker.invalid:41199/mcp",
        proofs=(proof,),
        sandbox=sandbox,
    )
    result = await host.launch(request())
    assert result.text == "ok"
    prepared, timeout_s = sandbox.calls[0]
    assert timeout_s == 10
    assert b"fixture-only" not in repr(prepared).encode()
    assert prepared.command[0] == str(binary)
    assert "--dangerously-bypass-approvals-and-sandbox" not in prepared.command
    assert "--sandbox" in prepared.command and "read-only" in prepared.command
    assert len(prepared.authorities) == 3
    assert all(
        not item.relative_path.is_absolute() and ".." not in item.relative_path.parts
        for item in prepared.authorities
    )
    assert {str(item.relative_path) for item in prepared.authorities} == {
        ".codex/auth.json",
        ".codex/config.toml",
        "system.md",
    }
    assert not any(name in repr(prepared) for name in ("DATABASE_URL", "BWS_ACCESS_TOKEN"))
    for changed in (
        dataclasses.replace(request(), model="other-model"),
        dataclasses.replace(request(), runtime="opencode"),
        dataclasses.replace(request(), provider="other-provider"),
    ):
        with pytest.raises(Refused, match="runtime_tuple_unproven"):
            await host.launch(changed)
    assert len(sandbox.calls) == 1
    binary.write_bytes(b"replacement binary")
    with pytest.raises(Refused, match="runtime_tuple_unproven"):
        await host.launch(request())
    assert len(sandbox.calls) == 1


def test_control_request_cannot_supply_launch_authority():
    value = request().to_wire()
    assert LaunchRequest.from_wire(value) == request_from(value)
    for name, value in (
        ("env", {"DATABASE_URL": "forbidden"}),
        ("argv", ["sh"]),
        ("proof", True),
        ("broker_url", "http://domain.invalid"),
    ):
        with pytest.raises(Refused):
            LaunchRequest.from_wire({**request().to_wire(), name: value})
    with pytest.raises(Refused):
        dataclasses.replace(request(), timeout_s=float("nan"))
    with pytest.raises(Refused):
        dataclasses.replace(request(), credential=b'{"DATABASE_URL":"forbidden"}')


def request_from(value):
    # Round-trip the original object instead of minting another invocation UUID.
    from uuid import UUID

    return dataclasses.replace(request(), invocation_id=UUID(value["invocation_id"]))
