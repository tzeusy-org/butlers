"""Contracts for the Go toolchain used to build the WhatsApp bridge."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_GO_MINIMUM = "1.25.0"
UTIL_VERSION = "v0.9.6"
BUILDER_GO_VERSION = "1.25.11"
BUILDER_DIGEST = "bbb255b0e131db500cf0520adc97441d2260cf629c7fa7e39e025ddf53995a24"


def test_whatsapp_bridge_go_version_contract() -> None:
    go_mod = (REPO_ROOT / "whatsapp-bridge/go.mod").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    module_version = re.search(r"^go (\d+\.\d+\.\d+)$", go_mod, re.MULTILINE)
    util_version = re.search(r"^\s+go\.mau\.fi/util (v\S+) // indirect$", go_mod, re.MULTILINE)
    toolchain = re.search(r"^toolchain\s+", go_mod, re.MULTILINE)
    builder_version = re.search(
        r"^FROM golang:(\d+\.\d+\.\d+)-bookworm@sha256:([0-9a-f]{64}) AS go-builder$",
        dockerfile,
        re.MULTILINE,
    )
    documented_version = re.search(
        r"^# Digest-pinned for full reproducibility \(Go (\d+\.\d+\.\d+)\)\.$",
        dockerfile,
        re.MULTILINE,
    )

    assert module_version is not None
    assert util_version is not None
    assert builder_version is not None
    assert documented_version is not None
    assert module_version.group(1) == MODULE_GO_MINIMUM
    assert util_version.group(1) == UTIL_VERSION
    assert toolchain is None
    assert builder_version.groups() == (BUILDER_GO_VERSION, BUILDER_DIGEST)
    assert documented_version.group(1) == BUILDER_GO_VERSION
