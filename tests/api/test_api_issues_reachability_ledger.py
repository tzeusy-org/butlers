"""Reachability as a durable condition ledger (bu-6jv4m.3).

Before this change ``GET /api/issues`` stamped ``first_seen_at ==
last_seen_at == now()`` onto every unreachable butler on every poll, and the
acknowledge-until-recurrence check compared that request-time timestamp
against the ack watermark.  The comparison was therefore ``now <= ack_time``,
which is false on the very next poll: acknowledging a continuously-unreachable
butler was structurally impossible to make stick.

The fix is a persisted outage *episode* (``public.butler_reachability_conditions``):

- one uninterrupted outage is one row with a stable ``started_at``;
- recovery closes it (``resolved_at``);
- a later down transition opens a genuinely new row with a newer ``started_at``.

``Issue.recurrence_at`` carries that stable epoch, and the ack is held against
it -- so ``last_seen_at`` stays honest ("last probed") instead of being bent to
make the ack work.

Mutation strength: the assertions read the ack outcome across two polls with
the SAME ledger onset, so reverting to the request-time timestamp fails
immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import compute_issue_key
from butlers.api.routers.issues import (
    _SOURCE_REACHABILITY_LEDGER,
    _get_db_manager,
)

pytestmark = pytest.mark.unit

BUTLER = "general"
UNREACHABLE_KEY = compute_issue_key("unreachable", BUTLER)


def _is_audit_group_query(sql: str) -> bool:
    return "grouped_errors" in sql


def _is_acks_query(sql: str) -> bool:
    return "dismissed_issues" in sql


def _is_ledger_open_query(sql: str) -> bool:
    return "butler_reachability_conditions" in sql and "RETURNING" in sql


def _is_ledger_resolve_query(sql: str) -> bool:
    return "butler_reachability_conditions" in sql and "resolved_at = now()" in sql


class _Recorder:
    """Scripted switchboard pool driving one poll of GET /api/issues."""

    def __init__(
        self,
        *,
        episode_started_at: datetime | None,
        observations: int = 1,
        ack_watermark: datetime | None = None,
        ledger_error: Exception | None = None,
    ) -> None:
        self.episode_started_at = episode_started_at
        self.observations = observations
        self.ack_watermark = ack_watermark
        self.ledger_error = ledger_error
        self.resolved_butlers: list[list[str]] = []
        self.opened_butlers: list[list[str]] = []

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        if _is_ledger_open_query(sql):
            if self.ledger_error is not None:
                raise self.ledger_error
            self.opened_butlers.append(list(args[0]))
            if self.episode_started_at is None:
                return []
            return [
                {
                    "butler": name,
                    "started_at": self.episode_started_at,
                    "observations": self.observations,
                }
                for name in args[0]
            ]
        if _is_acks_query(sql):
            if self.ack_watermark is None:
                return []
            return [{"issue_key": UNREACHABLE_KEY, "last_seen_at": self.ack_watermark}]
        if _is_audit_group_query(sql):
            return []
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        if _is_ledger_resolve_query(sql):
            if self.ledger_error is not None:
                raise self.ledger_error
            self.resolved_butlers.append(list(args[0]))
            return "UPDATE 1"
        return "UPDATE 0"


def _build_app(recorder: _Recorder, *, reachable: bool = False) -> Any:
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=recorder.fetch)
    mock_pool.execute = AsyncMock(side_effect=recorder.execute)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    mgr = MagicMock()
    if reachable:
        client = MagicMock()
        client.ping = AsyncMock(return_value=None)
        mgr.get_client = AsyncMock(return_value=client)
    else:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError(BUTLER, cause=ConnectionRefusedError("refused"))
        )
    mgr.invalidate_client = AsyncMock()

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[get_butler_configs] = lambda: [
        ButlerConnectionInfo(name=BUTLER, port=9001)
    ]
    return app


async def _get_issues(app: Any, path: str = "/api/issues") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


class TestContinuousOutageIdentity:
    async def test_unreachable_issue_reports_episode_onset_not_request_time(self) -> None:
        onset = datetime.now(UTC) - timedelta(hours=6)
        recorder = _Recorder(episode_started_at=onset, observations=4)
        app = _build_app(recorder)

        resp = await _get_issues(app)

        assert resp.status_code == 200
        [issue] = resp.json()["data"]
        assert issue["type"] == "unreachable"
        # The condition's identity is its onset, not this request's clock.
        assert issue["first_seen_at"].startswith(onset.isoformat()[:19])
        assert issue["recurrence_at"].startswith(onset.isoformat()[:19])
        # ``last_seen_at`` stays honest: it is when we last PROBED, which is now.
        assert issue["last_seen_at"] > issue["recurrence_at"]
        # Consecutive failed probes in this episode, not a hard-coded 1.
        assert issue["occurrences"] == 4

    async def test_acknowledgement_survives_repeated_polls_of_one_outage(self) -> None:
        """AC1: acking a continuously-down butler is NOT undone by the next poll."""
        onset = datetime.now(UTC) - timedelta(hours=6)

        for poll in range(3):
            recorder = _Recorder(
                episode_started_at=onset,
                observations=poll + 1,
                ack_watermark=onset,
            )
            app = _build_app(recorder)

            resp = await _get_issues(app)

            assert resp.status_code == 200, f"poll {poll}"
            assert resp.json()["data"] == [], f"ack lapsed on poll {poll}"

    async def test_acked_outage_is_still_listed_in_the_restore_view(self) -> None:
        onset = datetime.now(UTC) - timedelta(hours=6)
        recorder = _Recorder(episode_started_at=onset, observations=2, ack_watermark=onset)
        app = _build_app(recorder)

        resp = await _get_issues(app, "/api/issues?include_dismissed=true")

        assert resp.status_code == 200
        [issue] = resp.json()["data"]
        assert issue["issue_key"] == UNREACHABLE_KEY
        assert issue["dismissed"] is True


class TestRecoveryAndRecurrence:
    async def test_reachable_butler_closes_its_open_condition(self) -> None:
        """AC2 (first half): recovery closes the condition."""
        recorder = _Recorder(episode_started_at=None)
        app = _build_app(recorder, reachable=True)

        resp = await _get_issues(app)

        assert resp.status_code == 200
        assert resp.json()["data"] == []
        assert recorder.resolved_butlers == [[BUTLER]]
        # A recovered butler must not also be recorded as still-down.
        assert recorder.opened_butlers == []

    async def test_new_episode_after_recovery_reopens_an_acked_condition(self) -> None:
        """AC2 (second half): a genuinely new outage un-acks the condition."""
        old_onset = datetime.now(UTC) - timedelta(days=2)
        new_onset = datetime.now(UTC) - timedelta(minutes=5)

        recorder = _Recorder(
            episode_started_at=new_onset,
            observations=1,
            # The ack was taken against the PREVIOUS episode's onset.
            ack_watermark=old_onset,
        )
        app = _build_app(recorder)

        resp = await _get_issues(app)

        assert resp.status_code == 200
        [issue] = resp.json()["data"]
        assert issue["issue_key"] == UNREACHABLE_KEY
        assert issue["recurrence_at"].startswith(new_onset.isoformat()[:19])


class TestLedgerUnavailable:
    async def test_ledger_failure_is_named_as_a_degraded_source(self) -> None:
        """AC5 (unavailable case): a broken ledger must never read as calm.

        The issue itself still surfaces (over-reporting is the safe
        direction), but the response says the condition history was
        unavailable so no consumer can claim a durable ack held.
        """
        recorder = _Recorder(
            episode_started_at=None,
            ledger_error=ConnectionError("connection reset by peer"),
        )
        app = _build_app(recorder)

        resp = await _get_issues(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["sources_degraded"] == [_SOURCE_REACHABILITY_LEDGER]
        assert [i["type"] for i in body["data"]] == ["unreachable"]


class TestDismissDerivesReachabilityWatermarkServerSide:
    """The ack watermark for the reachability lane is server truth.

    A client that still posts the issue's ``last_seen_at`` (which is the probe
    clock) would otherwise write a watermark that the very next poll outruns.
    The endpoint therefore reads the open episode's ``started_at`` from the
    ledger and stores that instead, so AC1 holds even for an unpatched client.
    """

    @staticmethod
    def _build(
        *,
        started_at: datetime | None,
        fetch_error: Exception | None = None,
    ) -> tuple[Any, AsyncMock]:
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            if fetch_error is not None:
                raise fetch_error
            if started_at is None:
                return []
            return [{"started_at": started_at}]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch)
        mock_pool.execute = AsyncMock(return_value="INSERT 0 1")

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []
        return app, mock_pool

    @staticmethod
    async def _dismiss(app: Any, payload: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post("/api/issues/dismiss", json=payload)

    async def test_open_episode_onset_overrides_the_posted_timestamp(self) -> None:
        onset = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
        app, pool = self._build(started_at=onset)

        resp = await self._dismiss(
            app,
            {"issue_key": UNREACHABLE_KEY, "last_seen_at": "2026-08-22T09:30:00Z"},
        )

        assert resp.status_code == 200
        assert pool.execute.await_args.args[3] == onset

    async def test_audit_group_ack_still_uses_the_posted_timestamp(self) -> None:
        app, pool = self._build(started_at=datetime(2026, 8, 1, tzinfo=UTC))

        resp = await self._dismiss(
            app,
            {
                "issue_key": "audit_error_group:deadbeefdeadbeef",
                "last_seen_at": "2026-08-22T09:30:00Z",
            },
        )

        assert resp.status_code == 200
        assert pool.execute.await_args.args[3] == datetime(2026, 8, 22, 9, 30, tzinfo=UTC)

    async def test_unavailable_ledger_refuses_the_ack_instead_of_recording_a_broken_one(
        self,
    ) -> None:
        app, pool = self._build(
            started_at=None, fetch_error=ConnectionError("connection reset by peer")
        )

        resp = await self._dismiss(
            app,
            {"issue_key": UNREACHABLE_KEY, "last_seen_at": "2026-08-22T09:30:00Z"},
        )

        # Fail fast: silently storing the client's probe clock would record an
        # acknowledgement that cannot survive the next poll.
        assert resp.status_code == 503
        pool.execute.assert_not_awaited()
