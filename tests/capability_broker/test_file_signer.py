"""Ephemeral file producer -> actual broker signer; no mounted material/activation."""

import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from butlers.capability_broker.assertions import AssertionSigner, verify_assertion
from butlers.capability_broker.contracts import BrokerRefused, Reason
from tests.capability_broker.test_broker import build

pytestmark = pytest.mark.unit


def write_key(path, key):
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o400)


@pytest.mark.asyncio
async def test_file_snapshot_signs_actual_broker_dispatch_and_restart_rechecks_match(tmp_path):
    key, next_key = ed25519.Ed25519PrivateKey.generate(), ed25519.Ed25519PrivateKey.generate()
    path = tmp_path / "goal-signing-key"
    write_key(path, key)
    signer = AssertionSigner.from_file(
        enabled=True, path=path, kid="current", verification_keys={"current": key.public_key()}
    )
    broker, connection, _, domain, token = build()
    broker.signer = signer
    result = await broker.call(connection.goal, token, "health__read", {"amount": 1})
    assert result.status == "returned" and len(domain.calls) == 1
    reservation, args, assertion, _ = domain.calls[0]
    verified = verify_assertion(
        assertion,
        keys={"current": key.public_key()},
        audience="health",
        capability="read",
        arguments=args,
        now=int(broker.clock()),
        claim_call_id=lambda call, expiry: call == reservation.call_id,
    )
    assert verified["args_digest"] == reservation.args_digest
    path.chmod(0o600)
    write_key(path, next_key)
    # Rotation is restart-driven: the existing in-memory snapshot is unaffected.
    assert signer.private_key.public_key().public_bytes_raw() == key.public_key().public_bytes_raw()
    with pytest.raises(BrokerRefused) as rejected:
        AssertionSigner.from_file(
            enabled=True, path=path, kid="current", verification_keys={"current": key.public_key()}
        )
    assert rejected.value.reason is Reason.UNAVAILABLE
    replacement = AssertionSigner.from_file(
        enabled=True,
        path=path,
        kid="next",
        verification_keys={"current": key.public_key(), "next": next_key.public_key()},
    )
    assert replacement.kid == "next"
    assert "private_key" not in repr(signer)


def test_disabled_file_signer_never_opens_or_validates_material(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("OFF touched filesystem")

    monkeypatch.setattr(os, "open", forbidden)
    assert AssertionSigner.from_file(enabled=False, path=tmp_path / "absent") is None


@pytest.mark.parametrize("condition", ["missing", "writable", "symlink", "fifo", "oversize"])
def test_unsafe_file_refuses_without_fallback(tmp_path, condition):
    key = ed25519.Ed25519PrivateKey.generate()
    path = tmp_path / "key"
    if condition == "symlink":
        target = tmp_path / "target"
        write_key(target, key)
        path.symlink_to(target)
    elif condition == "fifo":
        os.mkfifo(path, 0o400)
    elif condition != "missing":
        write_key(path, key)
        if condition == "writable":
            path.chmod(0o600)
        elif condition == "oversize":
            path.chmod(0o600)
            path.write_bytes(b"x" * 8193)
            path.chmod(0o400)
    with pytest.raises(BrokerRefused) as rejected:
        AssertionSigner.from_file(
            enabled=True, path=path, kid="current", verification_keys={"current": key.public_key()}
        )
    assert rejected.value.reason is Reason.UNAVAILABLE
    assert str(path) not in str(rejected.value)


@pytest.mark.parametrize("condition", ["wrong_algorithm", "malformed", "unrecognized_kid"])
def test_file_key_contract_refuses_before_signer_construction(tmp_path, condition):
    key = ed25519.Ed25519PrivateKey.generate()
    path = tmp_path / "key"
    material = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if condition == "wrong_algorithm"
        else key
    )
    write_key(path, material)
    if condition == "malformed":
        path.chmod(0o600)
        path.write_bytes(b"not a private key")
        path.chmod(0o400)
    with pytest.raises(BrokerRefused) as rejected:
        AssertionSigner.from_file(
            enabled=True,
            path=path,
            kid="missing" if condition == "unrecognized_kid" else "current",
            verification_keys={"current": key.public_key()},
        )
    assert rejected.value.reason is Reason.UNAVAILABLE
