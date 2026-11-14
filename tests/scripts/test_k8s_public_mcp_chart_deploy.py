"""scripts/k8s/public-mcp-chart-deploy.sh: the public chart upgrade at the butlers TAG (task 2.4).

A stub `helm` records its arguments; nothing reaches a cluster.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts/k8s/public-mcp-chart-deploy.sh"
_CHART = _ROOT / "deploy/helm/butlers-public-mcp"
_TAG = "abc123def456"


def _run(tmp_path: Path, *args: str, helm_rc: int = 0, **env: str) -> tuple[int, str, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "helm"
    stub.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{tmp_path}/helm.log"\nexit {helm_rc}\n')
    stub.chmod(0o755)
    log = tmp_path / "helm.log"
    log.write_text("")
    result = subprocess.run(
        ["bash", str(_SCRIPT), *args], capture_output=True, text=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BUTLERS_SITE_ENV": str(tmp_path / "absent.env"),
            "HOME": str(tmp_path),
            **env,
        },
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text().splitlines()


def test_dev_on_upgrades_the_chart_at_the_tag_from_the_butlers_namespace(tmp_path: Path) -> None:
    code, stderr, calls = _run(
        tmp_path, "dev", _TAG, "on",
        BUTLERS_IMAGE_REGISTRY="registry.example", BUTLERS_PUBLIC_HOST="host.example",
        BUTLERS_PUBLIC_MCP_AUDIENCE="https://mcp.example/mcp",
        BUTLERS_PUBLIC_MCP_ALLOWED_SUBS="sub-1,sub-2",
        BUTLERS_PUBLIC_MCP_CLIENT_DOMAINS="claude.ai,chatgpt.com",
    )  # fmt: skip
    assert code == 0, stderr
    assert calls == [
        f"upgrade --install butlers-public-mcp {_CHART} -n butlers-dev "
        f"-f {_CHART}/values.yaml -f {_CHART}/values.dev.yaml --set image.tag={_TAG} "
        "--wait --timeout 5m --set image.repository=registry.example/butlers-app "
        "--set dashboardBase=https://host.example/butlers-dev "
        "--set-string audience=https://mcp.example/mcp "
        "--set-string allowedSubs=sub-1\\,sub-2 "
        "--set-string clientDomains=claude.ai\\,chatgpt.com"
    ]


@pytest.mark.parametrize("args", [("dev", _TAG, "off"), ("dev", _TAG), ("prod", _TAG, "on")])
def test_off_default_and_prod_skip_without_calling_helm(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    code, stderr, calls = _run(tmp_path, *args)
    assert code == 0, stderr
    assert calls == []
    assert "skipped" in stderr


def test_a_helm_failure_is_loud_and_non_zero(tmp_path: Path) -> None:
    code, stderr, _calls = _run(tmp_path, "dev", _TAG, "on", helm_rc=1)
    assert code == 1
    assert "public-mcp chart FAILED" in stderr


@pytest.mark.parametrize("args", [(), ("dev",), ("dev", "bad tag", "on"), ("dev", _TAG, "maybe")])
def test_bad_arguments_fail_before_helm(tmp_path: Path, args: tuple[str, ...]) -> None:
    code, _stderr, calls = _run(tmp_path, *args)
    assert code != 0
    assert calls == []


def test_the_go_live_live_checks_script_parses_and_never_dumps_a_secret() -> None:
    """The live checks run at go-live, not in CI: keep them parseable and secret-free."""
    script = _ROOT / "scripts/k8s/public-mcp-live-checks.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text()
    for banned in ("get secret", "printenv", "jsonpath='{.data"):
        assert banned not in text


def test_the_live_checks_keep_their_review_guards() -> None:
    """Pin what the review required of the go-live script (it cannot run before go-live)."""
    text = (_ROOT / "scripts/k8s/public-mcp-live-checks.sh").read_text()
    for needle in (
        "ciliumclusterwidenetworkpolicies",
        "globalnetworkpolicies",
        "clusterpolicies.kyverno.io",
        "constrainttemplates",
        "not visible",  # forbidden is never a pass
        "seccompProfile",  # restricted-compliant probe pods
        "get svc -o json",  # Service selectors read before the labelled probe
        "ABORTED",
        "INCONCLUSIVE",
        "CANARIES",
    ):
        assert needle in text, needle
    probe = (_ROOT / "scripts/k8s/public-mcp-isolation-probe.py").read_text()
    assert 'startswith("28")' in probe  # only an auth/pg_hba error class counts as refused


# --- live checks: a broken or inconclusive probe never reads as a pass (N1) ----------------

_FAKE_ASYNCPG = """
import asyncio, os

