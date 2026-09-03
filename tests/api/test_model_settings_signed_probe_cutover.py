"""Model verification asks Switchboard; it no longer runs a model itself.

Covers REQ-dashboard-model-settings-001 and REQ-core-credentials-002,
acceptance criteria 4, 6, 7, and 8.

Before this cutover the Dashboard container built a runtime adapter and
invoked a model to answer "Test" and "Verify all".  That is the pairing the
signer mount cannot coexist with: a process holding a private signing key must
not also be the process spawning provider runtimes.  So both entry points, and
the hourly sweep behind them, now sign a one-entry capability and let
Switchboard hold the runtime.

Two properties carry most of the weight here.

*Typed outcomes stay typed.*  A saturated coordinator, an expired capability
and a model that genuinely failed are three different facts, and the tempting
simplification --- "the probe did not come back OK, so the model is bad" ---
turns an outage into a wrong verdict on a healthy model, which then loses
routing eligibility.  So the Test route maps each control status onto its own
HTTP status, and verify-all counts unavailability in a bucket of its own
rather than folding it into ``failed``.

*This process writes no verification evidence.*  Only the coordinator's
``SECURITY DEFINER`` path does, and only for a probe that actually ran.  The
tests for that assert on absence --- no ``UPDATE`` reaches the pool --- which
is a weak assertion unless something would otherwise have made one, so the
completed-probe cases are asserted the same way and prove the absence is the
cutover rather than the mock.

No key material appears in this file: the client is scripted, so nothing here
signs or needs a signer at all.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import _get_db_manager
from butlers.core.runtime_probe_control.coordinator import ProbeResult, ProbeStatus

pytestmark = pytest.mark.unit

_OWNER_KEY = "owner-key"


@pytest.fixture(autouse=True)
def _dashboard_owner_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Test route now enforces require_dashboard_owner_control (bu-7y7z2)."""
    monkeypatch.setenv("DASHBOARD_API_KEY", _OWNER_KEY)


class _ScriptedClient:
    """Stands in for the signed control client and records what it was asked.

    Deliberately records only catalog entry ids: a probe request carries
    nothing else, and a stand-in that accepted more than the real client does
    would let a test pass on a request the plane would refuse.
    """

    def __init__(self, *results: ProbeResult) -> None:
        self._results = list(results)
        self.asked: list[uuid.UUID] = []

    async def probe(self, catalog_entry_id: uuid.UUID) -> ProbeResult:
        self.asked.append(catalog_entry_id)
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


def _pool(*, entry_exists: bool = True, rows: list[dict[str, Any]] | None = None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_record(row) for row in (rows or [])])
    pool.fetchval = AsyncMock(return_value=1 if entry_exists else None)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


def _record(row: dict[str, Any]) -> MagicMock:
    record = MagicMock()
    record.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return record


def _mount(app, pool: AsyncMock):
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: db
    return app


def _asgi(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": _OWNER_KEY},
    )


@pytest.fixture
def model_settings():
    import butlers.api.routers.model_settings as module

    return module


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch, model_settings):
    """Install a scripted control client and hand the test its recorder."""

    def _install(*results: ProbeResult) -> _ScriptedClient:
        client = _ScriptedClient(*results)
        monkeypatch.setattr(model_settings, "_probe_client", lambda caller: client)
        return client

    return _install


# ---------------------------------------------------------------------------
# REQ-dashboard-model-settings-001: owner control precedes the probe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "configured,header,expected", [(False, None, 503), (True, None, 401), (True, "wrong", 401)]
)
async def test_test_route_owner_gate_precedes_probe(
    app,
    scripted,
    monkeypatch: pytest.MonkeyPatch,
    configured: bool,
    header: str | None,
    expected: int,
) -> None:
    """REQ-dashboard-model-settings-001: no probe is requested before owner auth."""
    if configured:
        monkeypatch.setenv("DASHBOARD_API_KEY", _OWNER_KEY)
    else:
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    client = scripted(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=1))
    pool = _pool()
    _mount(app, pool)
    headers = {"X-API-Key": header} if header is not None else {}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
    ) as http:
        response = await http.post(f"/api/settings/models/{uuid.uuid4()}/test")

    assert response.status_code == expected
    assert client.asked == []
    pool.fetchval.assert_not_awaited()


