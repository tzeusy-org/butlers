"""CLI for the Butlers framework — manage butler daemons."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
from collections import defaultdict
from pathlib import Path

import click

from butlers.api.owner_auth.host_cli import auth as owner_auth_commands
from butlers.config import ConfigError, load_config
from butlers.db import is_db_unreachable

logger = logging.getLogger(__name__)

# Default directory containing butler configurations
DEFAULT_BUTLERS_DIR = Path("roster")


def _configure_logging() -> None:
    """Configure structured logging with sensible CLI defaults."""
    from butlers.core.logging import configure_logging

    configure_logging()  # defaults: INFO, text, no file


def _parse_comma_separated(ctx, param, value):
    """Parse comma-separated butler names from --only flag."""
    if not value:
        return ()
    # Split on comma and strip whitespace
    return tuple(name.strip() for name in value.split(",") if name.strip())


def _check_port_status(port: int, timeout: float = 0.5) -> bool:
    """Check if a butler is running by attempting to connect to its port.

    Parameters
    ----------
    port:
        The port number to check.
    timeout:
        Connection timeout in seconds.

    Returns
    -------
    bool
        True if the port is open (butler is running), False otherwise.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("localhost", port))
        return True
    except (TimeoutError, OSError):
        return False
    finally:
        sock.close()


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """Butlers — AI agent framework with pluggable MCP server daemons."""
    _configure_logging()


@cli.command()
@click.option(
    "--only",
    multiple=True,
    help="Start only specific butlers (repeatable, or comma-separated)",
)
@click.option(
    "--dir",
    "butlers_dir",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_BUTLERS_DIR,
    help="Directory containing butler configs",
)
@click.option("--exclude", multiple=True, help="Exclude butlers (repeatable or comma-separated)")
def up(only: tuple[str, ...], butlers_dir: Path, exclude: tuple[str, ...]) -> None:
    """Start all butler daemons (or filtered by --only)."""
    # Flatten: support both --only a --only b and --only a,b
    only = tuple(name.strip() for entry in only for name in entry.split(",") if name.strip())

    configs = _discover_configs(butlers_dir)
    if not configs:
        click.echo(f"No butler configs found in {butlers_dir}/")
        sys.exit(1)

    if only:
        configs = {name: path for name, path in configs.items() if name in only}
        missing = set(only) - set(configs.keys())
        if missing:
            click.echo(f"Butler(s) not found: {', '.join(sorted(missing))}")
            sys.exit(1)

    excluded = {name.strip() for entry in exclude for name in entry.split(",") if name.strip()}
    configs = {name: path for name, path in configs.items() if name not in excluded}
    if not configs:
        raise click.ClickException("No butlers remain after exclusions")

    # Check for port conflicts before starting any butler
    conflicts = _check_port_conflicts(configs)
    if conflicts:
        click.echo("Port conflict detected:")
        for port, butler_names in sorted(conflicts.items()):
            names_str = ", ".join(sorted(butler_names))
            click.echo(f"  Port {port}: {names_str}")
        sys.exit(1)

    ordered_names = [n for n, _ in _ordered_configs(configs)]
    click.echo(f"Starting {len(configs)} butler(s): {', '.join(ordered_names)}")
    asyncio.run(_start_all(configs))


@cli.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to butler config directory",
)
def run(config_path: Path) -> None:
    """Start a single butler daemon from a config directory."""
    click.echo(f"Starting butler from {config_path}")
    asyncio.run(_start_single(config_path))


@cli.command()
@click.option("--profile", type=click.Choice(["KEY_ABSENT_STAGE"]), required=True)
@click.option(
    "--config", "config_path", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--session-host",
    default="127.0.0.1",
    show_default=True,
    help="Bind session refusal port to this operator/deployment-selected interface",
)
def control(profile: str, config_path: Path, session_host: str) -> None:
    """Run Switchboard separately, without any goal signing key or broker."""
    from butlers.control import run_control

    asyncio.run(run_control(config_path, profile=profile, session_host=session_host))


