"""Isolated startup probe HTTP behavior and actual Helm render controls."""

from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import certifi
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[2]
_PROBE = _ROOT / "scripts/wait_for_butler_routes.py"


def _identity(*, omit: str | None = None, **changes) -> bytes:
    payload = {
        "schema_version": "butler.control.v1",
        "butler_name": "health",
        "boot_epoch": 1,
        "accepting_routes": True,
        **changes,
    }
    if omit is not None:
        payload.pop(omit)
    return json.dumps(payload).encode()


@dataclass
class _Response:
    body: bytes
    status: int = 200
    behavior: str = "normal"


@asynccontextmanager
async def _identity_server(responses: list[_Response], *, tls: ssl.SSLContext | None = None):
    """Serve a deterministic response sequence over a real loopback listener."""
    paths: list[str] = []
    tasks: set[asyncio.Task] = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            response = responses[min(len(paths), len(responses) - 1)]
            paths.append(request.split(b" ")[1].decode())
            if response.behavior == "stall":
                await asyncio.Event().wait()
            writer.write(
                f"HTTP/1.1 {response.status} Response\r\n"
                f"Content-Length: {len(response.body)}\r\n"
                "Location: /redirected\r\nConnection: close\r\n\r\n".encode()
            )
            if response.behavior == "trickle":
                for offset in range(0, len(response.body), 8):
                    writer.write(response.body[offset : offset + 8])
                    await writer.drain()
                    await asyncio.sleep(0.05)
            else:
                writer.write(response.body)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass  # The probe intentionally cancels requests at its deadline.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=tls)
    port = server.sockets[0].getsockname()[1]
    origin = f"http://127.0.0.1:{port}" if tls is None else f"https://localhost:{port}"
    try:
        yield f"{origin}/internal/control-plane/identity", paths
    finally:
        server.close()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()


async def _run_probe(url: str, *options: str):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(_PROBE),
        "--url",
        url,
        "--butler",
        "health",
        *options,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        return process.returncode, stdout.decode(), stderr.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_probe_requires_ready_identity_across_http_refusals(monkeypatch):
    # REQ-connector-google-health-016: HTTP success alone cannot release startup.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    invalid = [
        _Response(_identity(), status=503),
        _Response(_identity(), status=302),
        _Response(_identity(omit="accepting_routes")),
        _Response(_identity(omit="boot_epoch")),
        *[_Response(_identity(accepting_routes=value)) for value in (False, "true", 1, None)],
        *[_Response(_identity(boot_epoch=value)) for value in (0, -1, True, 1.5, "1", None)],
        _Response(_identity(butler_name="switchboard")),
        _Response(_identity(schema_version="butler.control.v2")),
        _Response(b"[true]"),
        _Response(b"synthetic-private-payload: invalid JSON"),
        _Response(_identity(padding="synthetic-private-payload" * 200)),
        _Response(_identity(), behavior="stall"),
        _Response(_identity(), behavior="trickle"),
    ]
    async with _identity_server([*invalid, _Response(_identity())]) as (url, paths):
        code, stdout, stderr = await _run_probe(
            url,
            "--timeout-seconds",
            "3",
            "--interval-seconds",
            "0.01",
            "--request-timeout-seconds",
            "0.25",
        )
    assert code == 0, stderr
    assert len(paths) >= len(invalid) + 1
    assert set(paths) == {"/internal/control-plane/identity"}  # No redirect followed.
    assert "confirmed" in stdout
    assert "synthetic-private-payload" not in stdout + stderr
    assert stderr == ""


