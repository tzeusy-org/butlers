from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.scheduled_jobs import _run_qa_pr_status_check_job

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_run_qa_pr_status_check_job_uses_module_token_resolution() -> None:
    qa = MagicMock()
    qa._resolve_gh_token = AsyncMock(return_value="gh-token")
    qa._check_pr_statuses = AsyncMock()

    with patch("butlers.modules.qa.get_active_instance", return_value=qa):
        result = await _run_qa_pr_status_check_job(pool=MagicMock(), job_args=None)

    assert result == {"status": "completed"}
    qa._resolve_gh_token.assert_awaited_once()
    qa._check_pr_statuses.assert_awaited_once()
    assert qa._check_pr_statuses.await_args.args[1] == "gh-token"
