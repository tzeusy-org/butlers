"""Pool closes stay inside the daemon's shutdown share (bu-dhloi1.153).

Each daemon closes two pools (credentials and its own). With a fixed 10s timeout each, a dozen
daemons with wedged pools would outlast the pod's grace margin. With a share set, each close gets
what is left of it, never under a 1s floor; alone, the daemon keeps the default.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers import lifecycle
from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


def _daemon(tmp_path: Path, share: float | None) -> ButlerDaemon:
    (tmp_path / "butler.toml").write_text(
        '[butler]\nname = "test-butler"\nport = 9100\n\n'
        '[butler.db]\nname = "butlers"\nschema = "test_butler"\n'
    )
    daemon = ButlerDaemon(tmp_path)
    daemon.config = ButlerConfig(name="test-butler", port=9100)
    daemon._shutdown_budget_s = share
    daemon.db = MagicMock(close=AsyncMock())
    daemon._shared_credentials_db = MagicMock(close=AsyncMock())
    return daemon


async def test_each_pool_close_gets_what_is_left_of_the_share(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=7.0)
    credentials, db = daemon._shared_credentials_db, daemon.db
    deadline = asyncio.get_running_loop().time() + 7.0

    await lifecycle._shutdown_chronicler_dependents(daemon, deadline)

    for close in (credentials.close, db.close):
        (timeout,), _ = close.await_args
        assert 6.0 < timeout <= 7.0


async def test_a_spent_share_still_leaves_a_pool_the_floor(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=7.0)
    credentials, db = daemon._shared_credentials_db, daemon.db
    deadline = asyncio.get_running_loop().time() - 5.0

    await lifecycle._shutdown_chronicler_dependents(daemon, deadline)

    assert credentials.close.await_args.args == (1.0,)
    assert db.close.await_args.args == (1.0,)


async def test_a_daemon_shutting_down_alone_calls_a_plain_close(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=None)
    credentials, db = daemon._shared_credentials_db, daemon.db

    await lifecycle._shutdown_chronicler_dependents(daemon, 0.0)

    # Exactly as before the budget existed: no argument, so a stand-in without a timeout works.
    assert db.close.await_args.args == () and credentials.close.await_args.args == ()


async def test_no_deadline_means_no_share_even_when_a_budget_is_set(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path, share=7.0)
    db = daemon.db

    await lifecycle._shutdown_chronicler_dependents(daemon)

    assert db.close.await_args.args == ()
