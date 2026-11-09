"""scripts/k8s/public-mcp-owner-step.sh and its deploy-% wiring (RFC 0039 D3, OD20).

Stubs play the cluster (`kubectl`: the dashboard-api pods and the CNPG primary), the checkout
(`git`: the tag's commit and tree) and the renderer (`uv run --project <tree> butlers ...`).
Nothing reaches a cluster. The real meta-command guard runs on the rendered file.
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
_GUARD = _REPO_ROOT / "scripts/k8s/public_mcp_sql_guard.py"
_SQL = "\\set ON_ERROR_STOP on\nSELECT 'owner step fixture';"
_TAG = "abc123def456"
_COMMIT = _TAG + "0" * 28
_IMAGE = f"registry.invalid/butlers-app:{_TAG}"

# Each call is logged; the response comes from the environment the test sets.
_KUBECTL = r"""#!/usr/bin/env bash
echo "kubectl $*" >> "$CALL_LOG"
case "$*" in
  *"get pods -l app.kubernetes.io/component=dashboard-api,app.kubernetes.io/instance=butlers "*)
    printf '%b' "$PODS"; exit "${PODS_RC:-0}" ;;
  *"get pod -l cnpg.io/cluster=postgres-dev,cnpg.io/instanceRole=primary"*)
    printf '%s\n' $PRIMARIES ;;
  *"-c postgres -- psql"*)
    cat > "$STDIN_LOG"
    [ "${PSQL_RC:-0}" = 0 ] || { echo "ERROR:  public MCP digest refused" >&2; exit "$PSQL_RC"; }
    ;;
  *"-c dashboard-api"*)
    # A compromised pod: whatever it prints must never reach psql.
    printf '%s\n' "\\! touch $STDIN_LOG.pwned" ;;
  *) echo "unexpected kubectl call: $*" >&2; exit 99 ;;
esac
"""
# The tag's commit and its tree: a marker file proves the renderer ran on the archived tree.
_GIT = r"""#!/usr/bin/env bash
echo "git $*" >> "$CALL_LOG"
case "$*" in
  *"rev-parse --verify --quiet "*"^{commit}"*)
    [ -n "${COMMIT:-}" ] || exit 1; echo "$COMMIT" ;;
  *"archive --format=tar $COMMIT"*)
    [ "${ARCHIVE_RC:-0}" = 0 ] || exit "$ARCHIVE_RC"
    tar -C "$TREE" -cf - . ;;
  *) echo "unexpected git call: $*" >&2; exit 99 ;;
esac
"""
_UV = r"""#!/usr/bin/env bash
echo "uv $*" >> "$CALL_LOG"
[ "$1 $2" = "run --project" ] && [ -f "$3/TREE_OF_TAG" ] || { echo "not the tag tree" >&2; exit 98; }
[ -z "${UV_PROJECT_ENVIRONMENT:-}${VIRTUAL_ENV:-}" ] || { echo "inherited venv" >&2; exit 97; }
[ -z "${PYTHONPATH+x}${PYTHONHOME+x}" ] || { echo "inherited python path" >&2; exit 94; }
shift 3
args="$*" rest="${args#--frozen --no-dev --quiet butlers }"
[ "$args" = "--frozen --no-dev --quiet butlers $rest" ] || exit 96
case "$rest" in
  "--help") printf '%s\n' "$HELP"; exit "${HELP_RC:-0}" ;;
  "public-mcp --help") printf '%s\n' "$HELP_PUBLIC_MCP" ;;
  "public-mcp owner-sql")
    [ "${GEN_RC:-0}" = 0 ] || { echo "Usage: butlers public-mcp [OPTIONS]" >&2; exit "$GEN_RC"; }
    printf '%s\n' "$SQL" ;;
  *) exit 95 ;;
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
    for name, body in (("kubectl", _KUBECTL), ("git", _GIT), ("uv", _UV)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "TREE_OF_TAG").write_text("", encoding="utf-8")
    return tmp_path