# ---------------------------------------------------------------------------
# Criterion 4: Test goes through the signed client and nothing else
# ---------------------------------------------------------------------------


async def test_test_route_asks_the_control_plane_about_exactly_one_entry(
    app, scripted, monkeypatch, model_settings
) -> None:
    """One catalog entry id, one probe, and a latency instead of a provider reply."""
    entry_id = uuid.uuid4()
    seen: list[str] = []
    monkeypatch.setattr(
        model_settings,
        "_probe_client",
        lambda caller: (seen.append(caller), client)[1],
    )
    client = _ScriptedClient(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=412))
    pool = _pool()
    _mount(app, pool)

    async with _asgi(app) as http:
        response = await http.post(f"/api/settings/models/{entry_id}/test")

    body = response.json()["data"]
    assert response.status_code == 200
    assert body == {"success": True, "duration_ms": 412, "error": None}
    assert client.asked == [entry_id]
    assert seen == [model_settings.DASHBOARD_CALLER]


async def test_the_test_route_carries_no_provider_reply(app, scripted) -> None:
    """AC4: the response schema has no field a provider's words could land in.

    Asserted on the model rather than one payload, so a future field cannot
    reintroduce the disclosure for a case this file does not exercise.
    """
    from butlers.api.routers.model_settings import ModelTestResult

    assert set(ModelTestResult.model_fields) == {"success", "duration_ms", "error"}


async def test_an_unknown_entry_is_refused_before_any_capability_is_signed(app, scripted) -> None:
    """A 404 must not spend a capability on an id the catalog does not have."""
    client = scripted(ProbeResult(ProbeStatus.COMPLETED, ok=True))
    _mount(app, _pool(entry_exists=False))

    async with _asgi(app) as http:
        response = await http.post(f"/api/settings/models/{uuid.uuid4()}/test")

    assert response.status_code == 404
    assert client.asked == []


# ---------------------------------------------------------------------------
# Criterion 6: every typed control failure keeps its own identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (ProbeStatus.UNAUTHORIZED, 401),
        (ProbeStatus.REPLAY, 409),
        (ProbeStatus.BUSY, 429),
        (ProbeStatus.UNAVAILABLE, 503),
        (ProbeStatus.TIMEOUT, 504),
    ],
)
async def test_each_control_failure_keeps_its_own_status(app, scripted, status, code) -> None:
    """Criterion 6: five outcomes, five statuses, none of them a model verdict.

    The failure this guards against is a 200 carrying ``success: false``: the
    dashboard would render an outage as a broken model, and an operator would
    go debugging a model that is fine.
    """
    client = scripted(ProbeResult(status))
    _mount(app, _pool())

    async with _asgi(app) as http:
        response = await http.post(f"/api/settings/models/{uuid.uuid4()}/test")

    assert response.status_code == code
    assert response.status_code != 200
    assert client.asked != []


async def test_a_control_failure_is_never_rendered_as_a_model_verdict(app, scripted) -> None:
    """The same point stated as absence, across every non-completed status."""
    _mount(app, _pool())

    for status in (
        ProbeStatus.UNAUTHORIZED,
        ProbeStatus.REPLAY,
        ProbeStatus.BUSY,
        ProbeStatus.UNAVAILABLE,
        ProbeStatus.TIMEOUT,
    ):
        scripted(ProbeResult(status))
        async with _asgi(app) as http:
            response = await http.post(f"/api/settings/models/{uuid.uuid4()}/test")

        assert "success" not in response.text
        assert "duration_ms" not in response.text


