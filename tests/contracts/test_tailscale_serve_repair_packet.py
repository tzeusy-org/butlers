"""Contract for the sanitized Tailscale Serve repair packet.

The packet is deliberately an authorization-gated operational aid, not an
executable runbook.  These checks keep its repository facts, placeholders,
strict-TLS acceptance criteria, and no-command boundary from drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).parent.parent.parent
_PACKET_PATH = _REPO_ROOT / "docs" / "operations" / "tailscale-serve-repair-packet.md"
_OPERATIONS_INDEX_PATH = _REPO_ROOT / "docs" / "operations" / "index.md"


def _packet_text() -> str:
    assert _PACKET_PATH.is_file(), f"Repair packet not found at {_PACKET_PATH}"
    return _PACKET_PATH.read_text(encoding="utf-8")


def test_packet_is_authority_gated_and_covers_each_repair_boundary() -> None:
    """The packet must separate evidence capture from any authorized repair."""
    text = _packet_text()

    required_sections = (
        "## 1. Packet Boundary and Authorities",
        "## 2. Repository Facts Versus Live Evidence",
        "## 3. Unauthorized Read-Only Capture",
        "## 4. Narrow Authorized Mutation",
        "## 5. Strict-TLS Verification",
        "## 6. Abort Criteria",
        "## 7. Rollback",
        "## 8. Full Serve Reconstruction: Escalation Only",
    )
    required_placeholders = (
        "[HOST]",
        "[APPROVED_WINDOW]",
        "[AUTHORIZED_OPERATOR]",
        "[OFF_HOST_VERIFIER]",
    )

    for section in required_sections:
        assert section in text, f"Missing repair-boundary section: {section}"
    for placeholder in required_placeholders:
        assert placeholder in text, f"Missing authorization placeholder: {placeholder}"

    assert "full serve reconstruction" in text.casefold()
    assert "new, explicit authorization" in text


def test_packet_distinguishes_read_only_observation_from_mutation_authority() -> None:
    """A pre-mutation observation must never imply access-control bypass."""
    text = _packet_text()
    normalized = " ".join(text.casefold().split())

    assert "[READ_ONLY_OBSERVATION_AUTHORIZATION]" in text
    assert "[MUTATION_AUTHORIZATION]" in text
    assert "no mutation authority" in normalized
    assert "not permission to bypass ordinary access control" in normalized


def test_packet_preserves_the_recorded_certificate_data_plane_boundary() -> None:
    """Known-good mappings and targets must not become a repair target."""
    text = _packet_text()
    normalized = " ".join(text.casefold().split())

    for category in (
        "tailscale status",
        "serve mappings",
        "listener ownership",
        "certificate chain",
        "local targets",
    ):
        assert category in normalized, f"Missing sanitized capture category: {category}"

    assert "recorded diagnosis" in normalized
    assert "healthy serve mappings and local targets" in normalized
    assert "port 443 presented the default certificate" in normalized
    assert "narrow certificate/data-plane hypothesis" in normalized
    assert "preserve existing serve mappings and local targets" in normalized
    assert "one reversible mapping-level correction" not in normalized
    assert "full serve reconstruction" in normalized
    assert "separately reviewed" in normalized


def test_packet_requires_strict_tls_for_the_supported_route_set() -> None:
    """Every in-scope route must be verified without a TLS bypass."""
    text = _packet_text()

    for route in (
        "https://[HOST]/butlers-dev/",
        "https://[HOST]/butlers-dev-api/api/health",
        "https://[HOST]/butlers-api/api/health",
    ):
        assert route in text, f"Missing strict-TLS acceptance route: {route}"

    normalized = text.casefold()
    assert "strict tls" in normalized
    assert "certificate validation bypass" in normalized
    assert "if the production api is present" in normalized


def test_packet_contains_no_executable_host_or_network_instructions() -> None:
    """The repository packet must stay sanitized and non-operational."""
    text = _packet_text()
    normalized = text.casefold()

    assert "```" not in text, "The packet must not carry executable command blocks"
    assert not re.search(r"https://(?!\[HOST\])", text)
    assert ".ts.net" not in normalized
    assert "localhost" not in normalized

    for forbidden in (
        "sudo ",
        "ssh ",
        "curl ",
        "tailscale serve --",
        "tailscale up",
        "tailscale set",
        "systemctl ",
        "docker compose ",
        "openssl ",
    ):
        assert forbidden not in normalized, f"Packet must not prescribe {forbidden!r}"


def test_operations_index_links_to_the_packet() -> None:
    """Operators can find the packet from the established operations index."""
    index_text = _OPERATIONS_INDEX_PATH.read_text(encoding="utf-8")
    assert "[Tailscale Serve Repair Packet](tailscale-serve-repair-packet.md)" in index_text