def _environment(root: Path, **env: str) -> dict[str, str]:
    return {
        "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        "CALL_LOG": str(root / "calls.log"),
        "STDIN_LOG": str(root / "stdin.log"),
        "TREE": str(root / "tree"),
        "COMMIT": _COMMIT,
        # Wrong tag, then a terminating pod on the right tag, then the one that rolled out.
        "PODS": (
            "api-old <none> registry.invalid/butlers-app:0000000000aa\\n"
            f"api-going 2026-11-09T00:00:00Z {_IMAGE}\\n"
            f"api-new <none> {_IMAGE}\\n"
        ),
        "PRIMARIES": "pod/postgres-dev-1",
        "HELP": _help(),
        "HELP_PUBLIC_MCP": _help("public-mcp"),
        "SQL": _SQL,
        **env,
    }


def _run(root: Path, *args: str, **env: str) -> tuple[int, str, list[str]]:
    log = root / "calls.log"
    log.write_text("", encoding="utf-8")
    (root / "stdin.log").unlink(missing_ok=True)
    result = subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True, text=True, env=_environment(root, **env), cwd=root,
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text(encoding="utf-8").splitlines()


def _psql_fed(root: Path) -> str | None:
    path = root / "stdin.log"
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_dev_renders_the_tags_tree_and_pipes_it_to_the_primary_as_superuser(
    tools: Path,
) -> None:
    code, stderr, calls = _run(tools, "dev", _TAG, "on", "butlers")
    assert code == 0, stderr
    assert calls[0] == f"git -C {_REPO_ROOT} rev-parse --verify --quiet {_TAG}^{{commit}}"
    assert "--field-selector=status.phase=Running --no-headers" in calls[1]
    assert calls[2] == f"git -C {_REPO_ROOT} archive --format=tar {_COMMIT}"
    renders = [call.split(" butlers ", 1)[1] for call in calls if call.startswith("uv ")]
    assert renders == ["--help", "public-mcp --help", "public-mcp owner-sql"]
    execs = [call for call in calls if " exec " in call]
    assert len(execs) == 1
    assert execs[0] == (
        "kubectl -n postgres-dev exec -i pod/postgres-dev-1 -c postgres -- "
        "psql -X -q -v ON_ERROR_STOP=1 -d butlers -f -"
    )
    assert _psql_fed(tools) == _SQL + "\n"
    assert " secret" not in "\n".join(calls)


def test_the_program_never_comes_from_a_pod(tools: Path) -> None:
    """B1: a compromised dashboard-api pod cannot supply the superuser SQL."""
    code, stderr, calls = _run(tools, "dev", _TAG, "on", "butlers")
    assert code == 0, stderr
    assert not any("-c dashboard-api" in call for call in calls)
    assert "\\!" not in (_psql_fed(tools) or "")


@pytest.mark.parametrize(
    "inherited",
    [
        {"UV_PROJECT_ENVIRONMENT": "/shared/venv", "VIRTUAL_ENV": "/shared/venv"},
        {"PYTHONPATH": "/planted/site"},
        {"PYTHONHOME": "/planted/home"},
        {"PYTHONPATH": ""},
    ],
)
def test_the_renderer_ignores_an_inherited_environment(
    tools: Path, inherited: dict[str, str]
) -> None:
    """A venv or a Python path from the deployer's shell must not shape the render."""
    code, stderr, _calls = _run(tools, "dev", _TAG, "on", "butlers", **inherited)
    assert code == 0, stderr


@pytest.mark.parametrize(
    "planted",
    ["\\! touch /tmp/pwned", "\\set ON_ERROR_STOP `id`", "\\if `id`", "SELECT 1; \\! id"],
)
def test_a_planted_meta_command_in_the_rendered_file_feeds_psql_nothing(
    tools: Path, planted: str
) -> None:
    code, stderr, calls = _run(
        tools, "dev", _TAG, "on", "butlers", SQL=_SQL + "\n" + planted + "\nSELECT 2;"
    )
    assert code == 1
    assert "public-mcp owner step FAILED" in stderr and "allowlist" in stderr
    assert _psql_fed(tools) is None
    assert not any("-c postgres" in call for call in calls)


