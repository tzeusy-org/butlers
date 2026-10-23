"""Default-off construction and immutable proof consumers, not eligibility proofs."""

import hashlib
import json
import os

import pytest

from butlers.session_host.launch import _PROFILE, Refused
from butlers.session_host.service import HostConfig, load_proofs, stage_proofs, start_host

pytestmark = pytest.mark.unit


async def test_disabled_host_never_reads_files_constructs_platform_or_opens_socket(monkeypatch):
    import butlers.session_host.service as service

    def forbidden(*args, **kwargs):
        pytest.fail("disabled host crossed an activation boundary")

    monkeypatch.setattr(service, "load_proofs", forbidden)
    monkeypatch.setattr(service, "BubblewrapSessionSandbox", forbidden)
    monkeypatch.setattr(service.asyncio, "start_server", forbidden)
    assert await start_host(HostConfig()) is None

    from butlers.session_host.__main__ import run

    monkeypatch.setattr("butlers.session_host.__main__.prepare_auth_volume", forbidden)
    await run(kind="prepare-auth-volume", enabled=False, config=None)


async def test_credential_volume_init_prepares_only_empty_owned_real_directory(tmp_path):
    from butlers.session_host.credential_volume import CodexCredentialVolume
    from butlers.session_host.service import prepare_auth_volume

    root = tmp_path / "volume"
    root.mkdir(mode=0o755)
    root.chmod(0o755)  # Exact emptyDir input, independent of the test runner's umask.
    prepare_auth_volume(root, expected_uid=os.getuid())
    assert root.stat().st_mode & 0o777 == 0o700
    initial_mount = tmp_path / "fresh-mounted-volume"
    initial_mount.mkdir()
    initial_mount.chmod(0o777)
    prepare_auth_volume(initial_mount, expected_uid=os.getuid())
    assert initial_mount.stat().st_mode & 0o777 == 0o700
    assert (await CodexCredentialVolume(root, expected_uid=os.getuid()).read()).content is None
    prepare_auth_volume(root, expected_uid=os.getuid())
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(Refused):
        prepare_auth_volume(link, expected_uid=os.getuid())
    with pytest.raises(Refused):
        prepare_auth_volume(root, expected_uid=os.getuid() + 1)
    # An existing permissive tree is not silently repaired or read as auth.
    root.chmod(0o755)
    sentinel = root / "unreviewed"
    sentinel.write_bytes(b"fixture")
    with pytest.raises(Refused):
        prepare_auth_volume(root, expected_uid=os.getuid())
    assert sentinel.read_bytes() == b"fixture"
    assert root.stat().st_mode & 0o777 == 0o755


def test_trusted_proof_loader_binds_full_tuple_and_evidence_and_refuses_mutation(tmp_path):
    # This fixture proves the loader predicate only. It does not earn an
    # eligible tuple in a deployment or claim adapter/provider conformance.
    evidence = {
        "protocol": "session-adapter-proof-v1",
        "runtime": "codex",
        "model": "fixture-model",
        "provider": "openai",
        "executable_sha256": "a" * 64,
        "profile_sha256": _PROFILE,
        "checks": {
            "builtin_tools": "denied",
            "mcp": "broker-only",
            "provider_hosted_tools": "absent",
            "filesystem": "isolated",
            "processes": "isolated",
            "descendants": "dead",
            "egress": "allowlist-only",
            "catalog_conformance": "passed",
        },
    }
    payload = json.dumps(evidence).encode()
    digest = hashlib.sha256(payload).hexdigest()
    artifact = tmp_path / f"{digest}.json"
    artifact.write_bytes(payload)
    artifact.chmod(0o444)
    manifest = tmp_path / "index.json"
    row = {
        key: evidence[key]
        for key in ("runtime", "model", "provider", "executable_sha256", "profile_sha256")
    }
    row["evidence_sha256"] = digest
    manifest.write_text(json.dumps({"version": 1, "tuples": [row]}))
    manifest.chmod(0o444)
    proofs = load_proofs(manifest, expected_uid=os.getuid())
    assert len(proofs) == 1
    assert proofs[0].evidence_sha256 == digest
    projected, snapshot = tmp_path / "projected", tmp_path / "snapshot"
    projected.mkdir()
    snapshot.mkdir()
    (projected / "index.json").symlink_to(manifest)
    (projected / artifact.name).symlink_to(artifact)
    with pytest.raises(Refused):
        load_proofs(projected / "index.json", expected_uid=os.getuid())
    stage_proofs(projected, snapshot, expected_uid=os.getuid())
    assert load_proofs(snapshot / "index.json", expected_uid=os.getuid()) == proofs
    assert not (snapshot / artifact.name).is_symlink()
    with pytest.raises(Refused):
        stage_proofs(projected, snapshot, expected_uid=os.getuid())
    assert load_proofs(snapshot / "index.json", expected_uid=os.getuid()) == proofs
    artifact.chmod(0o644)
    with pytest.raises(Refused, match="runtime_tuple_unproven"):
        load_proofs(manifest, expected_uid=os.getuid())
    artifact.write_bytes(payload.replace(b'"absent"', b'"unknown"'))
    artifact.chmod(0o444)
    with pytest.raises(Refused):
        load_proofs(manifest, expected_uid=os.getuid())
    manifest.chmod(0o644)
    manifest.write_text(json.dumps({"version": 1, "tuples": []}))
    manifest.chmod(0o444)
    # An authenticated CLI login must not require an already eligible model.
    # Empty evidence grants no runtime tuple and cannot create one at startup.
    assert load_proofs(manifest, expected_uid=os.getuid()) == ()


