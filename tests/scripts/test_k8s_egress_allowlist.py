"""scripts/k8s/egress-allowlist.sh resolves the dev egress allowlist (bu-viat6h.12).

getent is stubbed through PATH with documentation addresses (TEST-NET-1); nothing here
resolves a real name or carries a real address.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "k8s" / "egress-allowlist.sh"
_SERVICES = (
    "otel",
    "ollama",
    "tzehouse-synology",
    "homeassistant",
    "v-on-shenton",
    "wyoming-faster-whisper",
)
# homeassistant and v-on-shenton share a host, so dedupe must collapse them.
_ADDR = {
    "otel": "192.0.2.1",
    "ollama": "192.0.2.2",
    "tzehouse-synology": "192.0.2.3",
    "homeassistant": "192.0.2.4",
    "v-on-shenton": "192.0.2.4",
    "wyoming-faster-whisper": "192.0.2.5",
}
_TAILNET = "tailnet.example.test"


def _stub_getent(directory: Path, missing: str | None = None) -> None:
    lines = ["#!/bin/sh", '[ "$1" = ahostsv4 ] || exit 2', 'case "$2" in']
    for svc, addr in _ADDR.items():
        if svc == missing:
            continue
        lines.append(f'  {svc}.{_TAILNET}) echo "{addr} STREAM $2" ;;')
    lines += ["  *) exit 2 ;;", "esac"]
    stub = directory / "getent"
    stub.write_text("\n".join(lines) + "\n")
    stub.chmod(0o755)


def _run(
    tmp_path: Path, *, missing: str | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    _stub_getent(tmp_path, missing)
    full_env = {"PATH": f"{tmp_path}:/usr/bin:/bin", **env}
    body = f'. "{_SCRIPT}"; butlers_resolve_egress_allowlist || exit $?; echo "OUT=$ALLOWED_TAILNET_HOSTS"'
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env=full_env, cwd="/"
    )


def _out(result: subprocess.CompletedProcess[str]) -> str:
    return next(x for x in result.stdout.splitlines() if x.startswith("OUT="))[4:]


def test_resolves_every_service_dedupes_and_exports(tmp_path: Path) -> None:
    result = _run(tmp_path, TAILNET_NAME=_TAILNET)
    assert result.returncode == 0, result.stderr
    assert _out(result).split() == ["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4", "192.0.2.5"]
    for svc in _SERVICES:
        assert f"egress allow: {svc} resolved" in result.stdout
    assert "egress allowlist: 5 address(es)" in result.stdout


def test_includes_the_home_assistant_connector_host() -> None:
    assert "v-on-shenton" in _SCRIPT.read_text()


def test_fails_closed_when_a_service_does_not_resolve(tmp_path: Path) -> None:
    result = _run(tmp_path, missing="ollama", TAILNET_NAME=_TAILNET)
    assert result.returncode == 2
    assert "ollama did not resolve" in result.stderr
    assert "OUT=" not in result.stdout


def test_requires_tailnet_name(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "TAILNET_NAME" in result.stderr


def test_explicit_allowlist_wins_without_resolving(tmp_path: Path) -> None:
    result = _run(tmp_path, ALLOWED_TAILNET_HOSTS="203.0.113.9", TAILNET_NAME=_TAILNET)
    assert result.returncode == 0, result.stderr
    assert _out(result) == "203.0.113.9"
    assert "resolved" not in result.stdout


def test_never_prints_an_address(tmp_path: Path) -> None:
    result = _run(tmp_path, TAILNET_NAME=_TAILNET)
    printed = "\n".join(x for x in result.stdout.splitlines() if not x.startswith("OUT="))
    assert "192.0.2." not in printed + result.stderr
    failed = _run(tmp_path, missing="otel", TAILNET_NAME=_TAILNET)
    assert "192.0.2." not in failed.stdout + failed.stderr
