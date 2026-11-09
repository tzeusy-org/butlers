"""scripts/k8s/public-mcp-owner-step.sh and its deploy-% wiring (RFC 0039 D3, OD20).

A stubbed `kubectl` plays the dashboard-api pod and the CNPG primary. Nothing reaches a cluster.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from butlers.cli import cli

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts/k8s/public-mcp-owner-step.sh"
_SQL = "\\set ON_ERROR_STOP on\nSELECT 'owner step fixture';"
_TAG = "abc123def456"
_IMAGE = f"registry.invalid/butlers-app:{_TAG}"

# Each call is logged; the response comes from the environment the test sets.
_KUBECTL = r"""#!/usr/bin/env bash
echo "kubectl $*" >> "$CALL_LOG"
case "$*" in
  *"get pods -l app.kubernetes.io/component=dashboard-api"*)
    printf '%b' "$PODS"; exit "${PODS_RC:-0}" ;;
  *"get pod -l cnpg.io/cluster=postgres-dev,cnpg.io/instanceRole=primary"*)
    printf '%s\n' $PRIMARIES ;;
  *"-c dashboard-api -- /app/.venv/bin/butlers public-mcp owner-sql"*)
    [ "${GEN_RC:-0}" = 0 ] || { echo "Usage: butlers public-mcp [OPTIONS]" >&2; exit "$GEN_RC"; }
    printf '%s\n' "$SQL" ;;
  *"-c dashboard-api -- /app/.venv/bin/butlers public-mcp --help"*)
    printf '%s\n' "$HELP_PUBLIC_MCP" ;;
  *"-c dashboard-api -- /app/.venv/bin/butlers --help"*)
    printf '%s\n' "$HELP"; exit "${HELP_RC:-0}" ;;
  *"-c postgres -- psql"*)
    cat > "$STDIN_LOG"
    [ "${PSQL_RC:-0}" = 0 ] || { echo "ERROR:  public MCP digest refused" >&2; exit "$PSQL_RC"; }
    ;;
  *) echo "unexpected kubectl call: $*" >&2; exit 99 ;;
