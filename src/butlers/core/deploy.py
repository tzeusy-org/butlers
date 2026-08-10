"""One-command idempotent production deploy: build, migrate, recreate, verify, record.

bu-9r3hd.3 (epic bu-9r3hd "Deploy spine"). Replaces the artisanal prod-deploy
ceremony — a human running ``scripts/compose.sh --prod`` (or a bare ``docker
build`` + ``docker compose up -d``) by hand — with one idempotent verb:
``butlers deploy``.

Pipeline: build the ``butlers-app`` image stamped with the current git SHA →
force-rerun the one-shot ``migrations`` service → refresh the bind-mounted
beads export → recreate services under an explicit, hotreload/dev-profile-free
compose invocation → poll ``/health`` → record the outcome to
``public.deployments`` (success **or** failed — a failed deploy is recorded
too, so it is visible in the ledger rather than silent; see
``butlers.core.deployments``).

Three compose/deploy-flow bugs this closes (the first two discovered via
bu-zhfd0, the incident where core_155..161 sat unrun in prod for six days):

- **Stale migrations "succeed" forever.** ``docker compose up -d`` only runs
  the one-shot ``migrations`` service if its ``depends_on: condition:
  service_completed_successfully`` isn't already satisfied — and once that
  container has exited 0 *once*, compose treats it as permanently satisfied,
  even after the image is rebuilt with new migrations baked in. This module
  never relies on that: :func:`run_migrations` always uses ``run --rm``,
  which creates a brand new container every time.
- **Ambient hotreload leakage.** Docker Compose reads ``COMPOSE_PROFILES``
  from the environment even when no ``--profile`` flag is passed — a shell
  that still has ``COMPOSE_PROFILES=hotreload`` set from an earlier dev
  session would silently recreate prod under the bind-mounted hotreload
  services (verified empirically: ``COMPOSE_PROFILES=hotreload docker
  compose config --services`` includes profile-gated services with zero
  ``--profile`` flags on the command line). :func:`_clean_compose_env` strips
  it unconditionally, so the *shell's* profile selection can never leak in.
  ``DeployConfig.profiles`` (bu-hmdqz.1) lets a caller opt into a named
  profile explicitly (e.g. ``("dev",)``, so a project whose frontend service
  is profile-gated doesn't get torn down by ``--remove-orphans``) — but
  ``"hotreload"`` itself can never be one of them (``DeployConfig.__post_init__``
  raises), so this pipeline can never recreate services under the
  bind-mounted, working-tree-sourced containers that motivated this bead in
  the first place — see the "PROD DEPLOYS" note atop ``docker-compose.yml``.
- **Beads export missing/stale in the compose project directory.** bu-hmdqz.6.
  ``docker-compose.yml`` bind-mounts ``./.beads/issues.export.jsonl:ro`` (a
  relative host path, resolved against ``config.repo_root``) into
  ``dashboard-api``/``dashboard-api-hotreload``/``butlers-up``/
  ``butlers-up-hotreload``, feeding ``compute_decision_digest()``
  (``butlers.jobs.decision_review``). ``bd export`` only ever runs on the
  host, never inside a container, and a deploy commonly runs from a snapshot
  worktree whose ``.beads/`` may lack the export entirely or hold a stale
  one. :func:`materialize_beads_export` refreshes it right before
  ``recreate_services`` binds it into the freshly recreated containers, so
  the Decisions lane and weekly digest/escalation jobs are never stuck
  ``decisions_available=False`` purely because of deploy-flow plumbing.

Testability: :func:`run_deploy` accepts an injected ``pool`` so unit tests
can mock every subprocess/HTTP boundary while integration tests exercise the
real ``public.deployments`` write via ``butlers.core.deployments`` against a
migrated Postgres. Actual container recreation cannot be verified in CI —
see the accompanying bead follow-up for host verification.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import httpx

from butlers.core.deployments import record_deployment, resolve_core_migration_head
from butlers.db import db_params_from_env

logger = logging.getLogger(__name__)

DEFAULT_COMPOSE_FILES: tuple[str, ...] = ("docker-compose.yml",)
DEFAULT_PROJECT_NAME = "butlers"
DEFAULT_ENV_FILE = ".env.prod"
DEFAULT_IMAGE_TAG = "latest"
DEFAULT_HEALTH_URL = "http://localhost:41200/health"
DEFAULT_HEALTH_TIMEOUT_S = 180.0
DEFAULT_HEALTH_POLL_INTERVAL_S = 3.0

# This path is intentionally not configurable. The supported deploy path may
# elevate only the root-owned copy installed by
# scripts/install_restore_drill_firewall_wrapper.sh, never checkout code.
RESTORE_DRILL_FIREWALL_WRAPPER = "/usr/local/libexec/butlers-restore-drill-firewall"

_RESTORE_DRILL_ENV_KEYS = frozenset(
    {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "RESTORE_DRILL_EXECUTOR_DB_HOST",
        "RESTORE_DRILL_EXECUTOR_DB_PORT",
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST",
    }
)
_DOTENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class DeployError(RuntimeError):
    """Raised when a deploy pipeline phase fails; carries the phase name.

    ``phase`` is one of ``"build"``, ``"migrate"``, ``"recreate"``,
    ``"health-check"`` — used verbatim in the failed ledger row's log line
    and in the CLI's error message so a failure is immediately actionable.
    """

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(f"{phase}: {message}")
        self.phase = phase


@dataclass(frozen=True)
class RestoreDrillEndpoint:
    """Connection identity and firewall address for the isolated executor.

    ``connection_host`` intentionally remains a DNS name when the operator
    configured one.  The executor's ``extra_hosts`` entry resolves that name
    locally to ``firewall_ipv4``, so libpq/asyncpg retain the TLS identity for
    ``sslmode=verify-full`` without giving the default-deny bridge DNS egress.
    """

    connection_host: str
    firewall_ipv4: str
    port: int

    def compose_environment(self) -> dict[str, str]:
        """Return only the non-secret endpoint values Compose must render."""
        return {
            "RESTORE_DRILL_EXECUTOR_DB_HOST": self.connection_host,
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST": self.firewall_ipv4,
            "RESTORE_DRILL_EXECUTOR_DB_PORT": str(self.port),
        }


#: Profiles this pipeline must never activate. ``hotreload`` bind-mounts
#: source (``./src``, ``./roster``, ...) from the deploy host's working tree
#: into the running containers instead of the baked image -- exactly the
#: "serving from .worktrees/" failure mode bu-hmdqz.1 closes. Explicit
#: opt-in profiles (below) are fine; inheriting this one, ever, is not.
_FORBIDDEN_PROFILES = frozenset({"hotreload"})


@dataclass(frozen=True)
class DeployConfig:
    """Everything one ``butlers deploy`` invocation needs, explicitly.

    ``compose_files`` defaults to the single base file; there is no separate
    "prod" overlay file because the base file's default (profile-less)
    services already are the baked-image, no-bind-mount prod set.

    ``profiles`` lets a caller opt into compose profiles the target project
    actually needs (e.g. ``("dev",)`` for the ``butlers-dev`` project's
    ``frontend-dev`` service) -- but can never contain ``"hotreload"`` (see
    ``_FORBIDDEN_PROFILES``; enforced in ``__post_init__``). This is
    deliberately different from ambient ``COMPOSE_PROFILES`` leakage, which
    ``_clean_compose_env`` strips unconditionally: profiles here are always
    explicit, passed via ``--profile``, never inherited from the shell.
    """

    repo_root: Path
    compose_files: tuple[str, ...] = DEFAULT_COMPOSE_FILES
    project_name: str = DEFAULT_PROJECT_NAME
    env_file: str = DEFAULT_ENV_FILE
    image_tag: str = DEFAULT_IMAGE_TAG
    health_url: str = DEFAULT_HEALTH_URL
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_S
    health_poll_interval_s: float = DEFAULT_HEALTH_POLL_INTERVAL_S
    profiles: tuple[str, ...] = ()
    #: Downgrade the preflight guard (linked-worktree / non-ancestor-HEAD) from
    #: a hard rejection to a loud warning, for an intentional branch deploy.
    #: See :func:`preflight_check`. Off by default: the canonical prod deploy
    #: must fail closed if it is pointed at a frozen worktree or divergent HEAD.
    allow_dirty_root: bool = False

    def __post_init__(self) -> None:
        forbidden = _FORBIDDEN_PROFILES & set(self.profiles)
        if forbidden:
            raise ValueError(
                f"DeployConfig.profiles must never include {sorted(forbidden)} -- "
                "the hotreload profile bind-mounts source from the working tree "
                "(possibly a stale .worktrees/ checkout) instead of the baked "
                "image; see the module docstring."
            )


def _endpoint_values_from_env_file(config: DeployConfig) -> dict[str, str]:
    """Read only endpoint keys from Compose's env file without sourcing it.

    A deploy process must resolve the firewall address before Compose creates
    the credentialed executor.  Shell-sourcing ``.env.prod`` to obtain that
    host would execute operator-controlled syntax, so this intentionally
    narrow parser accepts only ordinary ``KEY=value`` assignments and retains
    only the five non-secret endpoint keys needed below.  Compose still owns
    all other interpolation semantics.
    """
    env_path = config.repo_root / config.env_file
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise DeployError(
            "restore-drill-endpoint",
            f"cannot read the Compose env file {env_path} for the restore-drill endpoint",
        ) from exc

    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DOTENV_ASSIGNMENT.match(stripped)
        if match is None or match.group(1) not in _RESTORE_DRILL_ENV_KEYS:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def _is_ipv4(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False


def _is_dns_name(value: str) -> bool:
    """Accept a conventional DNS identity, including a single Compose label."""
    if not value or len(value) > 253 or value.endswith("."):
        return False
    return all(_DNS_LABEL.fullmatch(label) for label in value.split("."))


def _resolve_restore_drill_endpoint(config: DeployConfig) -> RestoreDrillEndpoint:
    """Resolve the executor's TLS identity and separately constrained IPv4.

    Environment values override the Compose env file exactly as they do for
    Compose interpolation.  A supplied firewall override is accepted only as
    IPv4; otherwise a DNS connection identity is resolved on the deploy host.
    The executor itself never resolves DNS after its bridge is created.
    """
    values = _endpoint_values_from_env_file(config)
    values.update(
        {name: os.environ[name] for name in _RESTORE_DRILL_ENV_KEYS if name in os.environ}
    )

    connection_host = (
        values.get("RESTORE_DRILL_EXECUTOR_DB_HOST") or values.get("POSTGRES_HOST") or ""
    ).strip()
    if not (_is_ipv4(connection_host) or _is_dns_name(connection_host)):
        raise DeployError(
            "restore-drill-endpoint",
            "RESTORE_DRILL_EXECUTOR_DB_HOST (or POSTGRES_HOST) must be a DNS hostname "
            "or IPv4 address",
        )

    raw_port = (
        values.get("RESTORE_DRILL_EXECUTOR_DB_PORT") or values.get("POSTGRES_PORT") or "5432"
    ).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise DeployError(
            "restore-drill-endpoint",
            "RESTORE_DRILL_EXECUTOR_DB_PORT must be an integer in 1..65535",
        ) from exc
    if not 1 <= port <= 65535:
        raise DeployError(
            "restore-drill-endpoint", "RESTORE_DRILL_EXECUTOR_DB_PORT must be in 1..65535"
        )

    configured_firewall_ipv4 = values.get("RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST", "").strip()
    if configured_firewall_ipv4:
        if not _is_ipv4(configured_firewall_ipv4):
            raise DeployError(
                "restore-drill-endpoint",
                "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST must be a resolved IPv4 address",
            )
        firewall_ipv4 = configured_firewall_ipv4
    elif _is_ipv4(connection_host):
        firewall_ipv4 = connection_host
    else:
        try:
            addresses = socket.getaddrinfo(
                connection_host,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise DeployError(
                "restore-drill-endpoint",
                f"could not resolve an IPv4 PostgreSQL endpoint for {connection_host}",
            ) from exc
        firewall_ipv4 = next(
            (address[4][0] for address in addresses if _is_ipv4(address[4][0])), ""
        )
        if not firewall_ipv4:
            raise DeployError(
                "restore-drill-endpoint",
                f"could not resolve an IPv4 PostgreSQL endpoint for {connection_host}",
            )

    return RestoreDrillEndpoint(
        connection_host=connection_host,
        firewall_ipv4=firewall_ipv4,
        port=port,
    )


def resolve_git_sha(repo_root: Path) -> str:
    """Return the current HEAD SHA of *repo_root*'s git checkout.

    Distinct from ``butlers.core.deployments.resolve_git_sha`` (which reads
    the ``GIT_SHA`` env var baked into an already-running container) — this
    one runs on the deploy host, before the image exists, to decide what to
    build and stamp.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _is_linked_worktree(repo_root: Path) -> bool:
    """True if *repo_root* is a linked git worktree rather than the main checkout.

    In the canonical (main) checkout ``.git`` is a *directory*; in a linked
    worktree created by ``git worktree add`` it is instead a *file* holding a
    ``gitdir: <path>`` pointer. This is the exact filesystem tell that
    distinguishes the two — cheaper and more precise than shelling out to
    ``git rev-parse --git-common-dir`` and comparing paths.
    """
    return (repo_root / ".git").is_file()


