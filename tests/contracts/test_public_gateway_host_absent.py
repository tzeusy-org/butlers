"""The public MCP gateway's real hostname stays out of this public repository (bu-y66o00.64).

The live audience is a site value, never committed. Tests and golden vectors use the reserved
``mcp.example.com``; docs say "the gateway host" or ``<gateway-host>``. The guard scans every
tracked file for the hostname, case-insensitively, and for its base64 forms, which is how it
would hide inside a signed fixture's compact JWS. The needle is assembled from parts so this
file does not match itself.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HOST = ".".join(("mcp", "tze", "how"))


def _base64_needles(text: str) -> set[str]:
    """The runs of base64 characters that depend only on ``text``, at each byte alignment."""
    needles = set()
    for pad in range(3):
        raw = b"\0" * pad + text.encode()
        whole = (len(raw) // 3) * 3
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            encoded = encode(raw[:whole]).decode()
            needles.add(encoded[4:] if pad else encoded)
    return needles


def _git_grep(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "grep", "-n", "-I", "-F", *args], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode in (0, 1), result.stderr
    return result.stdout.splitlines()


def test_no_tracked_file_names_the_gateway_host() -> None:
    hits = _git_grep("-i", "-e", HOST)
    assert not hits, (
        "The public MCP gateway hostname is not committed; use mcp.example.com in tests "
        "and 'the gateway host' or <gateway-host> in docs:\n" + "\n".join(hits)
    )


def test_no_tracked_file_carries_the_gateway_host_base64_encoded() -> None:
    needles = sorted(_base64_needles(HOST))
    hits = _git_grep(*(arg for needle in needles for arg in ("-e", needle)))
    assert not hits, (
        "A tracked file carries the gateway hostname base64-encoded (a signed fixture?); "
        "regenerate it with the example host:\n" + "\n".join(hits)
    )


@pytest.mark.parametrize("prefix", ["", "/", "//", "https://"])
def test_the_base64_needles_match_an_encoded_url_at_every_alignment(prefix: str) -> None:
    encoded = base64.urlsafe_b64encode(f"{prefix}{HOST}/butlers".encode()).decode()
    assert any(needle in encoded for needle in _base64_needles(HOST))
