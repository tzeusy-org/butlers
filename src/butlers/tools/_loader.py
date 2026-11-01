"""Eager discovery adapter for canonical roster tools.

Entry selection and execution belong to :mod:`butlers.roster_imports`; this
adapter preserves discovery, missing-file errors and optional warning/continue.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_tools_entry(config_dir: Path) -> Path | None:
    """Return the tools entry-point path for a butler config directory.

    Checks for a ``tools/`` package first (``tools/__init__.py``), then
    falls back to a standalone ``tools.py`` file.  Returns *None* when
    neither exists.
    """
    pkg_init = config_dir / "tools" / "__init__.py"
    if pkg_init.exists():
        return pkg_init
    single = config_dir / "tools.py"
    if single.exists():
        return single
    return None


def register_butler_tools(
    butler_name: str,
    config_dir: Path,
) -> None:
    """Load tools from *config_dir* and register as ``butlers.tools.<butler_name>``.

    Supports both a single ``tools.py`` file and a ``tools/`` package directory
    (with ``__init__.py``).

    Parameters
    ----------
    butler_name:
        The butler identifier (e.g. ``"switchboard"``).
    config_dir:
        Path to the butler's config directory (e.g. ``roster/switchboard/``).
        Must contain either a ``tools.py`` file or a ``tools/__init__.py``.

    Raises
    ------
    FileNotFoundError
        If neither ``tools.py`` nor ``tools/__init__.py`` exists in *config_dir*.
    ImportError
        If the module cannot be loaded.
    ValueError
        If the name or config directory is outside the owning roster root.
    """
    tools_path = _find_tools_entry(config_dir)
    if tools_path is None:
        raise FileNotFoundError(f"No tools.py or tools/__init__.py found in {config_dir}")

    from butlers import roster_imports

    if not roster_imports._NAME.fullmatch(butler_name):
        raise ValueError("Invalid roster name")
    if config_dir.resolve() != (roster_imports._ROSTER / butler_name).resolve():
        raise ValueError("Tools config directory differs from owning roster root")
    roster_imports.roster_module(butler_name, "tools")
    logger.debug("Registered butler tools: %s from %s", butler_name, tools_path)


def _discover_butler_names(butlers_root: Path) -> list[str]:
    """Discover butler names that have tools in their config directory.

    Scans ``butlers_root/*/`` for directories containing either ``tools.py``
    or ``tools/__init__.py`` and returns a sorted list of directory names
    (butler identifiers).

    Parameters
    ----------
    butlers_root:
        Path to the ``roster/`` directory containing butler config dirs.

    Returns
    -------
    list[str]
        Sorted list of butler names with tools.
    """
    if not butlers_root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in butlers_root.iterdir()
        if entry.is_dir() and _find_tools_entry(entry) is not None
    )


def register_all_butler_tools(roster_root: Path | None = None) -> None:
    """Register tools for all discovered butlers.

    Scans ``roster/*/`` to dynamically discover butler tools (either a
    ``tools.py`` file or a ``tools/`` package).  No hardcoded butler names —
    adding a new butler with tools is automatically picked up.

    Parameters
    ----------
    roster_root:
        Path to the ``roster/`` directory containing butler config dirs.
        If *None*, auto-detects by walking up from this file's location
        to find the repository root.
    """
    if roster_root is None:
        # Auto-detect: this file is at src/butlers/tools/_loader.py
        # repo root is 4 levels up: _loader.py -> tools/ -> butlers/ -> src/ -> repo root
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        roster_root = repo_root / "roster"

    for name in _discover_butler_names(roster_root):
        config_dir = roster_root / name
        try:
            register_butler_tools(name, config_dir)
        except Exception:
            logger.warning("Failed to register tools for butler: %s", name, exc_info=True)