async def test_a_genuinely_failed_probe_is_a_model_verdict(app, scripted) -> None:
    """And the one case that *is* about the model still reads as one."""
    client = scripted(ProbeResult(ProbeStatus.COMPLETED, ok=False, latency_ms=25))
    _mount(app, _pool())

    async with _asgi(app) as http:
        response = await http.post(f"/api/settings/models/{uuid.uuid4()}/test")

    body = response.json()["data"]
    assert response.status_code == 200
    assert body["success"] is False
    assert body["duration_ms"] == 25
    assert client.asked != []


async def test_no_control_failure_detail_carries_a_provider_or_capability_value(
    app, scripted, model_settings
) -> None:
    """AC6 detail text is a fixed vocabulary this repository wrote.

    Checked against the module's own table rather than a rendered response, so
    a detail added later for a new status is covered without a new test.
    """
    details = model_settings._TEST_UNAVAILABLE_DETAIL

    assert set(details) == set(ProbeStatus) - {ProbeStatus.COMPLETED}
    for text in details.values():
        assert "{" not in text
        assert "%s" not in text


# ---------------------------------------------------------------------------
# Criterion 7: this process writes no verification evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=9), id="completed-ok"),
        pytest.param(ProbeResult(ProbeStatus.COMPLETED, ok=False), id="completed-failed"),
        pytest.param(ProbeResult(ProbeStatus.UNAUTHORIZED), id="unauthorized"),
        pytest.param(ProbeResult(ProbeStatus.REPLAY), id="replay"),
        pytest.param(ProbeResult(ProbeStatus.BUSY), id="busy"),
        pytest.param(ProbeResult(ProbeStatus.UNAVAILABLE), id="unavailable"),
        pytest.param(ProbeResult(ProbeStatus.TIMEOUT), id="timeout"),
    ],
)
async def test_the_test_route_writes_no_verification_evidence(app, scripted, result) -> None:
    """Criterion 7: previous evidence survives every outcome, including success.

    Success is in the parameter list on purpose.  ``execute`` is never awaited
    for *any* outcome, so an assertion restricted to failures would pass in a
    world where the route still wrote on success --- and that world is exactly
    the one the coordinator's ``SECURITY DEFINER`` path exists to prevent.
    """
    scripted(result)
    pool = _pool()
    _mount(app, pool)

    async with _asgi(app) as http:
        await http.post(f"/api/settings/models/{uuid.uuid4()}/test")

    pool.execute.assert_not_awaited()


async def test_no_statement_in_the_module_writes_a_verification_column() -> None:
    """The same guarantee at the source, so it cannot come back by another route.

    A behavioural test covers the paths it exercises; this covers the ones
    nobody thought to write a test for.  Reading the columns is fine and every
    catalog response returns them, so the check is over the *assigning* half of
    a write only: each catalog ``UPDATE`` literal is truncated at its
    ``RETURNING`` clause, and no verification column may survive the cut.

    The PUT endpoint builds its ``SET`` clause from field names rather than
    literals, so that model's field set is checked too --- otherwise the write
    could come back through a request body without changing any SQL here.
    """
    import ast
    from pathlib import Path as _Path

    import butlers.api.routers.model_settings as module

    tree = ast.parse(_Path(module.__file__).read_text(encoding="utf-8"))
    assignments = [
        node.value.partition("RETURNING")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "UPDATE" in node.value
        and "model_catalog" in node.value
    ]

    assert assignments, "no catalog UPDATE found at all -- the scan stopped matching"
    for statement in assignments:
        assert "last_verified" not in statement
    assert not [
        field for field in module.ModelCatalogUpdate.model_fields if "last_verified" in field
    ]


# ---------------------------------------------------------------------------
# Criterion 4 and 7: verify-all counts what it could not ask separately
# ---------------------------------------------------------------------------


