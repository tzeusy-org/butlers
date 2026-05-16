"""Unit tests for AES-256-GCM webhook secret encryption helpers.

Tests:
- Round-trip encrypt/decrypt returns original plaintext.
- Two encryptions of the same plaintext differ (random nonce).
- decrypt raises ValueError on tampered/truncated ciphertext.
- get_key raises RuntimeError when env var is missing.
- get_key raises RuntimeError when env var is wrong length.
- get_key raises RuntimeError when env var is not valid hex.
- Test vectors: deterministic encrypt output can be decrypted.
"""

from __future__ import annotations

import pytest

from butlers.core.crypto import aes_gcm

pytestmark = pytest.mark.unit

# A fixed 32-byte key expressed as 64 hex chars.
_TEST_KEY = bytes(range(32))  # 00 01 02 … 1f
_TEST_KEY_HEX = _TEST_KEY.hex()


# ---------------------------------------------------------------------------
# get_key
# ---------------------------------------------------------------------------


def test_get_key_missing_env_raises(monkeypatch):
    """Missing WEBHOOK_SECRET_KEY raises RuntimeError."""
    monkeypatch.delenv("WEBHOOK_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET_KEY"):
        aes_gcm.get_key()


def test_get_key_empty_env_raises(monkeypatch):
    """Empty WEBHOOK_SECRET_KEY raises RuntimeError."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", "")
    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET_KEY"):
        aes_gcm.get_key()


def test_get_key_wrong_length_raises(monkeypatch):
    """16-byte key (32 hex chars) is rejected — must be 32 bytes."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", "00" * 16)
    with pytest.raises(RuntimeError, match="32 bytes"):
        aes_gcm.get_key()


def test_get_key_invalid_hex_raises(monkeypatch):
    """Non-hex value raises RuntimeError."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", "zz" * 32)
    with pytest.raises(RuntimeError, match="not valid hex"):
        aes_gcm.get_key()


def test_get_key_valid(monkeypatch):
    """Valid 64-hex-char env var returns 32 bytes."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", _TEST_KEY_HEX)
    key = aes_gcm.get_key()
    assert key == _TEST_KEY
    assert len(key) == 32


# ---------------------------------------------------------------------------
# encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


def test_round_trip_basic():
    """encrypt followed by decrypt returns the original string."""
    plaintext = "my-webhook-secret-123"
    blob = aes_gcm.encrypt(plaintext, key=_TEST_KEY)
    assert aes_gcm.decrypt(blob, key=_TEST_KEY) == plaintext


def test_round_trip_empty_string():
    """Empty string round-trips correctly."""
    blob = aes_gcm.encrypt("", key=_TEST_KEY)
    assert aes_gcm.decrypt(blob, key=_TEST_KEY) == ""


def test_round_trip_unicode():
    """Unicode plaintext round-trips correctly."""
    plaintext = "sécret-123-éàü"
    blob = aes_gcm.encrypt(plaintext, key=_TEST_KEY)
    assert aes_gcm.decrypt(blob, key=_TEST_KEY) == plaintext


def test_nonce_is_random():
    """Two encryptions of the same plaintext produce different blobs."""
    plaintext = "same-secret"
    blob1 = aes_gcm.encrypt(plaintext, key=_TEST_KEY)
    blob2 = aes_gcm.encrypt(plaintext, key=_TEST_KEY)
    assert blob1 != blob2


def test_blob_length():
    """Blob is nonce (12) + ciphertext + GCM tag (16), so min 28 + utf8 bytes."""
    plaintext = "abc"
    blob = aes_gcm.encrypt(plaintext, key=_TEST_KEY)
    # 12 nonce + 3 plaintext + 16 tag = 31
    assert len(blob) == 12 + len(plaintext.encode()) + 16


# ---------------------------------------------------------------------------
# decrypt error paths
# ---------------------------------------------------------------------------


def test_decrypt_truncated_raises():
    """Blob shorter than 13 bytes raises ValueError."""
    with pytest.raises(ValueError, match="too short"):
        aes_gcm.decrypt(b"\x00" * 5, key=_TEST_KEY)


def test_decrypt_tampered_tag_raises():
    """Bit-flip in the authentication tag raises an error (invalid tag)."""
    blob = aes_gcm.encrypt("secret", key=_TEST_KEY)
    # Flip last byte (part of the GCM tag)
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    with pytest.raises(Exception):  # cryptography raises InvalidTag
        aes_gcm.decrypt(bytes(tampered), key=_TEST_KEY)


def test_decrypt_wrong_key_raises():
    """Decrypting with a different key raises an error."""
    blob = aes_gcm.encrypt("secret", key=_TEST_KEY)
    other_key = bytes(reversed(range(32)))
    with pytest.raises(Exception):
        aes_gcm.decrypt(blob, key=other_key)


# ---------------------------------------------------------------------------
# Test vector — deterministic
# ---------------------------------------------------------------------------


def test_vector_deterministic():
    """Fixed nonce + key + plaintext produces a stable, verifiable ciphertext.

    This is a pure regression vector: encrypt is normally randomised; here we
    patch the nonce to zero so the output is deterministic.
    """
    import unittest.mock

    zero_nonce = b"\x00" * 12
    plaintext = "hello"
    key = bytes(32)  # 32 zero bytes

    with unittest.mock.patch(
        "butlers.core.crypto.aes_gcm.secrets.token_bytes", return_value=zero_nonce
    ):
        blob = aes_gcm.encrypt(plaintext, key=key)

    # Verify layout: first 12 bytes are the (zero) nonce.
    assert blob[:12] == zero_nonce

    # Round-trip still works with the fixed key.
    assert aes_gcm.decrypt(blob, key=key) == plaintext