@cli.command("list")
@click.option(
    "--dir",
    "butlers_dir",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_BUTLERS_DIR,
    help="Directory containing butler configs",
)
def list_cmd(butlers_dir: Path) -> None:
    """List all discovered butler configurations."""
    configs = _discover_configs(butlers_dir)
    if not configs:
        click.echo(f"No butler configs found in {butlers_dir}/")
        return

    # Print header
    click.echo(f"{'Name':<20} {'Port':<8} {'Status':<10} {'Modules':<30} {'Description'}")
    click.echo("-" * 90)

    for name, config_dir in sorted(configs.items()):
        try:
            config = load_config(config_dir)
            modules = ", ".join(sorted(config.modules.keys())) or "(none)"
            desc = config.description or ""

            # Check if butler is running
            status = "running" if _check_port_status(config.port) else "stopped"

            click.echo(f"{config.name:<20} {config.port:<8} {status:<10} {modules:<30} {desc}")
        except ConfigError as exc:
            click.echo(f"{name:<20} {'ERROR':<8} {'N/A':<10} {exc!s}")


@cli.command()
@click.argument("name")
@click.option(
    "--port",
    type=int,
    default=int(os.environ.get("BUTLER_MCP_PORT", "41100")),
    show_default=True,
    help="Port for the butler's MCP server",
)
@click.option(
    "--dir",
    "butlers_dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_BUTLERS_DIR,
    help="Parent directory for butler configs",
)
def init(name: str, port: int, butlers_dir: Path) -> None:
    """Scaffold a new butler configuration directory."""
    butler_dir = butlers_dir / name
    if butler_dir.exists():
        click.echo(f"Directory already exists: {butler_dir}")
        sys.exit(1)

    butler_dir.mkdir(parents=True)
    (butler_dir / ".agents" / "skills").mkdir(parents=True)
    (butler_dir / ".claude").symlink_to(".agents")

    # butler.toml
    toml_content = f"""[butler]
name = "{name}"
port = {port}
description = ""

[butler.db]
name = "butlers"
schema = "{name}"
"""
    (butler_dir / "butler.toml").write_text(toml_content)

    # CLAUDE.md
    claude_md = f"# {name.title()} Butler\n\nYou are {name}, a butler AI assistant.\n"
    (butler_dir / "CLAUDE.md").write_text(claude_md)

    # AGENTS.md
    (butler_dir / "AGENTS.md").write_text("# Notes to self\n")

    click.echo(f"Created butler scaffold: {butler_dir}/")


@cli.command()
@click.option(
    "--host",
    default="0.0.0.0",
    show_default=True,
    help="Bind address for the dashboard server",
)
@click.option(
    "--port",
    type=int,
    default=int(os.environ.get("DASHBOARD_PORT", "41200")),
    show_default=True,
    help="Port for the dashboard server",
)
def dashboard(host: str, port: int) -> None:
    """Start the Butlers dashboard web UI."""
    import uvicorn

    click.echo(f"Starting Butlers dashboard on {host}:{port}")
    uvicorn.run(
        "butlers.api.app:create_app",
        host=host,
        port=port,
        factory=True,
        proxy_headers=False,
        access_log=False,
    )


