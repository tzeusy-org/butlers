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
import logging
import os
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


class DeployError(RuntimeError):
    """Raised when a deploy pipeline phase fails; carries the phase name.

    ``phase`` is one of ``"build"``, ``"migrate"``, ``"recreate"``,
    ``"health-check"`` — used verbatim in the failed ledger row's log line
    and in the CLI's error message so a failure is immediately actionable.
    """

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(f"{phase}: {message}")
        self.phase = phase


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

    def __post_init__(self) -> None:
        forbidden = _FORBIDDEN_PROFILES & set(self.profiles)
        if forbidden:
            raise ValueError(
                f"DeployConfig.profiles must never include {sorted(forbidden)} -- "
                "the hotreload profile bind-mounts source from the working tree "
                "(possibly a stale .worktrees/ checkout) instead of the baked "
                "image; see the module docstring."
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


def _run_subprocess(cmd: list[str], *, cwd: Path, phase: str) -> None:
    proc = subprocess.run(cmd, cwd=cwd, env=_clean_compose_env(), capture_output=True, text=True)
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


def run_migrations(config: DeployConfig) -> None:
    """Force-rerun the one-shot ``migrations`` service against the freshly built image.

    Uses ``run --rm`` (not ``up -d``) so a prior exited ``migrations``
    container from an older image can never silently satisfy
    ``service_completed_successfully`` and skip this deploy's migrations —
    see the module docstring (bu-zhfd0).
    """
    cmd = [*_compose_base_args(config), "run", "--rm", "migrations"]
    _run_subprocess(cmd, cwd=config.repo_root, phase="migrate")


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


def recreate_services(config: DeployConfig) -> None:
    """Recreate all prod services. Never passes ``--profile``; see module docstring."""
    cmd = [*_compose_base_args(config), "up", "-d", "--remove-orphans"]
    _run_subprocess(cmd, cwd=config.repo_root, phase="recreate")


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
    git_sha = resolve_git_sha(config.repo_root)
    owns_pool = pool is None
    if pool is None:
        pool = await _make_pool()
    try:
        try:
            build_image(config, git_sha)
            run_migrations(config)
            materialize_beads_export(config)
            recreate_services(config)
            await wait_for_health(config)
        except DeployError as exc:
            migration_head = await _best_effort_migration_head(pool)
            await record_deployment(
                pool, git_sha=git_sha, migration_head=migration_head, result="failed"
            )
            logger.error("butlers deploy failed at phase=%s: %s", exc.phase, exc)
            raise

        migration_head = await _best_effort_migration_head(pool)
        await record_deployment(
            pool, git_sha=git_sha, migration_head=migration_head, result="success"
        )
        return DeployResult(git_sha=git_sha, migration_head=migration_head, result="success")
    finally:
        if owns_pool:
            await pool.close()