class _Auth(Exception):
    sqlstate = "28000"

async def connect(*, database, **_kw):
    mode = os.environ["FAKE_MODE"]
    if database == "butlers_public":
        class C:
            async def close(self): pass
        return C()
    if mode == "timeout_second" and database == "butlers":
        raise asyncio.TimeoutError()
    if mode == "crash":
        raise SystemExit(7)
    raise _Auth()
"""

_KUBECTL = r"""#!/usr/bin/env bash
args="$*"
case "$args" in
  *"get deploy butlers-public-mcp"*) echo "registry.invalid/butlers-app:t" ;;
  *"get pod -l app.kubernetes.io/component=butlers-public-mcp"*) echo "pod/srv" ;;
  *"get networkpolicy -o name"*)
    printf 'networkpolicy.networking.k8s.io/%s\n' butlers-public-mcp-default-deny \
      butlers-public-mcp-allow-mcp-ingress butlers-public-mcp-allow-metrics-ingress \
      butlers-public-mcp-allow-postgres-egress butlers-public-mcp-allow-dns-egress ;;
  *"get networkpolicy -o json"*) echo '{"items": []}' ;;
  *"get crd"*) echo "NotFound" >&2; exit 1 ;;
  *"get svc -o json"*) echo '{"items": []}' ;;
  *" run "*|*" wait "*|*" delete "*) exit 0 ;;
  *"exec -i srv -- python3 -"*) exec "$PYBIN" - ;;
  *"test -s"*) exit 0 ;;
  *"import json"*) echo 2 ;;
  *"import socket"*)
    case "$args" in *pubmcp-probe-plain*|*1.1.1.1*) exit 2 ;; *) exit 0 ;; esac ;;
  *) echo "unexpected kubectl: $args" >&2; exit 99 ;;
esac
"""


def _live(tmp_path: Path, mode: str) -> tuple[int, str]:
    fake = tmp_path / "pylib"
    fake.mkdir()
    (fake / "asyncpg.py").write_text(_FAKE_ASYNCPG)
    secret = tmp_path / "password"
    secret.write_text("not-a-real-password\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "kubectl"
    stub.write_text(_KUBECTL)
    stub.chmod(0o755)
    import sys

    result = subprocess.run(
        ["bash", str(_ROOT / "scripts/k8s/public-mcp-live-checks.sh"), "dev"],
        capture_output=True, text=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}", "PYBIN": sys.executable,
            "PYTHONPATH": str(fake), "FAKE_MODE": mode, "PYTHONDONTWRITEBYTECODE": "1",
            "PUBLIC_MCP_PASSWORD_FILE": str(secret), "MCP_PUBLIC_DB_HOST": "h",
            "MCP_PUBLIC_DB_PORT": "5432", "MCP_PUBLIC_DB_USER": "u", "HOME": str(tmp_path),
        },
    )  # fmt: skip
    return result.returncode, result.stdout + result.stderr


def test_live_checks_pass_only_when_every_database_check_is_conclusive(tmp_path: Path) -> None:
    code, out = _live(tmp_path, "refuse")
    assert code == 0, out
    assert "live checks passed" in out


def test_a_timeout_on_the_second_database_is_inconclusive_not_a_pass(tmp_path: Path) -> None:
    code, out = _live(tmp_path, "timeout_second")
    assert code != 0, out
    assert "INCONCLUSIVE ISOLATION login to butlers: TimeoutError" in out
    assert "NameError" not in out
    assert "live checks passed" not in out
    assert "live checks FAILED" in out


def test_a_crashing_isolation_probe_fails_closed(tmp_path: Path) -> None:
    code, out = _live(tmp_path, "crash")
    assert code != 0, out
    assert "ISOLATION probe did not complete" in out
    assert "live checks passed" not in out
