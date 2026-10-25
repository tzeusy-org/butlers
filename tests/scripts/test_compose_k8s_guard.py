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
    control_replicas: str | None = None,
    probe_failure: tuple[str, str] | None = None,
    cluster_context: str = "configured",
    override: bool = False,
    complete_launch: bool = False,
    grafana_anonymous: str | None = None,
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
            "docker-compose.control-stage.yml",
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
        'if [[ "$*" == "config view"* ]]; then\n'
        '  [[ "$TEST_CLUSTER_CONTEXT" != "invalid" ]] || exit 1\n'
        '  [[ "$TEST_CLUSTER_CONTEXT" != "configured" ]] || printf "offline-context"\n'
        "  exit 0\n"
        "fi\n"
        'replicas="$TEST_K8S_REPLICAS"\n'
        'if [[ "$*" == *"get deploy butlers-control "* ]]; then replicas="$TEST_K8S_CONTROL_REPLICAS"; fi\n'
        'if [[ -n "$TEST_PROBE_ROLE" && "$*" == *"get deploy $TEST_PROBE_ROLE "* ]]; then\n'
        '  case "$TEST_PROBE_FAILURE" in\n'
        "    timeout) exit 124 ;;\n"
        '    rbac|transport|api) printf "%s\\n" "$TEST_PROBE_FAILURE" >&2; exit 1 ;;\n'
        '    malformed) printf "present invalid 0"; exit 0 ;;\n'
        '    partial) printf "present 0"; exit 0 ;;\n'
        '    negative) printf "present 0 -1"; exit 0 ;;\n'
        "  esac\n"
        "fi\n"
        # Model server NotFound faithfully: only --ignore-not-found makes it
        # a successful empty response. Failures never stand in for absence.
        'if [[ -z "$replicas" ]]; then [[ "$*" == *--ignore-not-found* ]]; exit $?; fi\n'
        'if [[ "$*" == *go-template=* ]]; then\n'
        '  if [[ "$*" == *".status.replicas"* ]]; then\n'
        '    [[ "$replicas" == *" "* ]] || replicas="$replicas 0"\n'
        '  else replicas="${replicas%% *}"; fi\n'
        '  printf "present %s" "$replicas"; exit 0\n'
        "fi\n"
        'if [[ "$*" != *"{.status.replicas}"* ]]; then replicas="${replicas%% *}"; fi\n'
        'printf "%s" "$replicas"\n',
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
        "'OTEL_', 'GF_AUTH_', 'COMPOSE_PROJECT_', 'SKIP_OAUTH_', "
        "'BUTLERS_SWITCHBOARD_SERVICE'))}}, stream)\n"
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
        "TEST_K8S_CONTROL_REPLICAS": replicas if control_replicas is None else control_replicas,
        "TEST_PROBE_ROLE": probe_failure[0] if probe_failure else "",
        "TEST_PROBE_FAILURE": probe_failure[1] if probe_failure else "",
        "TEST_CLUSTER_CONTEXT": cluster_context,
    }
    if grafana_anonymous is not None:
        environment["GF_AUTH_ANONYMOUS_ENABLED"] = grafana_anonymous
    if cluster_context == "not-installed":
        (fake_bin / "kubectl").unlink()
        # A hermetic executable search path proves command absence even on CI
        # hosts that install kubectl. Lifecycle commands above stay fake.
        for executable in ("bash", "dirname", "timeout", "sha256sum", "awk", "mkdir", "touch"):
            binary = shutil.which(executable)
            assert binary is not None
            (fake_bin / executable).symlink_to(binary)
        environment["PATH"] = str(fake_bin)
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

    # Retain every original mode/case and additionally exercise each staged
    # role as the sole active Kubernetes owner, before any lifecycle action.
    if namespace == "butlers-dev":
        for domains, control, active in (
            ("0", "1", "butlers-control"),
            ("1", "0", "butlers-up"),
            ("0", "0 1", "butlers-control"),
            ("0 1", "0", "butlers-up"),
        ):
            result, calls = _run_launcher(
                tmp_path / (active + domains.replace(" ", "-") + control.replace(" ", "-")),
                args=args,
                replicas=domains,
                control_replicas=control,
            )
            assert result.returncode == 1
            assert f"namespace {namespace}" in result.stderr
            assert f"get deploy {active}" in calls
            assert "docker" not in calls
            assert "sudo" not in calls

        # Exercise the real launcher through fake down/up, not source spelling.
        # A configured role whose ownership is unknown must stop before either.
        for role in ("butlers-up", "butlers-control"):
            for failure in (
                "timeout",
                "rbac",
                "transport",
                "api",
                "malformed",
                "partial",
                "negative",
            ):
                result, calls = _run_launcher(
                    tmp_path / f"{role}-{failure}",
                    args=args,
                    replicas="0",
                    probe_failure=(role, failure),
                    complete_launch=True,
                )
                assert result.returncode == 1, (role, failure, result.stderr)
                assert f"get deploy {role}" in calls
                assert "docker" not in calls
                assert "sudo" not in calls

        result, calls = _run_launcher(
            tmp_path / "invalid-local-config",
            args=args,
            replicas="0",
            cluster_context="invalid",
            complete_launch=True,
        )
        assert result.returncode == 1
        assert "docker" not in calls
        assert "sudo" not in calls