@cli.command()
@click.option(
    "--dir",
    "repo_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Repo root containing docker-compose.yml and .env.prod",
)
@click.option(
    "--timeout",
    "health_timeout_s",
    type=float,
    default=180.0,
    show_default=True,
    help="Seconds to wait for /health before failing the deploy",
)
@click.option(
    "--project-name",
    "project_name",
    default=None,
    help=(
        "Docker Compose project name (default: butlers). Pass 'butlers-dev' "
        "to target the dev/live stack instead of the .env.prod project."
    ),
)
@click.option(
    "--env-file",
    "env_file",
    default=None,
    help="Env file for docker compose --env-file (default: .env.prod)",
)
@click.option(
    "--health-url",
    "health_url",
    default=None,
    help="Health-check URL to poll after recreate (default: http://localhost:41200/health)",
)
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    help=(
        "Compose profile to activate (repeatable), e.g. --profile dev for a "
        "project whose frontend service is profile-gated. Never 'hotreload' "
        "-- DeployConfig rejects it."
    ),
)
@click.option(
    "--allow-dirty-root",
    "allow_dirty_root",
    is_flag=True,
    default=False,
    help=(
        "Downgrade the preflight guard from a hard rejection to a loud warning, "
        "for an intentional branch deploy. Without it, deploying from a linked "
        "git worktree or a HEAD that is not an ancestor of origin/main is "
        "refused before any build/migrate step (bu-hmdqz.1 stale-code incident)."
    ),
)
def deploy(
    repo_root: Path,
    health_timeout_s: float,
    project_name: str | None,
    env_file: str | None,
    health_url: str | None,
    profiles: tuple[str, ...],
    allow_dirty_root: bool,
) -> None:
    """Idempotent production deploy: build, migrate, recreate, verify, record.

    Replaces the artisanal deploy ceremony (build an image by hand, hope
    ``docker compose up -d`` reruns migrations, eyeball that things came back
    up) with one verb: builds the ``butlers-app`` image stamped with the
    current git SHA, force-reruns the one-shot migrations service (never
    trusts a stale exited container — see bu-zhfd0), prepares the protected
    restore-drill Compose overlay plus only explicitly-requested ``--profile``
    flags (never an ambient ``COMPOSE_PROFILES=hotreload``
    leftover from a dev shell, and never ``hotreload`` itself, which
    bind-mounts source instead of using the baked image — see bu-hmdqz.1),
    polls ``/health``, and records the outcome to
    ``public.deployments`` whether it succeeds or fails.

    ``--project-name``/``--env-file``/``--health-url`` default to the
    ``.env.prod`` / ``butlers`` project; pass ``--project-name butlers-dev
    --env-file .env.dev --health-url http://localhost:42200/health --profile
    dev`` to redeploy the dev/live stack instead (its ``frontend-dev`` service
    is gated behind the ``dev`` profile, so it must be requested explicitly or
    ``--remove-orphans`` would tear it down).
    """
    from butlers.core.deploy import DeployConfig, DeployError, run_deploy

    config_kwargs: dict[str, object] = {
        "repo_root": repo_root.resolve(),
        "health_timeout_s": health_timeout_s,
        "profiles": tuple(profiles),
        "allow_dirty_root": allow_dirty_root,
    }
    if project_name is not None:
        config_kwargs["project_name"] = project_name
    if env_file is not None:
        config_kwargs["env_file"] = env_file
    if health_url is not None:
        config_kwargs["health_url"] = health_url

    try:
        config = DeployConfig(**config_kwargs)
    except ValueError as exc:
        click.echo(f"Invalid deploy configuration: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Deploying {config.repo_root} (project={config.project_name})...")
    try:
        result = asyncio.run(run_deploy(config))
    except DeployError as exc:
        click.echo(f"Deploy failed at phase={exc.phase}: {exc}", err=True)
        sys.exit(1)
    for reason in result.overrides:
        click.echo(f"WARNING: preflight guard overridden (--allow-dirty-root): {reason}", err=True)
    click.echo(
        f"Deployed {result.git_sha} (migration_head={result.migration_head}, "
        f"result={result.result})"
    )


@cli.group()
def db() -> None:
    """Database management commands."""


