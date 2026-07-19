"""Tests for butlers.cli._record_deployment_boot (bu-9r3hd.2 deployments ledger).

_start_all calls this once per `butlers up` process boot -- not once per
butler daemon -- since every butler shares one process/container here. See
alembic/versions/core/core_163_deployments_ledger.py for why a per-butler
write would be wrong.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.cli import _record_deployment_boot

pytestmark = pytest.mark.unit


def _make_daemon(*, schema="switchboard", has_pool=True):
    daemon = MagicMock()
    daemon.config.name = schema
    daemon.config.db_schema = schema
    if has_pool:
        daemon.db.pool = AsyncMock()
    else:
        daemon.db = None
    return daemon


class TestRecordDeploymentBoot:
    async def test_records_success_when_all_configured_daemons_started(self):
        daemon = _make_daemon()
        with (
            patch("butlers.core.deployments.resolve_git_sha", return_value="abc1234"),
            patch(
                "butlers.core.deployments.read_migration_head", AsyncMock(return_value="core_163")
            ),
            patch(
                "butlers.core.deployments.detect_boot_serving_provenance",
                return_value=MagicMock(
                    serving_mode="hotreload-worktree",
                    serving_worktree=".worktrees/frozen-checkout",
                ),
            ),
            patch("butlers.core.deployments.record_deployment", AsyncMock()) as mock_record,
        ):
            await _record_deployment_boot([daemon], configured_count=1)

        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["git_sha"] == "abc1234"
        assert kwargs["migration_head"] == "core_163"
        assert kwargs["result"] == "success"
        assert kwargs["source"] == "boot"
        assert kwargs["serving_mode"] == "hotreload-worktree"
        assert kwargs["serving_worktree"] == ".worktrees/frozen-checkout"

    async def test_records_failed_when_fewer_daemons_started_than_configured(self):
        daemon = _make_daemon()
        with (
            patch("butlers.core.deployments.resolve_git_sha", return_value="abc1234"),
            patch("butlers.core.deployments.read_migration_head", AsyncMock(return_value=None)),
            patch(
                "butlers.core.deployments.detect_boot_serving_provenance",
                return_value=MagicMock(serving_mode="image", serving_worktree=None),
            ),
            patch("butlers.core.deployments.record_deployment", AsyncMock()) as mock_record,
        ):
            # Only 1 of 3 configured butlers actually started.
            await _record_deployment_boot([daemon], configured_count=3)

        assert mock_record.await_args.kwargs["result"] == "failed"
        assert mock_record.await_args.kwargs["source"] == "boot"
        assert mock_record.await_args.kwargs["serving_mode"] == "image"

    async def test_skips_when_primary_daemon_has_no_pool(self):
        daemon = _make_daemon(has_pool=False)
        with patch("butlers.core.deployments.record_deployment", AsyncMock()) as mock_record:
            await _record_deployment_boot([daemon], configured_count=1)
        mock_record.assert_not_awaited()

    async def test_ledger_write_failure_does_not_raise(self):
        """Best-effort: a ledger-write failure must not block/crash startup."""
        daemon = _make_daemon()
        with (
            patch("butlers.core.deployments.resolve_git_sha", return_value="abc1234"),
            patch(
                "butlers.core.deployments.read_migration_head",
                AsyncMock(side_effect=Exception("connection reset")),
            ),
        ):
            # Must not raise.
            await _record_deployment_boot([daemon], configured_count=1)