@pytest.mark.parametrize("replicas", ["0", ""])
def test_proceeds_when_release_is_absent_or_scaled_down(tmp_path, replicas):
    result, calls = _run_launcher(tmp_path, args=(), replicas=replicas)

    assert "served by Kubernetes" not in result.stderr
    assert "kubectl -n butlers-dev get deploy butlers-up" in calls

    # Prove real lifecycle reachability for both confirmed absence and zero,
    # plus a standalone host with no configured current Kubernetes context.
    for context in ("configured", "unconfigured", "not-installed"):
        result, calls = _run_launcher(
            tmp_path / context,
            args=(),
            replicas=replicas,
            cluster_context=context,
            complete_launch=True,
        )
        assert result.returncode == 0, result.stderr
        assert "down --remove-orphans" in calls
        assert "up -d" in calls
        assert ("get deploy" in calls) == (context == "configured")


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
        ("--prod", "--hotreload"),
    ],
    ids=["dev", "baked-dev", "audio", "restore-drill", "prod", "prod-hotreload"],
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
    staged = "--prod" not in args
    hotreload = "--hotreload" in args or ("--prod" not in args and "--no-hotreload" not in args)
    files = ["docker-compose.yml"]
    if protected:
        files.append("docker-compose.restore-drill.yml")
    if staged:
        files.append("docker-compose.control-stage.yml")
    if observability:
        files.append("docker-compose.observability.yml")
    profiles = ["dev"]
    if "--audio" in args:
        profiles.append("audio")
    if hotreload:
        profiles.append("hotreload")
    if observability:
        profiles.append("observability")
    if staged:
        profiles.append("control-stage")
        if hotreload:
            profiles.append("control-stage-hotreload")
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
    assert any("get deploy butlers-control " in call for call in call_lines) == staged
    # The same offline cases prove the dev split and the unchanged production
    # topology. No lifecycle operation reaches a real container or deployment.
    domains = services["butlers-up-hotreload" if hotreload else "butlers-up"]
    capacity = int(domains["environment"]["BUTLERS_MAX_GLOBAL_SESSIONS"])
    if staged:
        control = services["butlers-control-hotreload" if hotreload else "butlers-control"]
        control_capacity = int(control["environment"]["BUTLERS_MAX_GLOBAL_SESSIONS"])
        assert capacity == 4 and control_capacity == 1 and capacity + control_capacity == 5
        assert "--exclude switchboard" in domains["entrypoint"][-1]
        assert domains["environment"]["SWITCHBOARD_MCP_URL"] == "http://butlers-control:41100/sse"
        assert services["dashboard-api"]["environment"]["SWITCHBOARD_MCP_URL"] == (
            "http://butlers-control:41100/sse"
        )
        assert "KEY_ABSENT_STAGE" in control["entrypoint"][-1]
    else:
        assert capacity == 5
        assert "butlers-control" not in services and "butlers-control-hotreload" not in services
        assert "--exclude switchboard" not in domains["entrypoint"][-1]
        assert services["dashboard-api"]["environment"]["SWITCHBOARD_MCP_URL"] == (
            "http://butlers-up:41100/sse"
        )
    endpoint = services["butlers-up"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert endpoint == ("http://localhost:4318" if observability else "")
    if observability:
        anonymous = services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"]
        assert anonymous == "false"
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
            *(["--scale", "butlers-control=0"] if hotreload and staged else []),
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


@pytest.mark.parametrize("configured,expected", [(None, "false"), ("true", "true")])
def test_observability_uses_explicit_grafana_configuration(tmp_path, configured, expected):
    result, calls = _run_launcher(
        tmp_path,
        args=("--observability",),
        replicas="0",
        complete_launch=True,
        grafana_anonymous=configured,
    )
    assert result.returncode == 0, result.stderr
    records = [
        json.loads(line) for line in (tmp_path / "docker-argv.jsonl").read_text().splitlines()
    ]
    assert records
    assert all(r["env"]["GF_AUTH_ANONYMOUS_ENABLED"] == expected for r in records)
    assert "docker" in calls  # every command goes to the owned fake executable
