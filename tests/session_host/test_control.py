"""Real loopback TLS software controls, never provider or deployment evidence."""

import asyncio
import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from butlers.session_host.control import (
    CONTROL_IDENTITY,
    HOST_IDENTITY,
    ControlClient,
    ControlService,
    TLSFiles,
    read_frame,
    write_frame,
)
from butlers.session_host.launch import LaunchResult, Refused
from tests.session_host.test_launch import request

pytestmark = pytest.mark.unit


@pytest.fixture
def certificates(tmp_path):
    # Ephemeral fixture CA/keys, not operational credentials or trusted proofs.
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    def issue(name, identity, usage):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.UniformResourceIdentifier(identity)]
                ),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        cert_path, key_path = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        return TLSFiles(ca_path, cert_path, key_path)

    class Certificates(tuple):
        pass

    result = Certificates(
        (
            issue("host", HOST_IDENTITY, ExtendedKeyUsageOID.SERVER_AUTH),
            issue("runner", CONTROL_IDENTITY, ExtendedKeyUsageOID.CLIENT_AUTH),
            issue("other", "spiffe://butlers/dashboard", ExtendedKeyUsageOID.CLIENT_AUTH),
        )
    )
    result.issue = issue
    return result


async def test_authenticated_control_stages_only_closed_request_and_binds_reply(certificates):
    server_tls, runner_tls, wrong_tls = certificates

    class Host:
        calls = []

        async def launch(self, value):
            self.calls.append(value)
            return LaunchResult("fixture result")

    host = Host()
    service = ControlService(host)
    server = await asyncio.start_server(
        service.handle, "127.0.0.1", 0, ssl=server_tls.server_context(), ssl_handshake_timeout=1
    )
    port = server.sockets[0].getsockname()[1]
    try:
        client = ControlClient("localhost", port, runner_tls)
        value = request()
        assert (await client.launch(value)).text == "fixture result"
        assert host.calls == [value]
        # Same trusted CA and hostname are insufficient: role is authenticated
        # from the real TLS peer certificate before any request is staged.
        with pytest.raises(Refused):
            await ControlClient("localhost", port, wrong_tls).launch(request())
        assert host.calls == [value]
        reader, writer = await asyncio.open_connection(
            "localhost", port, ssl=runner_tls.client_context(), server_hostname="localhost"
        )
        try:
            await write_frame(writer, {**request().to_wire(), "env": {"unsafe": "fixture"}})
            assert (await read_frame(reader))["type"] == "refused"
            assert host.calls == [value]
        finally:
            writer.close()
            await writer.wait_closed()
        from butlers.session_host.model_function import ModelFunctionRequest

        function = ModelFunctionRequest(
            value.invocation_id,
            "benchmark",
            "opencode",
            "openai/fixture-model",
            "openai",
            "fixture input",
            "fixture contract",
            b'{"OPENAI_API_KEY":"fixture-only"}',
            10,
        )
        assert (await client.launch(function)).text == "fixture result"
        assert host.calls == [value, function]
    finally:
        server.close()
        await server.wait_closed()
        await service.close()


async def test_control_bounds_frames_before_json_and_refuses_uncorrelated_result(certificates):
    reader = asyncio.StreamReader()
    reader.feed_data((262145).to_bytes(4, "big"))
    with pytest.raises(Refused, match="invalid_control_frame"):
        await read_frame(reader)
    reader = asyncio.StreamReader()
    payload = b'{"a":1,"a":2}'
    reader.feed_data(len(payload).to_bytes(4, "big") + payload)
    with pytest.raises(Refused):
        await read_frame(reader)
    server_tls, runner_tls, _ = certificates

    async def wrong_reply(reader, writer):
        try:
            await read_frame(reader)
            await write_frame(
                writer, {"type": "completed", "invocation_id": "other", "text": "unsafe"}
            )
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(
        wrong_reply, "127.0.0.1", 0, ssl=server_tls.server_context()
    )
    try:
        with pytest.raises(Refused, match="invalid_control_result"):
            await ControlClient("localhost", server.sockets[0].getsockname()[1], runner_tls).launch(
                request()
            )
    finally:
        server.close()
        await server.wait_closed()


async def test_client_loss_cancels_one_owned_launch_and_repeated_close_joins_cleanup(certificates):
    server_tls, runner_tls, _ = certificates
    started, cancelled, release, settled = (asyncio.Event() for _ in range(4))
    cancellation_count = 0

    class Host:
        async def launch(self, value):
            nonlocal cancellation_count
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_count += 1
                cancelled.set()
                await release.wait()
                settled.set()
                raise

    service = ControlService(Host())
    server = await asyncio.start_server(
        service.handle, "127.0.0.1", 0, ssl=server_tls.server_context()
    )
    writer = None
    try:
        async with asyncio.timeout(5):
            _, writer = await asyncio.open_connection(
                "localhost",
                server.sockets[0].getsockname()[1],
                ssl=runner_tls.client_context(),
                server_hostname="localhost",
            )
            await write_frame(writer, request().to_wire())
            await started.wait()
            writer.close()
            await writer.wait_closed()
            async with asyncio.timeout(1):
                await cancelled.wait()
            # Shutdown must join the SAME launch already cancelled by peer loss.
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert not settled.is_set() and not closing.done()
            release.set()
            await closing
            await service.close()
            assert settled.is_set() and cancellation_count == 1 and not service._active
    finally:
        release.set()
        if writer is not None:
            writer.close()
        server.close()
        await server.wait_closed()
        await service.close()