async def test_probe_deadline_and_invalid_configuration_refuse_startup():
    # The default five-second poll must not outlive a shorter overall deadline.
    async with _identity_server([_Response(_identity(accepting_routes=False))]) as (url, paths):
        code, stdout, stderr = await _run_probe(url, "--timeout-seconds", "0.5")
        assert code == 1 and "deadline" in stderr
        assert paths == ["/internal/control-plane/identity"]
        assert "confirmed" not in stdout
        for invalid in ("0", "-1", "nan", "inf"):
            code, _, stderr = await _run_probe(url, "--timeout-seconds", invalid)
            assert code != 0 and "finite positive" in stderr
        assert paths == ["/internal/control-plane/identity"]


@pytest.fixture
def route_gate_tls(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Health test CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(identity)
        .issuer_name(identity)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert, private = tmp_path / "health-ca.crt", tmp_path / "health.key"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_3
    server.load_cert_chain(cert, private)
    return server, str(cert)


async def test_https_probe_verifies_health_before_accepting_readiness(route_gate_tls, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    tls, ca_file = route_gate_tls
    malformed_ca = Path(ca_file).with_name("bad-ca.crt")
    malformed_ca.write_text("synthetic malformed CA")
    responses = [_Response(_identity(accepting_routes=False)), _Response(_identity())]
    async with _identity_server(responses, tls=tls) as (url, paths):
        invalid_config = "route-gate: invalid transport configuration\n"
        deadline = "route-gate: startup deadline exceeded\n"
        for target, trust, diagnostic in (
            (url, (), invalid_config),
            (url, ("--ca-file", str(malformed_ca)), invalid_config),
            (url, ("--ca-file", certifi.where()), deadline),
            (url.replace("localhost", "127.0.0.1"), ("--ca-file", ca_file), deadline),
        ):
            code, stdout, stderr = await _run_probe(target, *trust, "--timeout-seconds", "0.7")
            assert code == 1 and stderr == diagnostic
            assert "confirmed" not in stdout
            assert paths == []

        code, stdout, stderr = await _run_probe(
            url, "--ca-file", ca_file, "--timeout-seconds", "3", "--interval-seconds", "0.01"
        )
        assert code == 0, stderr
        assert "confirmed" in stdout
        assert paths == ["/internal/control-plane/identity"] * 2


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("split", [False, True])
def test_google_health_target_gate_rendering_preserves_other_connectors(split):
    # REQ-connector-google-health-016: exercise Helm, not template source text.
    chart = _ROOT / "deploy/helm/butlers"
    result = subprocess.run(
        ["helm", "template", "butlers", str(chart),
         "--set", "image.tag=synthetic", "--set", "frontendImage.tag=synthetic",
         "--set", "database.host=postgres.invalid",
         "--set", f"butlersControl.enabled={str(split).lower()}"],
        check=False, capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    connectors = {
        doc["metadata"]["name"]: doc["spec"]["template"]["spec"]
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"].startswith("connector-")
    }
    values = yaml.safe_load((chart / "values.yaml").read_text())["connectors"]
    for name, config in values.items():
        if config.get("enabled") is False:
            continue
        pod = connectors[f"connector-{name}"]
        initializers = pod["initContainers"]
        expected = (["oauth-gate"] if config.get("oauth") else []) + ["wait-for-butlers-up"]
        if name == "google-health":
            expected += ["wait-for-health-routes"]
            gate = initializers[-1]
            assert gate["image"] == pod["containers"][0]["image"]
            assert gate["command"] == [
                "/usr/local/bin/python",
                "-I",
                "/app/scripts/runtime_clis/python_entrypoint.py",
                "python",
                "scripts/wait_for_butler_routes.py",
            ]
            assert gate["args"] == [
                "--url",
                "http://butlers-up:41103/internal/control-plane/identity",
                "--butler",
                "health",
            ]
        assert [item["name"] for item in initializers] == expected
        switchboard = "butlers-control" if split else "butlers-up"
        wait = next(item for item in initializers if item["name"] == "wait-for-butlers-up")
        assert f"http://{switchboard}:41100/health" in wait["args"][0]
        if config.get("oauth"):
            assert initializers[0]["command"][-1] == "scripts/oauth_gate.py"
