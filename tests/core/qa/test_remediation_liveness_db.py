"""Real-Postgres proofs for QA remediation-loop liveness (REQ-qa-remediation-liveness-*, bu-ejow5p.8).

Covers the controller's condition episodes, the honest ``staffer_status`` and the cause-bound
breaker reset against the real core-migrated schema. Docker-backed, so CI-only in the agent
sandbox.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db, get_mcp_manager
from butlers.core.qa import publisher_wiring
from butlers.core.qa import remediation_liveness as rl
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_BASE_URL = "http://testserver"
_PUBLICATION_UNAVAILABLE = (
    "publication_unavailable: automated QA publication is disabled until the isolated "
    "publisher lands; nothing was pushed and no PR was created or changed"
)


async def _patrol(pool: asyncpg.Pool, status: str = "clean"):
    return await pool.fetchval(
        "INSERT INTO public.qa_patrols (status, completed_at) VALUES ($1, now()) RETURNING id",
        status,
    )


async def _attempt(pool: asyncpg.Pool, patrol_id, status: str, detail: str | None) -> None:
    await pool.execute(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, status, severity, exception_type, call_site,
             healing_session_id, qa_patrol_id, error_detail)
        VALUES ($1, 'general', $2, 2, 'RuntimeError', 'src/foo.py:bar',
                gen_random_uuid(), $3, $4)
        """,
        "c" * 64,
        status,
        patrol_id,
        detail,
    )


def _app_for(pool: asyncpg.Pool):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    return app


async def _call(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        return await client.request(method, path, **kwargs)


async def _active(pool: asyncpg.Pool, source: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, fingerprint, state FROM public.infra_conditions "
        "WHERE source = $1 AND state IN ('open', 'aging')",
        source,
    )


async def test_unconfigured_publisher_opens_one_episode_and_a_rescan_opens_none(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    for _ in range(3):
        await _attempt(pool, patrol_id, "failed", _PUBLICATION_UNAVAILABLE)

    await rl.reconcile_remediation_loop(pool)
    first = await _active(pool, rl.CAPABILITY_SOURCE)
    assert [row["fingerprint"] for row in first] == [
        rl.capability_fingerprint("publisher_unconfigured")
    ]

    await rl.reconcile_remediation_loop(pool)
    second = await _active(pool, rl.CAPABILITY_SOURCE)
    assert [row["id"] for row in second] == [row["id"] for row in first]
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.infra_conditions WHERE source = $1",
            rl.CAPABILITY_SOURCE,
        )
        == 1
    )
    # Three publication-stage opportunities, no merge: the funnel absence is measurable.
    assert len(await _active(pool, rl.FUNNEL_SOURCE)) == 1


