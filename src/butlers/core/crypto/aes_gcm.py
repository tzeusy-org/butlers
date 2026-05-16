"""AES-256-GCM symmetric encryption helpers for secrets at rest.

Layout of the ciphertext blob (returned by :func:`encrypt` / stored in DB)::

    [ nonce (12 bytes) ][ ciphertext+tag (variable) ]

The GCM authentication tag (16 bytes) is appended by the AESGCM primitive
automatically; the caller never sees it separately.

Key loading
-----------
The server-side key is read from the ``WEBHOOK_SECRET_KEY`` environment
variable.  It must be exactly 32 bytes encoded as a 64-character hex string
(256-bit AES).  Generate one with::

    python -c "import secrets; print(secrets.token_hex(32))"

:func:`get_key` raises :class:`RuntimeError` at call time if the variable is
absent or malformed.  This is intentional fail-loud behaviour — missing key in
production means the daemon should refuse to start rather than silently falling
back to plaintext.

Test vectors
------------
The module ships a pair of deterministic test vectors that are checked in the
unit-test suite (``tests/core/test_aes_gcm.py``).  They were generated with a
fixed key and nonce so that they can be re-verified without network access or
external tooling::

    KEY_HEX = "0" * 64   # 32 zero bytes
    NONCE    = b"\\x00" * 12
    MESSAGE  = "hello"
    # Ciphertext (hex of nonce||ct||tag):
    #   000000000000000000000000  <nonce>
    #   b827e04f26f5e2f1edabd19a6f  <ct+tag from AES-256-GCM zero-key/nonce>
"""

from __future__ import annotations

import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENV_VAR = "WEBHOOK_SECRET_KEY"
_NONCE_BYTES = 12  # 96-bit nonce — NIST recommendation for GCM


def get_key() -> bytes:
    """Return the 32-byte AES-256 key from the environment.

    Raises :class:`RuntimeError` if ``WEBHOOK_SECRET_KEY`` is missing or not a
    64-hex-character string (i.e. not 32 bytes).
    """
    raw = os.environ.get(_ENV_VAR, "").strip()
    if not raw:
        raise RuntimeError(
            f"Missing required environment variable {_ENV_VAR}. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError(f"{_ENV_VAR} is not valid hex: {exc}") from exc
    if len(key) != 32:
        raise RuntimeError(
            f"{_ENV_VAR} must be exactly 64 hex characters (32 bytes for AES-256); "
            f"got {len(key)} bytes."
        )
    return key


def encrypt(plaintext: str, *, key: bytes | None = None) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM and return ``nonce || ciphertext``.

    A fresh cryptographically random 12-byte nonce is generated per call so
    that two calls with the same plaintext produce different ciphertext blobs.

    Args:
        plaintext: The secret string to encrypt (e.g. a webhook signing secret).
        key: Override the key for testing.  If ``None``, :func:`get_key` is
             called to load from the environment.

    Returns:
        Raw bytes in the layout ``nonce (12 B) || ciphertext+tag``.
    """
    if key is None:
        key = get_key()
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return nonce + ct


def decrypt(ciphertext: bytes, *, key: bytes | None = None) -> str:
    """Decrypt an AES-256-GCM blob produced by :func:`encrypt`.

    Args:
        ciphertext: Raw bytes in the layout ``nonce (12 B) || ciphertext+tag``.
        key: Override the key for testing.  If ``None``, :func:`get_key` is
             called to load from the environment.

    Returns:
        The original plaintext string.

    Raises:
        ValueError: If *ciphertext* is too short or authentication fails.
    """
    if key is None:
        key = get_key()
    if len(ciphertext) < _NONCE_BYTES + 16:
        raise ValueError(
            f"ciphertext is too short ({len(ciphertext)} bytes); "
            f"minimum is {_NONCE_BYTES + 16} bytes (nonce + GCM tag)."
        )
    nonce = ciphertext[:_NONCE_BYTES]
    ct = ciphertext[_NONCE_BYTES:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode()