@pytest.mark.parametrize(
    ("args", "notice"),
    [
        (("dev", _TAG, "off"), "is off"),
        (("prod", _TAG, "on"), "manually"),
        (("prod", _TAG), "manually"),
        # The recipe passes the undefined-for-prod switch as an explicit empty argument.
        (("prod", _TAG, "", "butlers"), "manually"),
        (("prod", _TAG, "off", "butlers"), "manually"),
    ],
)
def test_off_switch_and_other_environments_exit_zero_without_any_call(
    tools: Path, args: tuple[str, ...], notice: str
) -> None:
    code, stderr, calls = _run(tools, *args)
    assert code == 0, stderr
    assert notice in stderr
    assert calls == []


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("dev", _TAG, "maybe"), 1),
        # An explicitly empty switch is refused, not read as on.
        (("dev", _TAG, ""), 1),
        (("dev", _TAG, "on", ""), 1),
        (("dev", _TAG, "on", "Bad_Release"), 1),
        (("dev", "main", "on"), 1),
        (("dev", "ABC123DEF456", "on"), 1),
        (("dev", "abc12", "on"), 1),
        (("dev", ""), 2),
        (("dev",), 2),
        (("", _TAG), 2),
    ],
)
def test_bad_arguments_fail_before_any_call(tools: Path, args: tuple[str, ...], code: int) -> None:
    status, stderr, calls = _run(tools, *args)
    assert status == code
    assert "public-mcp owner step FAILED" in stderr
    assert calls == []


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"COMMIT": ""}, "is not a commit"),
        ({"COMMIT": "fff123def456" + "0" * 28}, "resolves to"),
        ({"PODS": "api-old <none> registry.invalid/butlers-app:0000000000aa\\n"}, "carries tag"),
        ({"PODS": f"api-going 2026-11-09T00:00:00Z {_IMAGE}\\n"}, "carries tag"),
        # Exact tag only: a longer or a shorter tag on the only pod does not match.
        ({"PODS": f"api-x <none> {_IMAGE}0\\n"}, "carries tag"),
        ({"PODS": f"api-x <none> {_IMAGE[:-1]}\\n"}, "carries tag"),
        ({"PODS": f"api-x <none> registry.invalid/butlers-app:0{_TAG}\\n"}, "carries tag"),
        ({"PODS": f"api-x <none> {_TAG}\\n"}, "carries tag"),
        ({"PODS": ""}, "carries tag"),
        ({"PODS_RC": "1"}, "could not list"),
        ({"ARCHIVE_RC": "1"}, "could not export"),
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
    code, stderr, calls = _run(tools, "dev", _TAG, "on", "butlers", **env)
    assert code == 1
    assert "public-mcp owner step FAILED" in stderr and reason in stderr
    assert _psql_fed(tools) is None
    assert not any("-c postgres" in call for call in calls)


