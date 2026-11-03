"""Real local TLS handshakes, synthetic PKI and content-blind host controls."""

import asyncio
import base64
import hashlib
import json
import ssl
import struct
import subprocess
from uuid import uuid4

import pytest

from butlers.session_host.control import LocalControl, local_command
from butlers.session_host.lifecycle import InvocationOwner


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic-control-pki")

    def run(*args):
        result = subprocess.run(["openssl", *args], capture_output=True, timeout=15)
        assert result.returncode == 0, "synthetic certificate generation failed"

    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=Synthetic Control CA",
        "-keyout",
        str(root / "ca.key"),
        "-out",
        str(root / "ca.crt"),
    )
    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=untrusted-runner",
        "-addext",
        "extendedKeyUsage=clientAuth",
        "-keyout",
        str(root / "outsider.key"),
        "-out",
        str(root / "outsider.crt"),
    )
    for name, purpose in (
        ("server", "serverAuth"),
        ("runner", "clientAuth"),
        ("other", "clientAuth"),
        ("reflection", "serverAuth,clientAuth"),
    ):
        run(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            str(root / f"{name}.key"),
            "-out",
            str(root / f"{name}.csr"),
        )
        extension = root / f"{name}.ext"
        extension.write_text(f"extendedKeyUsage={purpose}\nsubjectAltName=DNS:host.test\n")
        run(
            "x509",
            "-req",
            "-in",
            str(root / f"{name}.csr"),
            "-CA",
            str(root / "ca.crt"),
            "-CAkey",
            str(root / "ca.key"),
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            str(extension),
            "-out",
            str(root / f"{name}.crt"),
        )
    for key in root.glob("*.key"):
        key.chmod(0o600)

    def pin(name):
        cert = ssl.PEM_cert_to_DER_cert((root / f"{name}.crt").read_text())
        return hashlib.sha256(cert).hexdigest()

    def server(name="server"):
        return dict(
            certificate_pem=(root / f"{name}.crt").read_bytes(),
            private_key_pem=(root / f"{name}.key").read_bytes(),
            client_ca_pem=(root / "ca.crt").read_bytes(),
        )

    def client(name="runner"):
        context = ssl.create_default_context(cafile=str(root / "ca.crt"))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        if name:
            context.load_cert_chain(root / f"{name}.crt", root / f"{name}.key")
        return context

    return server, client, pin


def launch():
    goal = uuid4()
    return dict(
        version=1,
        operation="launch",
        invocation_id=str(uuid4()),
        goal_id=str(goal),
        endpoint=f"https://broker.test/goals/{goal}/mcp",
        token=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode(),
        runtime="synthetic",
        model="synthetic",
        credential=base64.b64encode(b"SYNTHETIC_PRIVATE_CREDENTIAL").decode(),
        prompt="SYNTHETIC_PRIVATE_PROMPT",
        timeout_s=2,
    )


class Domain:
    def __init__(self, *, block=False, settled=True):
        self.entered = asyncio.Event()
        self.block = block
        self.settled = settled
        self.material = None

    async def execute(self, material):
        self.material = material
        assert (
            material.directory / "model-credential"
        ).read_bytes() == b"SYNTHETIC_PRIVATE_CREDENTIAL"
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        return b"SYNTHETIC_PRIVATE_RESULT"

    async def settle(self):
        return self.settled


@pytest.fixture
def channel(tmp_path, pki):
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root, cleanup_timeout_s=0.2)
    server, client, pin = pki

    def build(domain):
        return LocalControl(
            owner=owner,
            factory=lambda runtime, model: domain,
            **server(),
            caller_sha256=pin("runner"),
            server_sha256=pin("server"),
            broker_origin="https://broker.test",
        )

    async def send(port, command, *, name="runner", hostname="host.test", server_pin=None):
        return await local_command(
            port,
            context=client(name),
            server_hostname=hostname,
            server_sha256=server_pin or pin("server"),
            command=command,
        )

    return build, send, owner, root


async def test_mtls_launch_cleanup_and_replay_refusal(channel, caplog):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    command = launch()
    try:
        reply = await send(port, command)
        assert reply == {"status": "completed"}
        assert domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert await send(port, command) == {"status": "refused"}
        assert "SYNTHETIC_PRIVATE" not in json.dumps(reply) + caplog.text
    finally:
        await host.close()