def _fetch_origin_main(repo_root: Path) -> bool:
    """Best-effort ``git fetch origin main`` so the ancestry check is honest.

    [decision] The ancestry check below is only as truthful as the local
    ``origin/main`` ref. Without a fetch, a checkout that is a strict ancestor
    of a *stale* local ``origin/main`` — yet behind the true remote head —
    would pass the guard. We fetch first so the guard reflects the real remote.
    A fetch failure (offline deploy host, transient network) must NOT hard-fail
    the deploy over a network hiccup, so this logs a warning and returns
    ``False``; the caller proceeds against the last-known ``origin/main`` (the
    guard degrades to "best available" rather than blocking, and the warning
    makes the staleness visible).
    """
    try:
        proc = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "deploy preflight: `git fetch origin main` failed (%s); checking ancestry "
            "against last-known origin/main",
            exc,
        )
        return False
    if proc.returncode != 0:
        logger.warning(
            "deploy preflight: `git fetch origin main` exited %d (%s); checking ancestry "
            "against last-known origin/main",
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip()[-500:],
        )
        return False
    return True


def _head_vs_origin_main(repo_root: Path) -> tuple[bool, int, int]:
    """Return ``(head_is_ancestor_of_origin_main, ahead, behind)``.

    ``head_is_ancestor`` is ``True`` only when ``git merge-base --is-ancestor
    HEAD origin/main`` exits 0 — i.e. HEAD is already contained in the remote
    main line. ``ahead``/``behind`` are HEAD's commit distance from
    ``origin/main`` (``ahead`` = commits on HEAD not on main, ``behind`` =
    commits on main not on HEAD), included in the rejection message so an
    operator can see *how* divergent the checkout is. If either git invocation
    fails (e.g. ``origin/main`` ref absent), ancestry cannot be confirmed and
    is reported ``False`` (fail closed), with zeroed counts. A git binary that
    is missing or cannot execute (``OSError``) is caught here and likewise
    reported as ``(False, 0, 0)`` — a preflight guard must fail closed, never
    crash the deploy with an unhandled traceback.
    """
    try:
        is_ancestor = (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        ahead = behind = 0
        counts = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if counts.returncode == 0:
            parts = counts.stdout.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])
        return is_ancestor, ahead, behind
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "deploy preflight: git ancestry check could not execute (%s); failing closed", exc
        )
        return False, 0, 0


