"""Tests for ProcessLog model serialization with retry provenance fields.

Verifies that the new retry_attempted, retry_succeeded, result_source, and
attempt_count fields are correctly exposed through the ProcessLog Pydantic
model and that the SessionDetail.process_log round-trips the values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.unit


def test_process_log_default_nulls() -> None:
    """ProcessLog omits retry provenance fields when not provided (all default None)."""
    from butlers.api.models.session import ProcessLog

    plog = ProcessLog(pid=1234, exit_code=0, runtime_type="codex")
    assert plog.retry_attempted is None
    assert plog.retry_succeeded is None
    assert plog.result_source is None
    assert plog.attempt_count is None


def test_process_log_with_retry_provenance() -> None:
    """ProcessLog round-trips all four retry provenance fields."""
    from butlers.api.models.session import ProcessLog

    now = datetime.now(tz=UTC)
    plog = ProcessLog(
        pid=42,
        exit_code=0,
        command="codex exec ...",
        stderr="",
        runtime_type="codex",
        retry_attempted=True,
        retry_succeeded=False,
        result_source="first",
        attempt_count=2,
        created_at=now,
        expires_at=now + timedelta(days=14),
    )
    assert plog.retry_attempted is True
    assert plog.retry_succeeded is False
    assert plog.result_source == "first"
    assert plog.attempt_count == 2


def test_process_log_json_serialization() -> None:
    """ProcessLog.model_dump() includes null retry fields; non-null fields serialize correctly."""
    from butlers.api.models.session import ProcessLog

    plog = ProcessLog(
        pid=50,
        retry_attempted=True,
        retry_succeeded=True,
        result_source="retry",
        attempt_count=2,
    )
    data = plog.model_dump()
    assert data["retry_attempted"] is True
    assert data["retry_succeeded"] is True
    assert data["result_source"] == "retry"
    assert data["attempt_count"] == 2

    # Default-null fields also present
    plog2 = ProcessLog(pid=50)
    data2 = plog2.model_dump()
    assert data2["retry_attempted"] is None
    assert data2["result_source"] is None


def test_session_detail_process_log_integration() -> None:
    """SessionDetail.process_log carries retry provenance through the model hierarchy."""
    from uuid import uuid4

    from butlers.api.models.session import ProcessLog, SessionDetail

    now = datetime.now(tz=UTC)
    session_id = uuid4()
    detail = SessionDetail(
        id=session_id,
        prompt="investigate failure",
        trigger_source="qa",
        started_at=now,
        process_log=ProcessLog(
            pid=42,
            exit_code=0,
            runtime_type="codex",
            retry_attempted=True,
            retry_succeeded=False,
            result_source="first",
            attempt_count=2,
        ),
    )
    assert detail.process_log is not None
    assert detail.process_log.retry_attempted is True
    assert detail.process_log.result_source == "first"
    assert detail.process_log.attempt_count == 2
