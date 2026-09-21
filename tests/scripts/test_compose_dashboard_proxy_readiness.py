"""Fake-command coverage for dashboard proxy readiness orchestration.

The harness runs the real launcher with fake lifecycle commands. It never invokes
Docker, Compose, credentials, or a runtime service.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER = _REPO_ROOT / "scripts" / "compose.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_launcher(
    tmp_path: Path,
    *,
    initial_health: str,
    final_health: str = "healthy",
    attribution: str = "success",
    no_hotreload: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(_LAUNCHER, scripts / "compose.sh")
    shutil.copy2(_REPO_ROOT / "scripts" / "base-image-input-fingerprint.sh", scripts)
    (scripts / "dashboard_proxy_peer.py").write_text("# intercepted by fake python3\n")
    for relative in (
        "Dockerfile.base",
        "scripts/runtime_cli_sandbox_init.c",
        "scripts/generate_runtime_cli_sandbox_manifest.py",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test build input\n", encoding="utf-8")
    (repo / ".env.dev").write_text(
        "POSTGRES_HOST=database.example.test\n"
        "POSTGRES_PORT=5432\n"
        "DASHBOARD_AUTH_ORIGIN=https://butlers.example.test\n"
        "DASHBOARD_AUTH_TRUSTED_PROXY_PEERS=old.invalid\n",
        encoding="utf-8",
    )

    calls = tmp_path / "calls"
    state = tmp_path / "state"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "$TEST_CALLS"
if [[ "$1 $2" == "image inspect" ]]; then exit 0; fi
if [[ "$1" == "inspect" ]]; then
  container="${@: -1}"
  phase=initial
  [[ "$container" == "final-container" ]] && phase=final
  counter="$TEST_STATE/${phase}-health-count"
  count=0; [[ -f "$counter" ]] && count=$(<"$counter")
  printf '%s\n' "$((count + 1))" > "$counter"
  if [[ "$phase" == initial ]]; then values="$TEST_INITIAL_HEALTH"; else values="$TEST_FINAL_HEALTH"; fi
  IFS=',' read -r -a states <<< "$values"
  index=$count; (( index >= ${#states[@]} )) && index=$((${#states[@]} - 1))
  case "${states[$index]}" in
    inspect-error) exit 1 ;;
    exited) printf 'exited none\n' ;;
    unhealthy) printf 'running unhealthy\n' ;;
    starting) printf 'running starting\n' ;;
    healthy) printf 'running healthy\n' ;;
  esac
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" ps -q "* ]]; then
    if [[ -f "$TEST_STATE/recreated" ]]; then printf 'final-container\n'; else printf 'initial-container\n'; fi
    exit 0
  fi
  if [[ " $* " == *" --no-deps --force-recreate "* ]]; then touch "$TEST_STATE/recreated"; fi
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" != *dashboard_proxy_peer.py ]]; then exec /usr/bin/python3 "$@"; fi
printf 'python3 dashboard-proxy-helper\n' >> "$TEST_CALLS"
counter="$TEST_STATE/attribution-count"
count=0; [[ -f "$counter" ]] && count=$(<"$counter")
printf '%s\n' "$((count + 1))" > "$counter"
IFS=',' read -r -a outcomes <<< "$TEST_ATTRIBUTION"
index=$count; (( index >= ${#outcomes[@]} )) && index=$((${#outcomes[@]} - 1))
case "${outcomes[$index]}" in
  success) printf '172.18.0.1\n' ;;
  concurrent) exit 75 ;;
  *) exit 1 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "date",
        """#!/usr/bin/env bash
set -euo pipefail
counter="$TEST_STATE/clock"
now=0; [[ -f "$counter" ]] && now=$(<"$counter")
printf '%s\n' "$((now + 10))" > "$counter"
printf '%s\n' "$now"
""",
    )
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "sudo", "#!/usr/bin/env bash\nexit 1\n")
    _write_executable(fake_bin / "bd", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "git",
        "#!/usr/bin/env bash\n[[ \"$1\" == rev-parse ]] && printf 'test-sha\\n'\n",
    )
    state.mkdir()
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TEST_CALLS": str(calls),
        "TEST_STATE": str(state),
        "TEST_INITIAL_HEALTH": initial_health,
        "TEST_FINAL_HEALTH": final_health,
        "TEST_ATTRIBUTION": attribution,
    }
    args = ["bash", scripts / "compose.sh", "--skip-oauth-check", "--skip-tailscale-check"]
    if no_hotreload:
        args.append("--no-hotreload")
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        cwd=repo,
        env=environment,
        text=True,
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return completed, lines


def test_delayed_initial_readiness_precedes_attribution_and_healthy_recreation(tmp_path):
    completed, calls = _run_launcher(
        tmp_path,
        initial_health="starting,healthy",
        final_health="starting,healthy",
    )

    assert completed.returncode == 0, completed.stderr
    helper_index = calls.index("python3 dashboard-proxy-helper")
    initial_inspects = [i for i, call in enumerate(calls) if call.endswith("initial-container")]
    assert len(initial_inspects) == 2
    assert max(initial_inspects) < helper_index
    assert any("ps -q dashboard-api-hotreload" in call for call in calls)
    assert any("--no-deps --force-recreate dashboard-api-hotreload" in call for call in calls)
    assert any(call.endswith("final-container") for call in calls[helper_index + 1 :])
    assert "phase=initial-readiness" in completed.stdout
    assert "phase=final-readiness" in completed.stdout


def test_initial_readiness_failure_never_attributes_or_recreates(tmp_path):
    completed, calls = _run_launcher(
        tmp_path,
        initial_health="starting",
        no_hotreload=True,
    )

    assert completed.returncode != 0
    assert "phase=initial-readiness" in completed.stderr
    assert "category=deadline-exceeded" in completed.stderr
    assert any("ps -q dashboard-api" in call for call in calls)
    assert "python3 dashboard-proxy-helper" not in calls
    assert not any("--force-recreate" in call for call in calls)


def test_post_recreation_readiness_failure_never_reports_success(tmp_path):
    completed, calls = _run_launcher(
        tmp_path,
        initial_health="healthy",
        final_health="unhealthy",
    )

    assert completed.returncode != 0
    assert "phase=final-readiness" in completed.stderr
    assert "category=unhealthy" in completed.stderr
    assert any("--force-recreate" in call for call in calls)
    assert "healthy; proxy trust is active" not in completed.stdout


def test_transient_concurrent_attribution_is_retried_then_succeeds(tmp_path):
    completed, calls = _run_launcher(
        tmp_path,
        initial_health="healthy",
        attribution="concurrent,success",
    )

    assert completed.returncode == 0, completed.stderr
    assert calls.count("python3 dashboard-proxy-helper") == 2
    assert "phase=peer-attribution" in completed.stdout
    assert "category=concurrent-attribution" in completed.stdout
    assert any("--force-recreate" in call for call in calls)


def test_persistent_concurrent_attribution_fails_closed_and_content_blind(tmp_path):
    completed, calls = _run_launcher(
        tmp_path,
        initial_health="healthy",
        attribution="concurrent",
    )

    assert completed.returncode != 0
    assert "phase=peer-attribution" in completed.stderr
    assert "category=concurrent-attribution" in completed.stderr
    assert calls.count("python3 dashboard-proxy-helper") > 1
    assert not any("--force-recreate" in call for call in calls)
    combined = completed.stdout + completed.stderr
    assert "initial-container" not in combined
    assert "172.18.0.1" not in combined
    assert "old.invalid" not in combined
