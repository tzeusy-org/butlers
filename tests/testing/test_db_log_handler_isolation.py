"""Contract tests for the log-isolation plugin (``tests/log_isolation.py``, bu-sg5nia).

The plugin wraps every test in ``detach_leaked_db_log_handlers`` so a daemon
test that runs ``run_startup`` without shutting down cannot leave its
``ButlerDBLogHandler`` on the root logger for later tests to trip over.  It must
detach exactly the butler DB handlers attached inside it, and nothing else.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from butlers.core.butler_logging import ButlerDBLogHandler
from tests.log_isolation import detach_leaked_db_log_handlers

pytestmark = pytest.mark.unit


def _db_handler(name: str) -> ButlerDBLogHandler:
    return ButlerDBLogHandler(butler_logger=MagicMock(), butler_name=name)


def test_detaches_db_handlers_attached_inside_and_keeps_everything_else() -> None:
    root = logging.getLogger()
    earlier = _db_handler("earlier")
    root.addHandler(earlier)
    try:
        leaked = _db_handler("leaked")
        other = logging.NullHandler()
        with detach_leaked_db_log_handlers():
            root.addHandler(leaked)
            root.addHandler(other)
        try:
            assert leaked not in root.handlers
            assert earlier in root.handlers
            assert other in root.handlers
        finally:
            root.removeHandler(other)
    finally:
        root.removeHandler(earlier)


def test_detaches_on_error_exit() -> None:
    root = logging.getLogger()
    leaked = _db_handler("leaked")
    with pytest.raises(RuntimeError), detach_leaked_db_log_handlers():
        root.addHandler(leaked)
        raise RuntimeError("startup failed after attaching the handler")
    assert leaked not in root.handlers


def test_plugin_is_loaded_for_default_runs(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.pluginmanager.has_plugin("tests.log_isolation")