def test_a_guard_stop_or_failed_statement_exits_non_zero(tools: Path) -> None:
    """Every stop in the file is RAISE EXCEPTION, so psql exits 3 under ON_ERROR_STOP."""
    code, stderr, _calls = _run(tools, "dev", _TAG, "on", "butlers", PSQL_RC="3")
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
def test_a_tag_without_owner_sql_is_a_notice_not_a_failure(
    tools: Path, help_text: str, public_mcp_help: str
) -> None:
    code, stderr, calls = _run(
        tools, "dev", _TAG, "on", "butlers", HELP=help_text, HELP_PUBLIC_MCP=public_mcp_help
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
        f"public-mcp-owner-step.sh dev {_TAG} on butlers",
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


def test_deploy_prod_runs_the_real_step_as_a_reminder_and_exits_zero(
    chart: Path, tools: Path
) -> None:
    """Prod is deferred (OD18): the release status stands and the step only reminds."""
    scripts = chart.parents[2] / "scripts/k8s"
    shutil.copy2(_SCRIPT, scripts / "public-mcp-owner-step.sh")
    env = _environment(tools)
    del env["CALL_LOG"]
    log = chart / "calls.log"
    log.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["make", "--no-print-directory", "deploy-prod", f"TAG={_TAG}"],
        cwd=chart, capture_output=True, text=True,
        env={**os.environ, **env, "CALL_LOG": str(log)},
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "not automated for 'prod'" in result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "record-deploy.sh" in calls
    assert "kubectl" not in calls and "git " not in calls and "uv " not in calls
    makefile = (_REPO_ROOT / "deploy/helm/butlers/makefile").read_text(encoding="utf-8")
    assert "\nPUBLIC_MCP_OWNER_STEP_prod := off\n" in makefile


def test_committed_switch_is_on_and_passed_through_when_overridden(chart: Path) -> None:
    makefile = (_REPO_ROOT / "deploy/helm/butlers/makefile").read_text(encoding="utf-8")
    assert "\nPUBLIC_MCP_OWNER_STEP_dev := on\n" in makefile
    _code, calls = _deploy(chart, "PUBLIC_MCP_OWNER_STEP_dev=off")
    assert calls[-1] == f"public-mcp-owner-step.sh dev {_TAG} off butlers"


def test_recipe_with_the_real_step_fails_when_the_guard_refuses(chart: Path, tools: Path) -> None:
    """Stubbed migrations that did not reconcile: the file's guard stops psql, the deploy fails."""
    scripts = chart.parents[2] / "scripts/k8s"
    shutil.copy2(_SCRIPT, scripts / "public-mcp-owner-step.sh")
    shutil.copy2(_GUARD, scripts / "public_mcp_sql_guard.py")
    env = _environment(tools, PSQL_RC="3")
    del env["CALL_LOG"]
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


_UV_IN_IMAGE = r"""#!/bin/sh
echo "uv $*" >> "$CALL_LOG"
case "$*" in
  *"db migrate"*) exit "${MIGRATE_RC:-0}" ;;
  *"butlers public-mcp --help")
    printf '%s\n' "$PUBLIC_MCP_HELP"; [ "${PUBLIC_MCP_RC:-0}" = 0 ] || exit "$PUBLIC_MCP_RC" ;;
  *"butlers --help")
    printf '%s\n' "$TOP_HELP"; [ "${TOP_RC:-0}" = 0 ] || exit "$TOP_RC" ;;
esac
exit 0
"""
_NO_GROUP = "Commands:\n  db        Database commands.\n  up        Start butlers."


@pytest.mark.parametrize(
    ("env", "reconciled", "status", "stderr"),
    [
        ({}, True, 0, ""),
        # An image before #5621: no `public-mcp` group, or a group without `reconcile`.
        ({"TOP_HELP": _NO_GROUP}, False, 0, "not in this image"),
        (
            {"PUBLIC_MCP_HELP": "Commands:\n  reconcile-draft  Not it."},
            False,
            0,
            "not in this image",
        ),
        # A listing that crashes (an import error) fails the rollout instead of skipping.
        ({"PUBLIC_MCP_RC": "1"}, False, 1, ""),
        ({"TOP_RC": "1"}, False, 1, ""),
        ({"MIGRATE_RC": "1"}, False, 1, ""),
    ],
)
def test_dev_migrations_reconcile_last_and_skip_only_an_image_without_it(
    tmp_path: Path, env: dict[str, str], reconciled: bool, status: int, stderr: str
) -> None:
    """The rendered initContainer command, run with a stubbed image `uv`."""
    args = _migrations("values.dev.yaml")
    assert args.startswith(
        "uv run --frozen --no-dev butlers db prepare-chronicler-memory --dir /app/roster"
        " && uv run --frozen --no-dev butlers db migrate --dir /app/roster && "
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(_UV_IN_IMAGE, encoding="utf-8")
    (bin_dir / "uv").chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["/bin/sh", "-c", args],
        capture_output=True, text=True, timeout=60,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALL_LOG": str(log),
            "TOP_HELP": _help(),
            "PUBLIC_MCP_HELP": _help("public-mcp"),
            **env,
        },
    )  # fmt: skip
    calls = log.read_text(encoding="utf-8").splitlines()
    assert result.returncode == status, result.stderr
    reconcile = "uv run --frozen --no-dev butlers public-mcp reconcile --database butlers"
    assert (calls[-1] == reconcile) is reconciled
    assert stderr in result.stderr
    if status:
        assert "not in this image" not in result.stderr
    if "MIGRATE_RC" in env:
        assert not any("--help" in call for call in calls)


@pytest.mark.parametrize("values", [(), ("values.prod.yaml",)])
def test_other_environments_do_not_reconcile(values: tuple[str, ...]) -> None:
    args = _migrations(*values)
    assert "public-mcp" not in args
    assert args.rstrip().endswith(
        "exec uv run --frozen --no-dev butlers db migrate --dir /app/roster"
    )