async def test_host_preparation_reaches_real_platform_bridge_without_ambient_authority(tmp_path):
    from butlers.cli_auth.sandbox import SandboxedCommandResult
    from butlers.session_host.launch import SessionHost, TupleProof
    from butlers.session_host.sandbox import BubblewrapSessionSandbox
    from tests.session_host.test_launch import request

    binary = tmp_path / "codex"
    binary.write_bytes(b"not an executable: software seam fixture")

    class Platform:
        calls = []

        async def run_staged_command(self, provider, **kwargs):
            self.calls.append((provider, kwargs))
            return SandboxedCommandResult(
                0, b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
            )

    platform = Platform()
    host = SessionHost(
        binary=binary,
        broker_url="https://broker.invalid:41199/mcp",
        proofs=(
            TupleProof(
                "codex",
                "fixture-model",
                "openai",
                hashlib.sha256(binary.read_bytes()).hexdigest(),
                "a" * 64,
            ),
        ),
        sandbox=BubblewrapSessionSandbox(platform),
    )
    assert (await host.launch(request())).text == "ok"
    provider, call = platform.calls[0]
    assert provider.name == "codex"
    assert call["expected_executable"] == binary
    assert call["command"][0] == "codex"
    assert set(call) == {"command", "expected_executable", "authorities", "timeout_s"}
    assert {str(item.relative_path) for item in call["authorities"]} == {
        ".codex/auth.json",
        ".codex/config.toml",
        "system.md",
    }
    config = next(
        item.content
        for item in call["authorities"]
        if str(item.relative_path) == ".codex/config.toml"
    )
    assert b"https://broker.invalid:41199/mcp" in config
    assert b"fixture-session-token" in config


async def test_model_function_profile_reaches_real_bridge_without_goal_mcp_or_tool_results(
    tmp_path,
):
    from uuid import uuid4

    from butlers.cli_auth.sandbox import SandboxedCommandResult
    from butlers.session_host.launch import TupleProof
    from butlers.session_host.model_function import (
        FUNCTION_PROFILE,
        ModelFunctionHost,
        ModelFunctionRequest,
    )
    from butlers.session_host.sandbox import BubblewrapSessionSandbox

    binary = tmp_path / "opencode"
    binary.write_bytes(b"software fixture, never executed")

    class Platform:
        calls = []
        output = b'{"type":"text","part":{"type":"text","text":"structured fixture"}}\n'

        async def run_staged_command(self, provider, **kwargs):
            self.calls.append((provider, kwargs))
            return SandboxedCommandResult(0, self.output)

    platform = Platform()
    value = ModelFunctionRequest(
        invocation_id=uuid4(),
        purpose="connector_discretion",
        runtime="opencode",
        model="openai/fixture",
        provider="openai",
        prompt="Fixed fixture query",
        system_prompt="Return the caller's fixed structured format.",
        credential=b'{"OPENAI_API_KEY":"fixture-only"}',
        timeout_s=10,
    )
    host = ModelFunctionHost(binary=binary, proofs=(), sandbox=BubblewrapSessionSandbox(platform))
    with pytest.raises(Refused, match="runtime_tuple_unproven"):
        await host.launch(value)
    assert platform.calls == []
    host = ModelFunctionHost(
        binary=binary,
        proofs=(
            TupleProof(
                "opencode",
                value.model,
                "openai",
                hashlib.sha256(binary.read_bytes()).hexdigest(),
                "a" * 64,
                FUNCTION_PROFILE,
            ),
        ),
        sandbox=BubblewrapSessionSandbox(platform),
    )
    assert (await host.launch(value)).text == "structured fixture"
    provider, call = platform.calls[0]
    assert provider.name == "opencode-openai" and call["expected_executable"] == binary
    assert call["command"][:5] == ("opencode", "run", "--format", "json", "--agent")
    staged = {str(item.relative_path): item.content for item in call["authorities"]}
    assert set(staged) == {
        ".local/share/opencode/auth.json",
        ".config/opencode/opencode.json",
        "system.md",
    }
    config = json.loads(staged[".config/opencode/opencode.json"])
    assert config["mcp"] == {} and config["permission"] == {"*": "deny"}
    assert config["tools"] == {"*": False} and config["plugin"] == []
    assert config["agent"]["model_function"]["tools"] == {"*": False}
    assert config["agent"]["model_function"]["permission"] == {"*": "deny"}
    assert config["agent"]["model_function"]["steps"] == 1
    assert config["enabled_providers"] == ["openai"] and config["autoupdate"] is False
    assert not any(b"broker" in content for content in staged.values())
    platform.output = b'{"type":"tool_use","part":{"type":"tool","tool":"shell"}}\n'
    with pytest.raises(Refused, match="invalid_runtime_output"):
        await host.launch(value)
    wire = value.to_wire()
    assert ModelFunctionRequest.from_wire(wire) == value
    for field in ("broker_token", "goal_id", "env", "proof"):
        with pytest.raises(Refused):
            ModelFunctionRequest.from_wire({**wire, field: "forbidden"})