async def test_verify_all_probes_every_enabled_entry_once(scripted, model_settings) -> None:
    """One capability per enabled entry, bounded by the coordinator's own limit."""
    ids = [uuid.uuid4() for _ in range(3)]
    client = scripted(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=5))
    pool = _pool(rows=[{"id": entry_id} for entry_id in ids])

    with _no_audit(model_settings):
        result = await model_settings.run_verify_all_models(pool)

    assert sorted(client.asked) == sorted(ids)
    assert (result.total, result.ok, result.failed, result.unavailable) == (3, 3, 0, 0)
    assert model_settings._VERIFY_ALL_CONCURRENCY == 8


async def test_verify_all_keeps_unavailable_apart_from_failed(scripted, model_settings) -> None:
    """Criterion 6: "we could not ask" is not "the model is broken"."""
    ids = [uuid.uuid4() for _ in range(3)]
    client = _ScriptedClient(
        ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=5),
        ProbeResult(ProbeStatus.COMPLETED, ok=False),
        ProbeResult(ProbeStatus.BUSY),
    )
    pool = _pool(rows=[{"id": entry_id} for entry_id in ids])

    with _no_audit(model_settings) as audit_append:
        _install(model_settings, client)
        result = await model_settings.run_verify_all_models(pool)

    assert (result.total, result.ok, result.failed, result.unavailable) == (3, 1, 1, 1)
    note = audit_append.await_args.kwargs["note"]
    assert audit_append.await_args.kwargs["result"] == "error"
    assert audit_append.await_args.kwargs["error"] == "1 of 3 model verifications failed"
    assert "1 verification(s) could not run" in note


async def test_verify_all_writes_no_verification_evidence(scripted, model_settings) -> None:
    """Criterion 7, for the sweep: the coordinator owns every catalog write."""
    client = _ScriptedClient(
        ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=5),
        ProbeResult(ProbeStatus.UNAVAILABLE),
    )
    pool = _pool(rows=[{"id": uuid.uuid4()}, {"id": uuid.uuid4()}])

    with _no_audit(model_settings):
        _install(model_settings, client)
        await model_settings.run_verify_all_models(pool)

    pool.execute.assert_not_awaited()


async def test_verify_all_audits_the_actor_that_actually_ran_it(scripted, model_settings) -> None:
    """Criterion 8: an automated sweep is never attributed to the owner."""
    client = scripted(ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=5))
    pool = _pool(rows=[{"id": uuid.uuid4()}])

    with _no_audit(model_settings) as audit_append:
        await model_settings.run_verify_all_models(
            pool, audit_actor="model_verify_sweep", caller="scheduler"
        )

    assert audit_append.await_args.args[:3] == (pool, "model_verify_sweep", "models.verify_all")
    assert client.asked != []


async def test_verify_all_asks_nothing_when_no_model_is_enabled(scripted, model_settings) -> None:
    """An empty catalog spends no capability and still leaves an audit trail."""
    client = scripted(ProbeResult(ProbeStatus.COMPLETED, ok=True))
    pool = _pool(rows=[])

    with _no_audit(model_settings) as audit_append:
        result = await model_settings.run_verify_all_models(pool)

    assert (result.total, result.ok, result.failed, result.unavailable) == (0, 0, 0, 0)
    assert client.asked == []
    assert audit_append.await_args.kwargs["result"] == "success"


# ---------------------------------------------------------------------------
# Helpers used by the verify-all group
# ---------------------------------------------------------------------------


def _install(model_settings, client: _ScriptedClient) -> None:
    model_settings._probe_client = lambda caller: client  # type: ignore[assignment]


class _no_audit:
    """Silence the audit write and expose the spy, restoring both on exit."""

    def __init__(self, model_settings) -> None:
        self._module = model_settings
        self._original = model_settings.audit.append
        self._probe_client = model_settings._probe_client

    def __enter__(self) -> AsyncMock:
        spy = AsyncMock(return_value=1)
        self._module.audit.append = spy
        return spy

    def __exit__(self, *_exc: object) -> None:
        self._module.audit.append = self._original
        self._module._probe_client = self._probe_client
