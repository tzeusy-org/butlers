"""Contracts for the Go toolchain used to build the WhatsApp bridge."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_GO_MINIMUM = "1.25.0"
UTIL_VERSION = "v0.9.12-0.20260717235539-f9ffa7eca58d"
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


def test_go_artifact_producer_and_consumer_use_the_same_pinned_offline_contract() -> None:
    producer = (REPO_ROOT / "Dockerfile.go-artifacts").read_text()
    consumer = (REPO_ROOT / "Dockerfile").read_text()
    source = (REPO_ROOT / "scripts/runtime_clis/go_artifacts.go").read_text()
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    builder = f"golang:{BUILDER_GO_VERSION}-bookworm@sha256:{BUILDER_DIGEST}"
    assert f"FROM {builder} AS producer" in producer
    assert "RUN --network=default" in producer
    assert "go mod download -json all" in producer
    assert "cp go.mod go.sum /seed/project/" in producer
    assert "RUN --network=none GOPROXY=off GOSUMDB=off" in producer
    assert "--project /source --cache /seed/modules --output /output/context" in producer
    assert "COPY --from=producer /output/context/ /" in producer
    assert "--mount=from=go-artifacts,source=.,target=/opt/go-artifacts,readonly" in consumer
    assert "RUN --network=none --mount=from=go-artifacts" in consumer
    assert "go run /producer/go_artifacts.go verify --project /build" in consumer
    assert "go mod verify" in consumer
    assert "go mod tidy -diff" in consumer
    assert "go mod download" not in consumer
    assert "go build -mod=readonly -trimpath -buildvcs=false" in consumer
    assert '"-trimpath", "-buildvcs=false", "-ldflags=-s -w"' in source
    assert "go_artifacts.go ../scripts/runtime_clis/go_artifacts_test.go" in workflow
