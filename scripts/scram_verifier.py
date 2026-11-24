#!/usr/bin/env python3
"""Print the PostgreSQL SCRAM-SHA-256 verifier for a password read from stdin.

``ALTER ROLE r PASSWORD 'SCRAM-SHA-256$...'`` stores the verifier as given, so the plaintext
never reaches the server, its logs or ``pg_stat_activity`` (and ``psql``'s ``\\password`` is no
help from a script: it prompts on the controlling terminal first). Format, RFC 5802/7677 as
PostgreSQL stores it::

    SCRAM-SHA-256$<iterations>:<salt b64>$<StoredKey b64>:<ServerKey b64>

with 4096 iterations of PBKDF2-HMAC-SHA256. The password is read from stdin only, never argv,
and is never printed. ASCII passwords only (no SASLprep); the provisioning script generates a
hex password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys

ITERATIONS = 4096
SALT_BYTES = 16
PREFIX = "SCRAM-SHA-256"


def verifier(password: str, *, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    if not password or not password.isascii() or "\n" in password or "\x00" in password:
        raise ValueError("password must be non-empty ASCII on one line")
    salt = os.urandom(SALT_BYTES) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"{PREFIX}${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def main() -> int:
    password = sys.stdin.read().rstrip("\n")
    try:
        print(verifier(password))
    except ValueError as exc:
        print(f"scram_verifier: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