@pytest.mark.parametrize(
    "name,hostname,pin",
    [
        (None, "host.test", None),
        ("outsider", "host.test", None),
        ("other", "host.test", None),
        ("reflection", "host.test", None),
        ("runner", "wrong.test", None),
        ("runner", "host.test", "0" * 64),
    ],
)
async def test_unauthorized_identity_hostname_and_server_pin_never_launch(
    channel, name, hostname, pin
):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    try:
        try:
            reply = await send(port, launch(), name=name, hostname=hostname, server_pin=pin)
            assert reply == {"status": "refused"}
        except (ssl.SSLError, ConnectionError, asyncio.IncompleteReadError, ValueError):
            pass
        assert not domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


@pytest.mark.parametrize("settled", [True, False])
async def test_authenticated_revoke_waits_for_cleanup_or_quarantines(channel, settled):
    build, send, owner, root = channel
    domain = Domain(block=True, settled=settled)
    host = build(domain)
    port = await host.start()
    command = launch()
    pending = asyncio.create_task(send(port, command))
    try:
        await asyncio.wait_for(domain.entered.wait(), 2)
        revoke = dict(version=1, operation="revoke", invocation_id=command["invocation_id"])
        assert await send(port, revoke) == {"status": "revoked" if settled else "quarantined"}
        assert await pending == {"status": "refused"}
        assert owner.healthy is settled
        assert bool(list(root.iterdir())) is not settled
        assert await send(port, command) == {"status": "refused"}
        assert await send(port, revoke) == {"status": "refused"}
    finally:
        await host.close()


@pytest.mark.parametrize("mutation", ["origin", "version", "unknown", "credential", "prompt"])
async def test_authenticated_malformed_launch_has_no_staging(channel, mutation):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    command = launch()
    if mutation == "origin":
        command["endpoint"] += "?authority=other"
    elif mutation == "version":
        command["version"] = True
    elif mutation == "unknown":
        command["caller_sha256"] = "0" * 64
    elif mutation == "credential":
        command["credential"] = "A" * 87385
    else:
        command["prompt"] = "x" * (128 * 1024 + 1)
    try:
        assert await send(port, command) == {"status": "refused"}
        assert not domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


async def test_oversized_frame_and_duplicate_keys_refuse_before_launch(channel, pki):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    try:
        for raw in (struct.pack("!I", 280 * 1024 + 1), struct.pack("!I", 13) + b'{"a":1,"a":2}'):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=pki[1](), server_hostname="host.test"
            )
            writer.write(raw)
            await writer.drain()
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            assert json.loads(await reader.readexactly(size)) == {"status": "refused"}
            writer.close()
            await writer.wait_closed()
        assert not domain.entered.is_set() and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


def test_reflected_server_identity_is_not_an_admissible_runner(channel, pki):
    from butlers.session_host.contracts import Refused

    _, _, owner, _ = channel
    with pytest.raises(Refused, match="invalid_control_identity"):
        LocalControl(
            owner=owner,
            factory=lambda runtime, model: Domain(),
            **pki[0](),
            caller_sha256=pki[2]("server"),
            server_sha256=pki[2]("server"),
            broker_origin="https://broker.test",
        )


async def test_cancelling_control_handler_still_settles_owned_launch(channel):
    build, _, owner, root = channel
    domain = Domain(block=True)
    host = build(domain)
    task = asyncio.create_task(host._command(launch()))
    await asyncio.wait_for(domain.entered.wait(), 2)
    task.cancel()
    assert await asyncio.wait_for(task, 2) == {"status": "refused"}
    assert owner.healthy and not list(root.iterdir()) and host._active is None
    await host.close()


@pytest.mark.parametrize(
    "identity,correct_pin", [("server", False), ("reflection", False), ("reflection", True)]
)
async def test_actual_installed_leaf_rejects_false_pin_and_reflection(
    channel, pki, identity, correct_pin
):
    from butlers.session_host.contracts import Refused

    _, _, owner, root = channel
    domain = Domain()
    host = None
    try:
        with pytest.raises(Refused, match="invalid_control_identity"):
            host = LocalControl(
                owner=owner,
                factory=lambda runtime, model: domain,
                **pki[0](identity),
                caller_sha256=pki[2]("reflection" if identity == "reflection" else "runner"),
                server_sha256=pki[2](identity) if correct_pin else "0" * 64,
                broker_origin="https://broker.test",
            )
            port = await host.start()
            reply = await local_command(
                port,
                context=pki[1]("reflection" if identity == "reflection" else "runner"),
                server_hostname="host.test",
                server_sha256=pki[2](identity),
                command=launch(),
            )
            assert reply == {"status": "completed"} and domain.entered.is_set()
        assert not domain.entered.is_set() and not list(root.iterdir())
    finally:
        if host:
            await host.close()
