"""Shared session-host fixtures: synthetic PKI only, never real identities."""

import hashlib
import ssl
import subprocess

import pytest


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