esac
"""


def _help(*args: str) -> str:
    result = CliRunner().invoke(cli, [*args, "--help"])
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture()
def tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(_KUBECTL, encoding="utf-8")
    kubectl.chmod(0o755)
    return tmp_path


def _run(root: Path, *args: str, **env: str) -> tuple[int, str, list[str]]:
    log = root / "calls.log"
    log.write_text("", encoding="utf-8")
    stdin_log = root / "stdin.log"
    stdin_log.unlink(missing_ok=True)
    environment = {
        "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "STDIN_LOG": str(stdin_log),
        # Wrong tag, then a terminating pod on the right tag, then the one to use.
        "PODS": (
            f"api-old <none> registry.invalid/butlers-app:0000000000aa\\n"
            f"api-going 2026-11-09T00:00:00Z {_IMAGE}\\n"
            f"api-new <none> {_IMAGE}\\n"
        ),
        "PRIMARIES": "pod/postgres-dev-1",
        "HELP": _help(),
        "HELP_PUBLIC_MCP": _help("public-mcp"),
        "SQL": _SQL,
        **env,
    }
    result = subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True, text=True, env=environment, cwd=root,
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text(encoding="utf-8").splitlines()


def _psql_fed(root: Path) -> str | None:
    path = root / "stdin.log"
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_dev_pipes_the_tag_pods_owner_sql_into_the_primary_as_superuser(tools: Path) -> None:
    code, stderr, calls = _run(tools, "dev", _TAG, "on")
    assert code == 0, stderr
    execs = [call for call in calls if " exec " in call]
    assert [call.split(" -- ", 1)[1] for call in execs] == [
        "/app/.venv/bin/butlers --help",
        "/app/.venv/bin/butlers public-mcp --help",
        "/app/.venv/bin/butlers public-mcp owner-sql",
        "psql -X -q -v ON_ERROR_STOP=1 -d butlers -f -",
    ]
    assert all(" exec api-new -c dashboard-api " in call for call in execs[:3])
    assert execs[3].startswith("kubectl -n postgres-dev exec -i pod/postgres-dev-1 -c postgres")
    assert "--field-selector=status.phase=Running --no-headers" in calls[0]
    assert _psql_fed(tools) == _SQL + "\n"
    assert " secret" not in "\n".join(calls)


@pytest.mark.parametrize(
    ("args", "notice"),
    [
        (("dev", _TAG, "off"), "is off"),
        (("prod", _TAG, "on"), "manually"),
        (("prod", _TAG), "manually"),
    ],
)
def test_off_switch_and_other_environments_exit_zero_without_kubectl(
    tools: Path, args: tuple[str, ...], notice: str
) -> None:
    code, stderr, calls = _run(tools, *args)
    assert code == 0, stderr
    assert notice in stderr
    assert calls == []


@pytest.mark.parametrize(
    ("args", "code"),
    [(("dev", _TAG, "maybe"), 1), (("dev", ""), 2), (("dev",), 2), (("", _TAG), 2)],
)
def test_bad_arguments_fail_before_kubectl(tools: Path, args: tuple[str, ...], code: int) -> None:
    status, stderr, calls = _run(tools, *args)
    assert status == code
    assert "public-mcp owner step FAILED" in stderr
    assert calls == []


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"PODS": "api-old <none> registry.invalid/butlers-app:0000000000aa\\n"}, "carries tag"),
        ({"PODS": f"api-going 2026-11-09T00:00:00Z {_IMAGE}\\n"}, "carries tag"),
        ({"PODS": ""}, "carries tag"),
        ({"PODS_RC": "1"}, "could not list"),
        ({"HELP_RC": "1"}, "listing failed"),
        ({"GEN_RC": "2"}, "generation failed"),
        ({"SQL": ""}, "printed nothing"),
        ({"PRIMARIES": "pod/a pod/b"}, "expected one primary"),
        ({"PRIMARIES": ""}, "expected one primary"),
    ],
)
def test_every_failure_before_psql_is_loud_and_feeds_psql_nothing(
    tools: Path, env: dict[str, str], reason: str
) -> None:
    code, stderr, calls = _run(tools, "dev", _TAG, "on", **env)
    assert code == 1
    assert "public-mcp owner step FAILED" in stderr and reason in stderr
    assert _psql_fed(tools) is None
    assert not any("-c postgres" in call for call in calls)


def test_a_guard_stop_or_failed_statement_exits_non_zero(tools: Path) -> None:
    """Every stop in the file is RAISE EXCEPTION, so psql exits 3 under ON_ERROR_STOP."""
    code, stderr, _calls = _run(tools, "dev", _TAG, "on", PSQL_RC="3")
    assert code == 1
    assert "public-mcp owner step FAILED" in stderr
    assert "digest refused" in stderr
    assert _psql_fed(tools) == _SQL + "\n"


@pytest.mark.parametrize(
    ("help_text", "public_mcp_help"),
    [
        ("Commands:\n  db        Database commands.\n  up        Start butlers.", ""),
        (
            "Commands:\n  public-mcp  Manage the projection.",
            "Commands:\n  reconcile  Converge the publication.",
        ),
        (
            "Commands:\n  public-mcp  Manage the projection.",
            "Commands:\n  reconcile        Converge.\n  owner-sql-draft  Not the command.",
        ),
    ],
)
def test_an_image_without_owner_sql_is_a_notice_not_a_failure(
    tools: Path, help_text: str, public_mcp_help: str
) -> None:
    code, stderr, calls = _run(
        tools, "dev", _TAG, "on", HELP=help_text, HELP_PUBLIC_MCP=public_mcp_help
    )
    assert code == 0, stderr
    assert "has no 'butlers public-mcp owner-sql'" in stderr
    assert not any(call.endswith("owner-sql") for call in calls)
    assert _psql_fed(tools) is None


# --- deploy-% recipe ---------------------------------------------------------------------


@pytest.fixture()
def chart(tmp_path: Path) -> Path:
    """The real makefile with recording stubs for every script it calls."""
    chart = tmp_path / "deploy" / "helm" / "butlers"
    chart.mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "deploy/helm/butlers/makefile", chart / "makefile")
    scripts = tmp_path / "scripts" / "k8s"
    scripts.mkdir(parents=True)
    for name, rc in (
        ("runtime-topology-preflight.sh", "0"),
        ("site-helm-args.sh", "0"),
        ("bootstrap-db.sh", "0"),
        ("record-deploy.sh", "${RECORD_RC:-0}"),
        ("public-mcp-owner-step.sh", "${OWNER_RC:-0}"),
    ):
        stub = scripts / name
        stub.write_text(
            f'#!/usr/bin/env bash\necho "{name} $*" >> "$CALL_LOG"\nexit {rc}\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return chart


def _deploy(chart: Path, *make_args: str, **env: str) -> tuple[int, list[str]]:
    """Return the recipe shell's exit status (make itself exits 2 on any failure)."""
    log = chart / "calls.log"
    log.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["make", "--no-print-directory", "deploy-dev", f"TAG={_TAG}", *make_args],
        cwd=chart, capture_output=True, text=True,
        env={**os.environ, "CALL_LOG": str(log), **env},
    )  # fmt: skip
    status = 0
    if result.returncode:
        assert result.returncode == 2, result.stderr
        status = int(result.stderr.rsplit("Error ", 1)[1].split()[0])
    # site-helm-args.sh runs while make expands the helm arguments, so it is not a step.
    lines = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if not line.startswith("site-helm-args.sh")
    ]
    return status, [line.split(" ", 1)[0] for line in lines] + [
        line for line in lines if line.startswith("public-mcp-owner-step.sh")
    ]