def preflight_check(config: DeployConfig) -> tuple[str, ...]:
    """Refuse to deploy from a frozen worktree or a divergent HEAD.

    The bu-hmdqz.1 incident served stale code for weeks because a deploy was
    pointed at a frozen ``.worktrees/`` checkout. PR #3169 closed the
    *bind-mount* path (the hotreload profile), but ``docker build`` would still
    happily bake a stale or divergent checkout into the image if an operator
    points ``--dir`` at one. This guard runs BEFORE any build/migrate/container
    step and rejects two shapes:

    1. **Linked-worktree root** — ``repo_root`` is a ``git worktree add``
       checkout (``.git`` is a gitdir-pointer file, not a directory). Deploys
       must run from the canonical main checkout.
    2. **Non-ancestor HEAD** — ``HEAD`` is not an ancestor of ``origin/main``
       (after a best-effort ``git fetch origin main``; see
       :func:`_fetch_origin_main`), i.e. the checkout carries commits that were
       never merged to main.

    Returns the tuple of override *reasons* that were downgraded to warnings —
    empty for a clean canonical checkout. When ``config.allow_dirty_root`` is
    set, both rejections become loud ``logger.warning`` lines and the reasons
    are returned (so callers can surface them and the deploy still records its
    real, possibly-divergent ``git_sha`` in ``public.deployments`` — that
    divergent SHA is itself the durable ledger record that a non-main commit
    was deployed, which the drift sentinel already compares against main).
    Otherwise the first-violating shape raises ``DeployError("preflight", ...)``.
    """
    violations: list[str] = []

    if not (config.repo_root / ".git").exists():
        # Not a git checkout at all — flag it explicitly rather than letting the
        # git subprocesses below fail into a confusing "not an ancestor" message
        # (and skip those subprocesses entirely; there is nothing to check).
        violations.append(
            f"deploy root {config.repo_root} is not a git repository (no .git); deploys "
            "must run from the canonical main checkout"
        )
    else:
        if _is_linked_worktree(config.repo_root):
            violations.append(
                f"deploy root {config.repo_root} is a linked git worktree (.git is a "
                "gitdir-pointer file, not a directory); deploys must run from the canonical "
                "main checkout so `docker build` bakes committed code, not a frozen worktree "
                "snapshot (bu-hmdqz.1)"
            )

        _fetch_origin_main(config.repo_root)
        is_ancestor, ahead, behind = _head_vs_origin_main(config.repo_root)
        if not is_ancestor:
            violations.append(
                f"deploy root {config.repo_root} HEAD is not an ancestor of origin/main "
                f"({ahead} commit(s) ahead, {behind} behind); `docker build` would bake this "
                "unmerged/divergent code into the prod image (bu-hmdqz.1)"
            )

    if not violations:
        return ()

    if config.allow_dirty_root:
        for reason in violations:
            logger.warning("deploy preflight OVERRIDDEN (--allow-dirty-root): %s", reason)
        return tuple(violations)

    raise DeployError(
        "preflight",
        "; ".join(violations)
        + " — re-run with allow_dirty_root=True (CLI: --allow-dirty-root) to deploy anyway",
    )


