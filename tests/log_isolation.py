"""Pytest plugin that detaches butler DB log handlers a test leaves behind.

``run_startup`` attaches a ``ButlerDBLogHandler`` to the root logger and only
the daemon's shutdown removes it.  Tests that drive startup against mocks rarely
shut down, so the handler outlived them and forwarded later tests' records to a
dead pool, which warned into those tests' ``caplog`` (bu-sg5nia; one half of the
definer-test flake fixed in bu-w4c1iw).  An autouse fixture here wraps every
test and removes only the DB log handlers attached during it.

``pyproject.toml`` loads this plugin for every pytest run (``-p
tests.log_isolation``), like ``tests/quarantine.py``.  It was written as a
plugin rather than a root ``conftest.py`` fixture because the since-retired
condensation ledger guard treated any root ``conftest.py`` edit as a change to
every test beneath it.  It imports ``butlers`` only inside
the context manager: pytest loads ``-p`` plugins before the root
``conftest.py``, whose guard explains a ``butlers`` that resolves outside this
checkout.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest


@contextmanager
def detach_leaked_db_log_handlers() -> Iterator[None]:
    """On exit, detach the butler DB log handlers attached to the root logger inside.

    Handlers attached before entry, and every other handler type (pytest's own
    capture handlers included), are left alone.
    """
    from butlers.core.butler_logging import ButlerDBLogHandler

    root = logging.getLogger()
    attached = set(root.handlers)
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if isinstance(handler, ButlerDBLogHandler) and handler not in attached:
                root.removeHandler(handler)


@pytest.fixture(autouse=True)
def _detach_leaked_db_log_handlers() -> Iterator[None]:
    with detach_leaked_db_log_handlers():
        yield