@db.command()
@click.option("--migration-role", required=True, help="Existing distinct migration login")
def bootstrap(migration_role: str) -> None:
    """Prepare source-owned roles/extensions with an explicit administrative login.

    No application body or blanket future-object grant is installed. This
    operator action does not run as part of serving startup or migration retry.
    """
    from urllib.parse import unquote, urlparse

    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from butlers.bootstrap_prerequisite import prepare_bootstrap_connection
    from butlers.db_authority.roles import bootstrap_role_manifest

    selected = os.environ.get("BUTLERS_BOOTSTRAP_DATABASE_URL")
    if not selected:
        raise click.ClickException("Explicit bootstrap database configuration is required")
    database = unquote(urlparse(selected).path.lstrip("/"))
    # Compile before opening a connection, including malformed identity refusal.
    try:
        bootstrap_role_manifest(database, migration_role, source_database=database)
    except Exception as exc:
        raise click.ClickException("Bootstrap source identity is unavailable") from exc
    engine = create_engine(
        selected,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    raw = None
    try:
        raw = engine.raw_connection()
        prepare_bootstrap_connection(raw, database, migration_role, source_database=database)
    except Exception as exc:
        raise click.ClickException("Selected database bootstrap did not complete") from exc
    finally:
        try:
            if raw is not None:
                raw.close()
        finally:
            engine.dispose()
    click.echo("Selected database bootstrap preparation complete.")


@db.command()
@click.option(
    "--only",
    callback=_parse_comma_separated,
    default="",
    help="Migrate only specific butlers (comma-separated)",
)
@click.option(
    "--dir",
    "butlers_dir",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_BUTLERS_DIR,
    help="Directory containing butler configs",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what migrations would run without executing them",
)
def migrate(only: tuple[str, ...], butlers_dir: Path, dry_run: bool) -> None:
    """Run Alembic migrations for all (or selected) butler schemas.

    Owns core, domain and module upgrades plus final authority verification on
    one explicit migrator session. Serving daemons only verify installed state.
    Complete operator bootstrap and ownership prerequisites first.
    """
    configs = _discover_configs(butlers_dir)
    if not configs:
        click.echo(f"No butler configs found in {butlers_dir}/")
        sys.exit(1)

    if only:
        configs = {name: path for name, path in configs.items() if name in only}
        missing = set(only) - set(configs.keys())
        if missing:
            click.echo(f"Butler(s) not found: {', '.join(sorted(missing))}")
            sys.exit(1)

    if dry_run:
        click.echo("Dry run — no migrations will be executed.")
    asyncio.run(_migrate_all(configs, dry_run=dry_run))


@db.command()
@click.option(
    "--only",
    callback=_parse_comma_separated,
    default="",
    help="Provision only specific butlers (comma-separated)",
)
@click.option(
    "--dir",
    "butlers_dir",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_BUTLERS_DIR,
    help="Directory containing butler configs",
)
@click.option("--owner-role", required=True, help="Existing nonprivileged normal database owner")
def provision(only: tuple[str, ...], butlers_dir: Path, owner_role: str) -> None:
    """Provision PostgreSQL databases for all (or selected) butlers."""
    configs = _discover_configs(butlers_dir)
    if not configs:
        click.echo(f"No butler configs found in {butlers_dir}/")
        sys.exit(1)

    if only:
        configs = {name: path for name, path in configs.items() if name in only}
        missing = set(only) - set(configs.keys())
        if missing:
            click.echo(f"Butler(s) not found: {', '.join(sorted(missing))}")
            sys.exit(1)

    asyncio.run(_provision_databases(configs, owner_role=owner_role))


async def _migrate_all(configs: dict[str, Path], *, dry_run: bool = False) -> None:
    """Run declared chains and authority finalization under one selected session.

    Serving credentials are never a migrator fallback. The explicit URL selects
    one database; every requested roster configuration must belong to it before
    any connection or DDL is attempted.
    """
    from urllib.parse import unquote, urlparse

    from butlers.migrations import (
        finalize_authority,
        get_module_migration_chains,
        has_butler_chain,
        migration_session,
        run_migrations,
    )
    from butlers.modules.registry import default_registry

    registry = default_registry()
    plan = []
    for name, config_dir in sorted(configs.items()):
        config = load_config(config_dir)
        schema = config.db_schema
        modules = registry.load_from_config(config.modules)
        chains = [("core", schema)]
        if has_butler_chain(name):
            chains.append((name, schema))
        for module_name, chain in get_module_migration_chains(modules, name).items():
            module_cfg = config.modules.get(module_name) or {}
            chains.append((chain, module_cfg.get("memory_schema") or schema))
        plan.append((name, config.db_name or "butlers", chains))
    if dry_run:
        for name, _, chains in plan:
            click.echo(f"  {name}: would run chains: {', '.join(chain for chain, _ in chains)}")
        return
    db_url = os.environ.get("BUTLERS_MIGRATOR_DATABASE_URL")
    if not db_url:
        raise click.ClickException("Explicit migrator database configuration is required")
    selected = unquote(urlparse(db_url).path.lstrip("/"))
    if not selected or any(database != selected for _, database, _ in plan):
        raise click.ClickException("Migration plan does not match the selected database")
    if not plan:
        return
    try:
        with migration_session(db_url) as connection:
            for name, _, chains in plan:
                for chain, schema in chains:
                    click.echo(f"  {name}: {chain} (schema={schema})...", nl=False)
                    await run_migrations(db_url, chain=chain, schema=schema, connection=connection)
                    click.echo(" done")
            finalize_authority(
                connection,
                plans=tuple(
                    (chain, schema, None) for _, _, chains in plan for chain, schema in chains
                ),
            )
    except Exception as exc:
        raise click.ClickException("Selected database migration did not complete") from exc
    click.echo("All migrations and authority verification complete.")


async def _provision_databases(configs: dict[str, Path], *, owner_role: str) -> None:
    """Use the selected bootstrap connection, never daemon credentials."""
    from butlers.db import Database

    dsn = os.environ.get("BUTLERS_BOOTSTRAP_DATABASE_URL")
    if not dsn:
        raise click.ClickException("Explicit provisioning database configuration is required")
    for name, config_dir in sorted(configs.items()):
        config = load_config(config_dir)
        database = Database(db_name=config.db_name or "butlers")
        try:
            await database.provision(administrative_dsn=dsn, owner_role=owner_role)
        except Exception as exc:
            raise click.ClickException("Selected database provisioning did not complete") from exc
        click.echo(f"  {name}: provisioned")


def _discover_configs(butlers_dir: Path) -> dict[str, Path]:
    """Discover all butler.toml configs in a directory.

    Returns a dict mapping butler name to config directory path.
    """
    configs: dict[str, Path] = {}
    if not butlers_dir.is_dir():
        return configs

    for entry in sorted(butlers_dir.iterdir()):
        if entry.is_dir() and (entry / "butler.toml").exists():
            try:
                config = load_config(entry)
                configs[config.name] = entry
            except ConfigError:
                logger.warning("Invalid config in %s, skipping", entry)

    return configs


def _check_port_conflicts(configs: dict[str, Path]) -> dict[int, list[str]]:
    """Check for port conflicts among butler configurations.

    Parameters
    ----------
    configs:
        Mapping of butler name to config directory path.

    Returns
    -------
    dict[int, list[str]]
        Mapping of conflicting ports to lists of butler names using that port.
        Empty dict if no conflicts.
    """
    port_to_butlers: dict[int, list[str]] = defaultdict(list)

    for name, config_dir in configs.items():
        try:
            config = load_config(config_dir)
            port_to_butlers[config.port].append(config.name)
        except ConfigError:
            logger.warning("Could not load config for %s, skipping conflict check", name)

    # Return only ports with multiple butlers
    return {port: names for port, names in port_to_butlers.items() if len(names) > 1}


_PORT_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt
_PORT_RETRY_MAX_DELAY = 300.0  # cap at 5 minutes
_PORT_RETRY_MAX_ATTEMPTS = 5  # skip butler after this many retries

_DB_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt
_DB_RETRY_MAX_DELAY = 30.0  # cap per-attempt backoff
_DB_RETRY_MAX_ATTEMPTS = 10  # ~140s total budget — covers Tailscale/DB blips

# Butlers that must start before the alphabetical bulk.  Order matters:
# switchboard is the routing backbone — every other butler needs it for notify().
_PRIORITY_BUTLERS = ("switchboard",)


def _is_port_conflict(exc: BaseException) -> bool:
    """Return True if *exc* is an OSError caused by EADDRINUSE."""
    import errno

    return isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.EADDRINUSE


def _is_db_unreachable(exc: BaseException) -> bool:
    """Compatibility wrapper for the canonical transient DB-connect classifier."""
    return is_db_unreachable(exc)


def _ordered_configs(configs: dict[str, Path]) -> list[tuple[str, Path]]:
    """Return configs ordered by priority: _PRIORITY_BUTLERS first, then alphabetical."""
    priority = [(n, configs[n]) for n in _PRIORITY_BUTLERS if n in configs]
    rest = sorted((n, p) for n, p in configs.items() if n not in _PRIORITY_BUTLERS)
    return priority + rest


async def _record_deployment_boot(daemons: list, *, configured_count: int) -> None:
    """Record this process boot in ``public.deployments`` (best-effort).

    Called once per ``butlers up`` invocation — not once per butler daemon —
    since every butler shares one process/container here (see
    ``core_163_deployments_ledger.py``). Uses the first successfully started
    daemon's pool to write (``_PRIORITY_BUTLERS`` puts switchboard first when
    present) and to read a representative migration head. Never blocks or
    fails startup: a ledger-write failure is logged and swallowed, matching
    the ``_ensure_owner_entity`` bootstrap convention.
    """
    from butlers.core.deployments import (
        detect_boot_serving_provenance,
        read_migration_head,
        record_deployment,
        resolve_git_sha,
    )

    primary = daemons[0]
    if primary.db is None or primary.db.pool is None:
        logger.warning("deployments: no DB pool available on primary daemon; skipping record")
        return

    schema = primary.config.db_schema or primary.config.name
    try:
        migration_head = await read_migration_head(primary.db.pool, schema)
        result = "success" if len(daemons) == configured_count else "failed"
        serving = detect_boot_serving_provenance()
        await record_deployment(
            primary.db.pool,
            git_sha=resolve_git_sha(),
            migration_head=migration_head,
            result=result,
            source="boot",
            serving_mode=serving.serving_mode,
            serving_worktree=serving.serving_worktree,
        )
    except Exception:
        logger.warning("Failed to record deployment ledger row (best-effort)", exc_info=True)


async def _start_all(configs: dict[str, Path]) -> None:
    """Start all butler daemons in a single event loop."""
    from butlers.credential_store import credential_purpose_config_from_env
    from butlers.daemon import ButlerDaemon

    credential_purpose = credential_purpose_config_from_env()

    daemons: list[ButlerDaemon] = []
    loop = asyncio.get_event_loop()

    # Set up signal handling
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        click.echo("\nShutting down...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Start daemons: priority butlers first, then alphabetical.
    # Port conflicts retry up to _PORT_RETRY_MAX_ATTEMPTS before skipping.
    # Transient DB-connect failures retry up to _DB_RETRY_MAX_ATTEMPTS — covers
    # short-lived Tailscale/DB blips during startup.
    for name, config_dir in _ordered_configs(configs):
        started = False
        port_attempt = 0
        db_attempt = 0
        while not started:
            daemon = ButlerDaemon(config_dir, credential_purpose=credential_purpose)
            try:
                await daemon.start()
                daemons.append(daemon)
                click.echo(f"  started: {name}")
                started = True
            except Exception as exc:
                # daemon.start() may have already pre-bound the MCP socket and
                # launched the server task (lifecycle step 14) before a later
                # step raised.  A failed start that is retried or skipped without
                # releasing those resources leaves the port bound, so the next
                # attempt collides with "port still in use".  Tear the failed
                # daemon down before retrying/skipping.  shutdown() is fully
                # guarded (every step is None-checked), so it is safe to call on
                # a partially-started daemon; we still wrap it so a cleanup error
                # never masks the original start failure or crashes the loop.
                try:
                    await daemon.shutdown()
                except Exception:
                    logger.warning(
                        "Error releasing resources for failed butler %s", name, exc_info=True
                    )
                if _is_port_conflict(exc) and port_attempt < _PORT_RETRY_MAX_ATTEMPTS:
                    delay = min(_PORT_RETRY_BASE_DELAY * (2**port_attempt), _PORT_RETRY_MAX_DELAY)
                    port_attempt += 1
                    click.echo(
                        f"  {name}: port in use, retrying in {delay:.0f}s"
                        f" (attempt {port_attempt}/{_PORT_RETRY_MAX_ATTEMPTS})..."
                    )
                    await asyncio.sleep(delay)
                elif _is_port_conflict(exc):
                    click.echo(
                        f"  skipped: {name}: port still in use after"
                        f" {_PORT_RETRY_MAX_ATTEMPTS} attempts"
                    )
                    break
                elif _is_db_unreachable(exc) and db_attempt < _DB_RETRY_MAX_ATTEMPTS:
                    delay = min(_DB_RETRY_BASE_DELAY * (2**db_attempt), _DB_RETRY_MAX_DELAY)
                    db_attempt += 1
                    click.echo(
                        f"  {name}: DB unreachable ({exc}), retrying in {delay:.0f}s"
                        f" (attempt {db_attempt}/{_DB_RETRY_MAX_ATTEMPTS})..."
                    )
                    await asyncio.sleep(delay)
                elif _is_db_unreachable(exc):
                    click.echo(
                        f"  failed: {name}: DB unreachable after"
                        f" {_DB_RETRY_MAX_ATTEMPTS} attempts: {exc}"
                    )
                    break
                else:
                    click.echo(f"  failed: {name}: {exc}")
                    break
        if not started:
            logger.warning("Butler %s failed to start", name)

    if not daemons:
        click.echo("No butlers started successfully")
        return

    await _record_deployment_boot(daemons, configured_count=len(configs))

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown
    for daemon in reversed(daemons):
        await daemon.shutdown()


async def _start_single(config_path: Path) -> None:
    """Start a single butler daemon."""
    from butlers.credential_store import credential_purpose_config_from_env
    from butlers.daemon import ButlerDaemon

    credential_purpose = credential_purpose_config_from_env()

    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        click.echo("\nShutting down...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    daemon = ButlerDaemon(config_path, credential_purpose=credential_purpose)
    await daemon.start()
    click.echo(f"Butler {daemon.config.name} running on port {daemon.config.port}")

    await shutdown_event.wait()
    await daemon.shutdown()


# Host-only commands are deliberately absent from the dashboard HTTP router.

cli.add_command(owner_auth_commands)


def _rollback_capsule_path(path: Path) -> Path:
    """Keep operator catalog artifacts out of the checkout; never follow links."""
    import stat

    resolved = path.resolve()
    if path.is_symlink() or any((parent / ".git").exists() for parent in resolved.parents):
        raise click.ClickException("Rollback catalog must be an owned external file")
    if path.exists():
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 8 * 1024 * 1024
        ):
            raise click.ClickException("Rollback catalog ownership/mode/size is unsupported")
    return resolved


def _load_rollback_capsule(path: Path, expected_sha256: str):
    import hashlib
    import json
    import re

    path = _rollback_capsule_path(path)
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise click.ClickException("Exact rollback catalog SHA256 is required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        import stat

        actual = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_uid != os.getuid()
            or stat.S_IMODE(actual.st_mode) != 0o600
        ):
            raise click.ClickException("Opened rollback catalog ownership/mode differs")
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise click.ClickException("Rollback catalog bytes differ from the selected artifact")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise click.ClickException("Rollback catalog encoding is unsupported") from exc


@db.command("rollback-capture")
@click.option("--migration-role", required=True)
@click.option("--chart", required=True, help="Pinned predecessor chart artifact identity")
@click.option("--image", required=True, help="Pinned predecessor image artifact identity")
@click.option(
    "--old-secret-key", required=True, help="Retained secret KEY reference, never its value"
)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def rollback_capture(migration_role, chart, image, old_secret_key, output):
    """Capture nonsecret authority BEFORE cutover, on the explicit admin session."""
    import hashlib
    import json

    from butlers.db_authority.apply import capture_rollback_capsule
    from butlers.migrations import operator_rollback_session

    selected = os.environ.get("BUTLERS_BOOTSTRAP_DATABASE_URL")
    if not selected:
        raise click.ClickException("Explicit administrative database configuration is required")
    output = _rollback_capsule_path(output)
    try:
        with operator_rollback_session(selected) as connection:
            capsule = capture_rollback_capsule(
                connection,
                migration_role,
                {
                    "chart": chart,
                    "image": image,
                    "old_secret": old_secret_key,
                },
            )
        data = (json.dumps(capsule, sort_keys=True, indent=2, default=str) + "\n").encode()
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    except Exception as exc:
        raise click.ClickException("Predecessor catalog capture did not complete") from exc
    click.echo("Predecessor catalog captured; SHA256=" + hashlib.sha256(data).hexdigest())


@db.command("rollback-catalog")
@click.option("--transition", type=click.Choice(["R0", "R1"]), required=True)
@click.option("--capsule", type=click.Path(path_type=Path), required=True)
@click.option(
    "--sha256", required=True, help="Independently retained exact catalog artifact SHA256"
)
def rollback_catalog(transition, capsule, sha256):
    """Restore pinned catalog/stamps; deployment/profile restoration stays explicit.

    This operator command never reads or falls back to a serving credential.
    It returns only after the physical held session is committed and released.
    """
    from butlers.migrations import run_preserving_rollback

    selected = os.environ.get("BUTLERS_BOOTSTRAP_DATABASE_URL")
    if not selected:
        raise click.ClickException("Explicit administrative database configuration is required")
    try:
        document = _load_rollback_capsule(capsule, sha256)
        run_preserving_rollback(selected, document, transition=transition)
    except Exception as exc:
        raise click.ClickException(
            "Pinned catalog rollback did not complete; retain stopped state"
        ) from exc
    click.echo(
        "Pinned predecessor catalog/stamps restored. Restore the recorded predecessor "
        "chart/image/key references and verify its actual serving profile before traffic."
    )


@db.command("attention-transition")
@click.option(
    "--component",
    type=click.Choice(["producers", "reissue", "conditions", "marker"]),
    required=True,
)
@click.option("--state", type=click.Choice(["enabled", "disabled"]), required=True)
def attention_transition(component, state):
    """Preserve attention evidence while selecting a stopped operator transition."""
    from butlers.db_authority.apply import transition_runtime_attention
    from butlers.migrations import migration_session

    selected = os.environ.get("BUTLERS_BOOTSTRAP_DATABASE_URL")
    if not selected:
        raise click.ClickException("Explicit administrative database configuration is required")
    try:
        with migration_session(selected) as held:
            transition_runtime_attention(held, component=component, enabled=state == "enabled")
            held.commit()
    except Exception as exc:
        raise click.ClickException(
            "Attention transition did not complete; retain stopped state"
        ) from exc
    click.echo("Attention transition committed and selected maintenance session released.")