def _compose_base_args(config: DeployConfig) -> list[str]:
    args = ["docker", "compose"]
    for compose_file in config.compose_files:
        args += ["-f", compose_file]
    args += ["-p", config.project_name, "--env-file", config.env_file]
    for profile in config.profiles:
        args += ["--profile", profile]
    return args


def _clean_compose_env() -> dict[str, str]:
    """Subprocess env with ``COMPOSE_PROFILES`` stripped.

    A prod deploy must never inherit an ambient ``COMPOSE_PROFILES=hotreload``
    left over from a dev shell session — see the module docstring for the
    verified behavior this guards against.
    """
    env = os.environ.copy()
    env.pop("COMPOSE_PROFILES", None)
    return env


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    phase: str,
    environment: dict[str, str] | None = None,
    inherit_environment: bool = True,
) -> None:
    env = _clean_compose_env() if inherit_environment else {}
    if environment is not None:
        env.update(environment)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise DeployError(phase, detail[-4000:] or f"exit code {proc.returncode}")


def build_image(config: DeployConfig, git_sha: str) -> None:
    """Build the ``butlers-app`` image, tagged and stamped with *git_sha*."""
    cmd = [
        "docker",
        "build",
        "--build-arg",
        f"GIT_SHA={git_sha}",
        "-t",
        f"butlers-app:{config.image_tag}",
        ".",
    ]
    _run_subprocess(cmd, cwd=config.repo_root, phase="build")


