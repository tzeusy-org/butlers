"""The SCRAM-SHA-256 verifier helper: PostgreSQL's stored format, stdin only, no leaks."""

import base64
import hashlib
import hmac
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scram_verifier.py"
SPEC = importlib.util.spec_from_file_location("scram_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
scram = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scram)
ITERATIONS, verifier = scram.ITERATIONS, scram.verifier
FORMAT = re.compile(
    r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/]{22}==\$[A-Za-z0-9+/]{43}=:[A-Za-z0-9+/]{43}=$"
)


def test_the_verifier_has_postgresqls_stored_format() -> None:
    assert ITERATIONS == 4096
    assert FORMAT.match(verifier("correct horse"))


def test_a_known_salt_gives_the_rfc_5802_derivation() -> None:
    salt = b"\x00" * 16
    result = verifier("pencil", salt=salt)
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
    stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    expected = (
        f"SCRAM-SHA-256$4096:{base64.b64encode(salt).decode()}"
        f"${base64.b64encode(stored).decode()}:{base64.b64encode(server).decode()}"
    )
    assert result == expected


def test_each_verifier_has_a_fresh_salt() -> None:
    assert verifier("same") != verifier("same")


@pytest.mark.parametrize("bad", ["", "naïve", "two\nlines", "nul\x00byte"])
def test_unusable_passwords_are_refused(bad) -> None:
    with pytest.raises(ValueError):
        verifier(bad)


def test_the_script_reads_stdin_prints_only_the_verifier_and_never_the_password() -> None:
    secret = "s3cr3t-hex-0123456789abcdef"
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], input=secret + "\n", text=True, capture_output=True
    )
    assert done.returncode == 0 and FORMAT.match(done.stdout.strip())
    assert secret not in done.stdout and secret not in done.stderr


def test_the_script_refuses_an_empty_stdin() -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], input="", text=True, capture_output=True
    )
    assert done.returncode == 2 and done.stdout == ""