def test_recipe_runs_the_owner_step_after_the_release_with_the_switch(chart: Path) -> None:
    code, calls = _deploy(chart)
    assert code == 0
    assert calls[:5] == [
        "runtime-topology-preflight.sh",
        "bootstrap-db.sh",
        "record-deploy.sh",
        "public-mcp-owner-step.sh",
        f"public-mcp-owner-step.sh dev {_TAG} on",
    ]


@pytest.mark.parametrize(
    ("record_rc", "owner_rc", "expected"),
    [("1", "0", 1), ("0", "1", 1), ("4", "1", 4), ("0", "0", 0)],
)
def test_recipe_runs_the_step_after_a_failed_release_and_keeps_the_first_failure(
    chart: Path, record_rc: str, owner_rc: str, expected: int
) -> None:
    """A readiness failure after reconcile still runs the step; the first non-zero wins."""
    code, calls = _deploy(chart, RECORD_RC=record_rc, OWNER_RC=owner_rc)
    assert code == expected
    assert "public-mcp-owner-step.sh" in calls


def test_committed_switch_is_on_and_passed_through_when_overridden(chart: Path) -> None:
    makefile = (_REPO_ROOT / "deploy/helm/butlers/makefile").read_text(encoding="utf-8")
    assert "\nPUBLIC_MCP_OWNER_STEP_dev := on\n" in makefile
    _code, calls = _deploy(chart, "PUBLIC_MCP_OWNER_STEP_dev=off")
    assert calls[-1] == f"public-mcp-owner-step.sh dev {_TAG} off"


def test_recipe_with_the_real_step_fails_when_the_guard_refuses(chart: Path, tools: Path) -> None:
    """Stubbed migrations that did not reconcile: the file's guard stops psql, the deploy fails."""
    shutil.copy2(_SCRIPT, chart.parents[2] / "scripts/k8s/public-mcp-owner-step.sh")
    env = {
        "PATH": f"{tools / 'bin'}:{os.environ['PATH']}",
        "STDIN_LOG": str(tools / "stdin.log"),
        "PODS": f"api-new <none> {_IMAGE}\\n",
        "PRIMARIES": "pod/postgres-dev-1",
        "HELP": _help(),
        "HELP_PUBLIC_MCP": _help("public-mcp"),
        "SQL": _SQL,
        "PSQL_RC": "3",
    }
    code, _calls = _deploy(chart, **env)
    assert code == 1
    assert _psql_fed(tools) == _SQL + "\n"
    code, _calls = _deploy(chart, **{**env, "PSQL_RC": "0"})
    assert code == 0


# --- reconcile in the migrations initContainer -------------------------------------------


def _migrations(*values: str) -> str:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    chart = _REPO_ROOT / "deploy/helm/butlers"
    command = ["helm", "template", "butlers", str(chart), "-f", str(chart / "values.yaml")]
    for value in values:
        command.extend(["-f", str(chart / value)])
    for setting in (
        "image.tag=test",
        "frontendImage.tag=test",
        "database.host=pg.invalid",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={192.0.2.2/32}",
    ):
        command.extend(["--set", setting])
    rendered = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert rendered.returncode == 0, rendered.stderr
    api = next(
        doc
        for doc in yaml.safe_load_all(rendered.stdout)
        if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"] == "dashboard-api"
    )
    (init,) = api["spec"]["template"]["spec"]["initContainers"]
    assert init["name"] == "migrations"
    return init["args"][0]


def test_dev_migrations_reconcile_last_after_every_chain() -> None:
    steps = [step.strip() for step in _migrations("values.dev.yaml").split("&&")]
    assert steps == [
        "uv run --frozen --no-dev butlers db prepare-chronicler-memory --dir /app/roster",
        "uv run --frozen --no-dev butlers db migrate --dir /app/roster",
        "exec uv run --frozen --no-dev butlers public-mcp reconcile --database butlers",
    ]


@pytest.mark.parametrize("values", [(), ("values.prod.yaml",)])
def test_other_environments_do_not_reconcile(values: tuple[str, ...]) -> None:
    args = _migrations(*values)
    assert "public-mcp" not in args
    assert args.rstrip().endswith(
        "exec uv run --frozen --no-dev butlers db migrate --dir /app/roster"
    )