def run_migrations(config: DeployConfig, endpoint: RestoreDrillEndpoint | None = None) -> None:
    """Force-rerun the one-shot ``migrations`` service against the freshly built image.

    Uses ``run --rm`` (not ``up -d``) so a prior exited ``migrations``
    container from an older image can never silently satisfy
    ``service_completed_successfully`` and skip this deploy's migrations —
    see the module docstring (bu-zhfd0).
    """
    cmd = [*_compose_base_args(config), "run", "--rm", "migrations"]
    _run_subprocess(
        cmd,
        cwd=config.repo_root,
        phase="migrate",
        environment=endpoint.compose_environment() if endpoint is not None else None,
    )


def materialize_beads_export(config: DeployConfig) -> bool:
    """Best-effort: (re)generate ``.beads/issues.export.jsonl`` in *config.repo_root*.

    bu-hmdqz.6. ``docker-compose.yml`` bind-mounts
    ``./.beads/issues.export.jsonl:ro`` into ``dashboard-api``,
    ``dashboard-api-hotreload``, ``butlers-up``, and ``butlers-up-hotreload``
    -- a relative *host* path resolved against the compose project directory
    (``config.repo_root``, since ``docker compose`` always runs with that as
    ``cwd`` here). ``bd export`` itself only ever runs on the host (``bd``
    talks to the Dolt server at ``127.0.0.1:3307``, unreachable from inside
    any container -- see ``decision_review.py``'s module docstring), and a
    prod deploy commonly runs from a snapshot worktree (see this module's
    "PROD DEPLOYS" docstring note) whose ``.beads/`` may have no export at
    all, or a stale one from whenever that worktree was last synced. Without
    refreshing it here, right before ``recreate_services`` binds it into the
    freshly recreated containers, ``compute_decision_digest()`` sees a
    missing/stale file and everything downstream of it (dashboard-api's
    ``/api/decisions``, Switchboard's weekly digest/escalation jobs) degrades
    to ``decisions_available=False`` forever -- even though the underlying
    bd/Dolt data is perfectly healthy.

    Best-effort, like :func:`_best_effort_migration_head`: a missing/failing
    ``bd`` binary on the deploy host must never fail the whole prod deploy
    over an ancillary governance surface that already degrades honestly
    on its own when this file is absent or stale -- logs a warning and
    returns ``False`` instead of raising.

    Deliberately exports to ``issues.export.jsonl``, never
    ``issues.jsonl`` -- see the bd 1.0.4 auto-import-loop hazard documented
    in AGENTS.md/CLAUDE.md.

    Ensures *export_path* exists as a regular file before ever touching
    ``bd`` (classic Docker bind-mount trap, flagged in PR #3174 review): if
    the host path backing a ``:ro`` bind mount doesn't exist yet, Docker
    creates a *directory* there to satisfy the mount, and every subsequent
    ``bd export -o`` to that path then fails permanently with
    ``IsADirectoryError`` -- this is not self-healing. Touching an empty
    placeholder first (parent-dir + file, no-op if already a file) means a
    ``bd export`` failure below only ever leaves behind stale/empty content,
    never a directory that wedges the mount forever.
    """
    export_path = config.repo_root / ".beads" / "issues.export.jsonl"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    if not export_path.exists():
        export_path.touch()
    cmd = ["bd", "export", "-o", str(export_path)]
    try:
        proc = subprocess.run(cmd, cwd=config.repo_root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("deploy: could not materialize beads export (%s): %s", cmd, exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "deploy: `bd export` failed (exit %d): %s",
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip()[-2000:],
        )
        return False
    return True


def prepare_restore_drill_executor(config: DeployConfig, endpoint: RestoreDrillEndpoint) -> None:
    """Install the executor's default-deny policy before any ``up`` can run it.

    This is deliberately a deploy phase rather than a best-effort post-start
    hook.  Stopping an old executor first prevents a chain refresh from
    creating an egress window; ``create`` then materializes only its network;
    the root-owned immutable firewall wrapper must succeed before the ordinary recreate
    phase starts any service.
    """
    environment = endpoint.compose_environment()
    _run_subprocess(
        [*_compose_base_args(config), "stop", "restore-drill-executor"],
        cwd=config.repo_root,
        phase="restore-drill-stop",
        environment=environment,
    )
    _run_subprocess(
        [*_compose_base_args(config), "create", "restore-drill-executor"],
        cwd=config.repo_root,
        phase="restore-drill-create",
        environment=environment,
    )
    _run_subprocess(
        [
            "sudo",
            "-n",
            RESTORE_DRILL_FIREWALL_WRAPPER,
            "--project",
            config.project_name,
            "--db-host",
            endpoint.firewall_ipv4,
            "--db-port",
            str(endpoint.port),
        ],
        cwd=config.repo_root,
        phase="restore-drill-firewall",
        # Do not hand ordinary deployment environment (including shared DB
        # credentials) to an elevated subprocess. The immutable wrapper gets
        # only validated, literal endpoint arguments through its fixed path.
        environment={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        inherit_environment=False,
    )


def recreate_services(config: DeployConfig, endpoint: RestoreDrillEndpoint | None = None) -> None:
    """Recreate all prod services. Never passes ``--profile``; see module docstring."""
    cmd = [*_compose_base_args(config), "up", "-d", "--remove-orphans"]
    _run_subprocess(
        cmd,
        cwd=config.repo_root,
        phase="recreate",
        environment=endpoint.compose_environment() if endpoint is not None else None,
    )


async def wait_for_health(config: DeployConfig) -> None:
    """Poll ``/health`` until it reports ``status == "ok"``, or raise after the timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + config.health_timeout_s
    last_error = "no attempt made"
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            try:
                resp = await client.get(config.health_url)
                if resp.status_code == 200 and resp.json().get("status") == "ok":
                    return
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            if loop.time() >= deadline:
                break
            await asyncio.sleep(config.health_poll_interval_s)
    raise DeployError("health-check", f"timed out after {config.health_timeout_s}s: {last_error}")


async def _make_pool() -> asyncpg.Pool:
    params = db_params_from_env()
    database = os.environ.get("POSTGRES_DB", "butlers")
    return await asyncpg.create_pool(database=database, min_size=1, max_size=2, **params)


async def _best_effort_migration_head(pool: asyncpg.Pool) -> str | None:
    # Resolve the CORE chain head from whichever schema actually tracks it
    # (per-butler schema on the live DB), NOT from a hard-coded ``public`` —
    # ``public`` carries no ``alembic_version`` table, and the old
    # ``read_migration_head(pool, "public")`` recorded ``migration_head=None``
    # for every real deploy (bu-l94um). The resolver never raises for the
    # expected-absent case; this wrapper is the final safety net for a genuine
    # DB failure, which still logs loudly.
    try:
        return await resolve_core_migration_head(pool)
    except Exception:
        logger.warning("deploy: could not read migration head for ledger row", exc_info=True)
        return None


@dataclass(frozen=True)
class DeployResult:
    git_sha: str
    migration_head: str | None
    result: str  # "success" | "failed"
    #: Preflight guard reasons that were downgraded to warnings via
    #: ``allow_dirty_root`` (empty for a clean canonical-checkout deploy).
    overrides: tuple[str, ...] = ()


async def run_deploy(config: DeployConfig, *, pool: asyncpg.Pool | None = None) -> DeployResult:
    """Run the full deploy pipeline; always records a ledger row.

    Idempotent: every phase is safe to re-run (``docker build`` reuses
    layer cache, ``docker compose run --rm`` always starts a fresh
    container, ``docker compose up -d`` only recreates what changed, and
    each pipeline run inserts a new ledger row rather than mutating one).

    Raises :class:`DeployError` on failure, after recording a ``"failed"``
    row, so the caller can exit non-zero with a phase-specific message.

    Parameters
    ----------
    pool:
        Injected asyncpg pool (tests use this to point at a real migrated
        Postgres without touching env vars). Production callers omit it —
        a pool is created from ``POSTGRES_*`` env vars and closed on exit.
    """
    # Preflight FIRST, before touching the pool or building anything: refuse
    # (or, under allow_dirty_root, loudly warn about) a frozen-worktree root or
    # a HEAD that never landed on main. A rejection here means nothing was
    # attempted, so no ledger row is written — this is a refusal to deploy, not
    # a deploy failure.
    overrides = preflight_check(config)
    git_sha = resolve_git_sha(config.repo_root)
    owns_pool = pool is None
    if pool is None:
        pool = await _make_pool()
    try:
        try:
            endpoint = _resolve_restore_drill_endpoint(config)
            build_image(config, git_sha)
            run_migrations(config, endpoint)
            materialize_beads_export(config)
            prepare_restore_drill_executor(config, endpoint)
            recreate_services(config, endpoint)
            await wait_for_health(config)
        except DeployError as exc:
            migration_head = await _best_effort_migration_head(pool)
            await record_deployment(
                pool,
                git_sha=git_sha,
                migration_head=migration_head,
                result="failed",
                source="deploy",
                serving_mode="image",
                serving_worktree=None,
            )
            logger.error("butlers deploy failed at phase=%s: %s", exc.phase, exc)
            raise

        migration_head = await _best_effort_migration_head(pool)
        await record_deployment(
            pool,
            git_sha=git_sha,
            migration_head=migration_head,
            result="success",
            source="deploy",
            serving_mode="image",
            serving_worktree=None,
        )
        return DeployResult(
            git_sha=git_sha,
            migration_head=migration_head,
            result="success",
            overrides=overrides,
        )
    finally:
        if owns_pool:
            await pool.close()
