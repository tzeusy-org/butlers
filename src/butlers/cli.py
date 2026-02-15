"""CLI for the Butlers framework — manage butler daemons."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
from collections import defaultdict
from pathlib import Path

import click

from butlers.config import ConfigError, load_config

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
        sock.close()
        return True
    except (TimeoutError, OSError):
        return False


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
def up(only: tuple[str, ...], butlers_dir: Path) -> None:
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

    # Check for port conflicts before starting any butler
    conflicts = _check_port_conflicts(configs)
    if conflicts:
        click.echo("Port conflict detected:")
        for port, butler_names in sorted(conflicts.items()):
            names_str = ", ".join(sorted(butler_names))
            click.echo(f"  Port {port}: {names_str}")
        sys.exit(1)

    click.echo(f"Starting {len(configs)} butler(s): {', '.join(sorted(configs.keys()))}")
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
@click.option("--port", type=int, default=8100, help="Port for the butler's MCP server")
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
    (butler_dir / "skills").mkdir()

    # butler.toml
    toml_content = f"""[butler]
name = "{name}"
port = {port}
description = ""

[butler.db]
name = "butler_{name}"
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
    default=8200,
    show_default=True,
    help="Port for the dashboard server",
)
def dashboard(host: str, port: int) -> None:
    """Start the Butlers dashboard web UI."""
    import uvicorn

    click.echo(f"Starting Butlers dashboard on {host}:{port}")
    uvicorn.run("butlers.api.app:create_app", host=host, port=port, factory=True)


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


async def _start_all(configs: dict[str, Path]) -> None:
    """Start all butler daemons in a single event loop."""
    from butlers.daemon import ButlerDaemon

    daemons: list[ButlerDaemon] = []
    loop = asyncio.get_event_loop()

    # Set up signal handling
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        click.echo("\nShutting down...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Start all daemons
    for name, config_dir in sorted(configs.items()):
        daemon = ButlerDaemon(config_dir)
        try:
            await daemon.start()
            daemons.append(daemon)
            click.echo(f"  started: {name}")
        except Exception as exc:
            click.echo(f"  failed: {name}: {exc}")

    if not daemons:
        click.echo("No butlers started successfully")
        return

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown
    for daemon in reversed(daemons):
        await daemon.shutdown()


async def _start_single(config_path: Path) -> None:
    """Start a single butler daemon."""
    from butlers.daemon import ButlerDaemon

    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        click.echo("\nShutting down...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    daemon = ButlerDaemon(config_path)
    await daemon.start()
    click.echo(f"Butler {daemon.config.name} running on port {daemon.config.port}")

    await shutdown_event.wait()
    await daemon.shutdown()
