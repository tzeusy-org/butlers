"""Safe launcher command capture and offline Compose model coverage."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_launcher(
    tmp_path: Path,
    *,
    args: tuple[str, ...],
    replicas: str,
    override: bool = False,
    complete_launch: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "scripts" / "compose.sh", repo / "scripts" / "compose.sh")
    for mode in ("dev", "prod"):
        (repo / f".env.{mode}").write_text(
            "POSTGRES_HOST=db.invalid\nPOSTGRES_PASSWORD=x\n", encoding="utf-8"
        )
    if complete_launch:
        shutil.copy2(_REPO_ROOT / "scripts/base-image-input-fingerprint.sh", repo / "scripts")
        for relative in (
            "Dockerfile.base",
            "scripts/runtime_cli_sandbox_init.c",
            "scripts/generate_runtime_cli_sandbox_manifest.py",
        ):
            (repo / relative).write_text("synthetic build input\n", encoding="utf-8")
        for filename in (
            "docker-compose.yml",
            "docker-compose.restore-drill.yml",
            "docker-compose.observability.yml",
        ):
            shutil.copy2(_REPO_ROOT / filename, repo)
        password = tmp_path / "synthetic-password"
        password.write_text("synthetic-only\n", encoding="utf-8")
        for mode in ("dev", "prod"):
            with (repo / f".env.{mode}").open("a", encoding="utf-8") as stream:
                stream.write(
                    f"RESTORE_DRILL_EXECUTOR_PASSWORD_FILE={password}\n"
                    "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5\n"
                )

    calls = tmp_path / "calls"
    calls.touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "kubectl",
        "#!/usr/bin/env bash\n"
        'printf "kubectl %s\\n" "$*" >> "$TEST_CALLS"\n'
        '[[ -n "$TEST_K8S_REPLICAS" ]] || exit 1\n'
        'printf "%s" "$TEST_K8S_REPLICAS"\n',
    )
    # All lifecycle calls remain fake, including image builds and firewall setup.
    _write_executable(
        fake_bin / "docker",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['TEST_CALLS'], 'a') as stream:\n"
        "    stream.write('docker ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "with open(os.environ['TEST_DOCKER_ARGV'], 'a') as stream:\n"
        "    json.dump({'argv': sys.argv[1:], 'env': {key: os.environ[key] for key in "
        "os.environ if key.startswith(('POSTGRES_', 'RESTORE_DRILL_EXECUTOR_', "
        "'OTEL_', 'GF_AUTH_', 'COMPOSE_PROJECT_', 'SKIP_OAUTH_'))}}, stream)\n"
        "    stream.write('\\n')\n"
        f"sys.exit({0 if complete_launch else 1})\n",
    )
    _write_executable(
        fake_bin / "sudo",
        '#!/usr/bin/env bash\nprintf "sudo %s\\n" "$*" >> "$TEST_CALLS"\n'
        '[[ "$*" != "-n true" ]] || exit 1\n'
        'if [[ "$*" == *--prepare-executor-capability-v1* ]]; then printf "%064d\\n" 0; fi\n',
    )
    _write_executable(fake_bin / "git", "#!/usr/bin/env bash\nprintf 'synthetic-sha\\n'\n")
    _write_executable(fake_bin / "bd", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "tailscale", "#!/usr/bin/env bash\nexit 1\n")
    environment = {
        # Never inherit deployment configuration or credentials into the copy.
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "LC_ALL": "C",
        "ALLOWED_TAILNET_HOSTS": "10.23.4.5",
        "TEST_CALLS": str(calls),
        "TEST_DOCKER_ARGV": str(tmp_path / "docker-argv.jsonl"),
        "TEST_K8S_REPLICAS": replicas,
    }
    if override:
        environment["BUTLERS_ALLOW_COMPOSE_WITH_K8S"] = "1"
    result = subprocess.run(
        [str(repo / "scripts" / "compose.sh"), "--skip-tailscale-check", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, calls.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("args", "namespace"),
    [
        ((), "butlers-dev"),
        (("--prod",), "butlers"),
        (("--observability", "--with-restore-drill"), "butlers-dev"),
        (("--prod", "--observability"), "butlers"),
    ],
)
def test_refuses_while_release_has_replicas(tmp_path, args, namespace):
    result, calls = _run_launcher(tmp_path, args=args, replicas="1")

    assert result.returncode == 1
    assert f"namespace {namespace}" in result.stderr
    assert f"kubectl -n {namespace} get deploy butlers-up" in calls
    assert "docker" not in calls
    assert "sudo" not in calls


@pytest.mark.parametrize("replicas", ["0", ""])
def test_proceeds_when_release_is_absent_or_scaled_down(tmp_path, replicas):
    result, calls = _run_launcher(tmp_path, args=(), replicas=replicas)

    assert "served by Kubernetes" not in result.stderr
    assert "kubectl -n butlers-dev get deploy butlers-up" in calls


def test_override_skips_the_cluster_check(tmp_path):
    result, calls = _run_launcher(tmp_path, args=(), replicas="1", override=True)

    assert "served by Kubernetes" not in result.stderr
    assert "kubectl" not in calls


@pytest.mark.parametrize("observability", [False, True], ids=["off", "on"])
@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--no-hotreload",),
        ("--audio",),
        ("--with-restore-drill",),
        ("--prod",),
        ("--prod", "--hotreload", "--hardened"),
    ],
    ids=["dev", "baked-dev", "audio", "restore-drill", "prod", "prod-hotreload-hardened"],
)
def test_observability_command_and_offline_service_model(tmp_path, args, observability):
    docker = shutil.which("docker")
    assert docker is not None, "Docker Compose CLI is required for offline model validation"
    # Repeated selection must still add the fragment and profile exactly once.
    flags = (*args, "--skip-oauth-check", *(("--observability",) * 2 if observability else ()))
    result, calls = _run_launcher(tmp_path, args=flags, replicas="0", complete_launch=True)
    assert result.returncode == 0, result.stderr
    call_lines = calls.splitlines()
    records = [
        json.loads(line) for line in (tmp_path / "docker-argv.jsonl").read_text().splitlines()
    ]
    compose = [record for record in records if record["argv"][0] == "compose"]
    protected = "--prod" in args or "--with-restore-drill" in args
    hotreload = "--hotreload" in args or ("--prod" not in args and "--no-hotreload" not in args)
    files = ["docker-compose.yml"]
    if protected:
        files.append("docker-compose.restore-drill.yml")
    if observability:
        files.append("docker-compose.observability.yml")
    profiles = ["dev"]
    if "--audio" in args:
        profiles.append("audio")
    if hotreload:
        profiles.append("hotreload")
    if observability:
        profiles.append("observability")
    prefix = ["compose", *(word for file in files for word in ("-f", file))]
    prefix += [word for profile in profiles for word in ("--profile", profile)]
    # Render the captured command with the real CLI in the disposable copy.
    # Only `config` is executed; no daemon, real dotenv or credential is used.
    rendered = subprocess.run(
        [
            docker,
            *compose[-1]["argv"][: compose[-1]["argv"].index("up")],
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path / "repo",
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), **compose[-1]["env"]},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    model = json.loads(rendered.stdout)
    services = model["services"]
    actual = {"otel-collector", "tempo", "prometheus", "grafana"} & services.keys()
    assert actual == (
        {"otel-collector", "tempo", "prometheus", "grafana"} if observability else set()
    )
    assert model["name"] == ("butlers" if "--prod" in args else "butlers-dev")
    assert ("restore-drill-executor" in services) == protected
    assert services["oauth-gate"]["environment"]["SKIP_OAUTH_CHECK"] == "true"
    endpoint = services["butlers-up"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert endpoint == ("http://localhost:4318" if observability else "")
    if observability:
        anonymous = services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"]
        assert anonymous == ("false" if "--hardened" in args else "true")
    actions = [record["argv"][len(prefix) :] for record in compose]
    assert all(record["argv"][: len(prefix)] == prefix for record in compose)
    assert actions == [
        ["down", "--remove-orphans"],
        *(
            [["create", "restore-drill-postgres-proxy", "restore-drill-executor"]]
            if protected
            else []
        ),
        [
            "up",
            "-d",
            *(["--scale", "butlers-up=0", "--scale", "dashboard-api=0"] if hotreload else []),
        ],
    ]
    if protected:
        prepare = next(
            i for i, call in enumerate(call_lines) if "--prepare-executor-capability-v1" in call
        )
        create = next(i for i, call in enumerate(call_lines) if " create " in call)
        fence = next(
            i for i, call in enumerate(call_lines) if "--require-executor-capability-v1" in call
        )
        up = next(i for i, call in enumerate(call_lines) if " up -d" in call)
        assert prepare < create < fence < up
    else:
        assert not any("executor-capability-v1" in call for call in call_lines)