async def test_condition_resolves_only_after_a_measured_recovery(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    await _attempt(pool, patrol_id, "failed", _PUBLICATION_UNAVAILABLE)
    await rl.reconcile_remediation_loop(pool)
    assert len(await _active(pool, rl.CAPABILITY_SOURCE)) == 1

    # A recovery is a later *closed* launched attempt with a non-structural outcome: closed_at is
    # database-stamped only on a terminal status (core_305), and ``pr_open`` is not terminal, so
    # it is invisible to the latest-closed-attempt read until it merges.
    await _attempt(pool, patrol_id, "pr_merged", None)
    await rl.reconcile_remediation_loop(pool)
    assert await _active(pool, rl.CAPABILITY_SOURCE) == []


async def test_funnel_is_unmeasurable_with_no_opportunities(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    await _attempt(pool, patrol_id, "failed", "agent crashed")
    await rl.reconcile_remediation_loop(pool)
    assert (await rl.measure_funnel(pool)).state == "unmeasurable"
    assert await _active(pool, rl.FUNNEL_SOURCE) == []


async def test_summary_is_not_healthy_while_a_loop_condition_is_active(db_pool) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool)
    await _attempt(pool, patrol_id, "failed", _PUBLICATION_UNAVAILABLE)

    healthy = await _call(_app_for(pool), "GET", "/api/qa/summary")
    assert healthy.json()["data"]["staffer_status"] == "healthy"

    await rl.reconcile_remediation_loop(pool)
    degraded = (await _call(_app_for(pool), "GET", "/api/qa/summary")).json()["data"]
    assert degraded["staffer_status"] == "degraded"
    assert degraded["loop_conditions_known"] is True
    assert [c["cause"] for c in degraded["loop_conditions"]] == ["publisher_unconfigured"]


@pytest.mark.pg_clock
async def test_reset_is_refused_without_a_cause_while_the_cause_is_structural(
    db_pool, monkeypatch
) -> None:
    pool = db_pool
    patrol_id = await _patrol(pool, "error")
    for _ in range(5):
        await _attempt(pool, patrol_id, "failed", _PUBLICATION_UNAVAILABLE)
    app = _app_for(pool)

    for body in (None, {"cause_ref": "execution_failure:retry"}):
        resp = await _call(app, "POST", "/api/qa/circuit-breaker/reset", json=body)
        assert resp.status_code == 200
        assert resp.json()["data"]["reset"] is False
    # Each refusal is persisted as an owner-visible decision, and none of them clears the breaker.
    refused = await pool.fetch(
        "SELECT cause_ref, refusal_reason FROM public.breaker_resets WHERE outcome = 'refused'"
    )
    assert sorted((r["cause_ref"], r["refusal_reason"]) for r in refused if r["cause_ref"]) == [
        ("retry", "structural_cause_not_named:publisher_unconfigured")
    ]
    assert len(refused) == 2
    # An identical refusal inside the window is not recorded again.
    await _call(
        app,
        "POST",
        "/api/qa/circuit-breaker/reset",
        json={"cause_ref": "execution_failure:retry"},
    )
    assert (
        await pool.fetchval("SELECT count(*) FROM public.breaker_resets WHERE outcome = 'refused'")
        == 2
    )
    assert (
        await pool.fetchval("SELECT count(*) FROM public.breaker_resets WHERE outcome = 'reset'")
        == 0
    )
    assert (await _call(app, "GET", "/api/qa/circuit-breaker")).json()["data"]["tripped"] is True

    # The right code with a fresh reference is still refused while the capability is missing.
    still = (
        await _call(
            app,
            "POST",
            "/api/qa/circuit-breaker/reset",
            json={"cause_ref": "publisher_unconfigured:PR-1"},
        )
    ).json()["data"]
    assert still["reset"] is False
    assert still["refusal_reason"] == "structural_cause_present:publisher_unconfigured"

    monkeypatch.setattr(publisher_wiring, "production_publisher_wired", lambda: True)
    ok = await _call(
        app,
        "POST",
        "/api/qa/circuit-breaker/reset",
        json={"cause_ref": "publisher_unconfigured:PR-1"},
    )
    assert ok.json()["data"]["reset"] is True
    row = await pool.fetchrow(
        "SELECT cause_code, cause_ref FROM public.breaker_resets WHERE outcome = 'reset'"
    )
    assert (row["cause_code"], row["cause_ref"]) == ("publisher_unconfigured", "PR-1")


@pytest.mark.pg_clock
async def test_a_reference_whose_claim_was_disproved_cannot_be_reused(db_pool, monkeypatch) -> None:
    monkeypatch.setattr(publisher_wiring, "production_publisher_wired", lambda: True)
    pool = db_pool
    patrol_id = await _patrol(pool, "error")
    for _ in range(5):
        await _attempt(pool, patrol_id, "failed", _PUBLICATION_UNAVAILABLE)
    app = _app_for(pool)
    claim = {"cause_ref": "publisher_unconfigured:PR-1"}
    assert (await _call(app, "POST", "/api/qa/circuit-breaker/reset", json=claim)).json()["data"][
        "reset"
    ] is True

    # The "fix" did not land: five more identical structural failures.
    for _ in range(5):
        await _attempt(pool, patrol_id, "failed", _PUBLICATION_UNAVAILABLE)
    again = (await _call(app, "POST", "/api/qa/circuit-breaker/reset", json=claim)).json()["data"]
    assert again["reset"] is False
    assert again["refusal_reason"] == "structural_cause_unchanged:publisher_unconfigured"

    fresh = {"cause_ref": "publisher_unconfigured:PR-2"}
    assert (await _call(app, "POST", "/api/qa/circuit-breaker/reset", json=fresh)).json()["data"][
        "reset"
    ] is True


@pytest.mark.pg_clock
async def test_an_unprobed_cause_does_not_wedge_the_breaker(db_pool) -> None:
    """Retention has no probe: a fresh reference resets (marked unprobed), a repeat is refused."""
    pool = db_pool
    patrol_id = await _patrol(pool, "error")
    retention = "publication_unretained: export_pruned"
    for _ in range(5):
        await _attempt(pool, patrol_id, "failed", retention)
    app = _app_for(pool)
    claim = {"cause_ref": "proposal_retention_unavailable:PR-9"}
    ok = (await _call(app, "POST", "/api/qa/circuit-breaker/reset", json=claim)).json()["data"]
    assert ok["reset"] is True
    row = await pool.fetchrow(
        "SELECT outcome, refusal_reason FROM public.breaker_resets WHERE outcome = 'reset'"
    )
    assert (row["outcome"], row["refusal_reason"]) == ("reset", "unprobed")
    assert (await _call(app, "GET", "/api/qa/circuit-breaker")).json()["data"]["tripped"] is False

    for _ in range(5):
        await _attempt(pool, patrol_id, "failed", retention)
    again = (await _call(app, "POST", "/api/qa/circuit-breaker/reset", json=claim)).json()["data"]
    assert again["reset"] is False
    assert again["refusal_reason"] == "structural_cause_unchanged:proposal_retention_unavailable"
