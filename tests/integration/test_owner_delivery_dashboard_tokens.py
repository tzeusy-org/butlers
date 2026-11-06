"""A dossier render reuses its live tokens, so page views don't grow the ledger (bu-dhloi1.37)."""

from __future__ import annotations

import asyncio
import shutil
import uuid

import asyncpg
import pytest

from butlers.api.routers import approvals as approvals_router
from butlers.modules.approvals import owner_delivery_bridge as bridge
from tests.integration.test_owner_delivery_ledger import _enable, _park, _redeem

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture
async def dossier_pool(db) -> asyncpg.Pool:
    pool = await db("core", "approvals", max_size=4)
    await pool.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true, worker_enabled = false"
    )
    if not await pool.fetchval("SELECT count(*) FROM public.entities WHERE 'owner' = ANY(roles)"):
        await pool.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Synthetic owner', 'person', ARRAY['owner'])"
        )
    await _enable(pool)
    return pool


async def _render(pool: asyncpg.Pool, action_id: uuid.UUID):
    card = await approvals_router._dashboard_owner_delivery_card(pool, action_id)
    assert card is not None
    return card


async def _token_rows(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("SELECT count(*) FROM public.owner_delivery_tokens")


async def test_rendering_a_dossier_repeatedly_mints_only_once(dossier_pool) -> None:
    action_id = await _park(dossier_pool, card=True)
    first = await _render(dossier_pool, action_id)
    rows = await _token_rows(dossier_pool)

    renders = [await _render(dossier_pool, action_id) for _ in range(20)]

    assert await _token_rows(dossier_pool) == rows
    assert {(card.approve_token, card.reject_token) for card in renders} == {
        (first.approve_token, first.reject_token)
    }
    won = await _redeem(dossier_pool, action_id, renders[-1].approve_token, surface="dashboard")
    assert won.outcome == "won"


async def test_two_actions_never_share_tokens(dossier_pool) -> None:
    one = await _render(dossier_pool, await _park(dossier_pool, 0, card=True))
    other = await _render(dossier_pool, await _park(dossier_pool, 1, card=True))

    assert one.approve_token != other.approve_token
    assert one.subject_id != other.subject_id


async def test_a_consumed_token_is_never_rendered_again(dossier_pool) -> None:
    action_id = await _park(dossier_pool, card=True)
    first = await _render(dossier_pool, action_id)
    await dossier_pool.execute(
        "UPDATE public.owner_delivery_tokens SET consumed_at = clock_timestamp(), "
        "outcome = '{\"revoked\": \"synthetic\"}'::jsonb WHERE action = 'reject'"
    )

    second = await _render(dossier_pool, action_id)

    assert second.reject_token != first.reject_token
    won = await _redeem(dossier_pool, action_id, second.reject_token, surface="dashboard")
    assert won.outcome == "won"


async def test_a_new_generation_mints_tokens_that_can_decide(dossier_pool) -> None:
    action_id = await _park(dossier_pool, card=True)
    first = await _render(dossier_pool, action_id)
    await dossier_pool.execute(
        "UPDATE public.owner_delivery_subjects SET generation = generation + 1 "
        "WHERE source_ref = $1",
        str(action_id),
    )

    second = await _render(dossier_pool, action_id)

    assert second.generation == first.generation + 1
    assert second.approve_token != first.approve_token
    won = await _redeem(dossier_pool, action_id, second.approve_token, surface="dashboard")
    assert won.outcome == "won"


async def test_an_expired_token_is_never_rendered_again(dossier_pool) -> None:
    action_id = await _park(dossier_pool, card=True)
    await dossier_pool.execute(
        "UPDATE pending_actions SET expires_at = clock_timestamp() + interval '1 second' "
        "WHERE id = $1",
        action_id,
    )
    first = await _render(dossier_pool, action_id)
    await asyncio.sleep(1.1)
    await dossier_pool.execute(
        "UPDATE pending_actions SET expires_at = clock_timestamp() + interval '1 hour' "
        "WHERE id = $1",
        action_id,
    )

    second = await _render(dossier_pool, action_id)

    assert second.approve_token != first.approve_token
    won = await _redeem(dossier_pool, action_id, second.approve_token, surface="dashboard")
    assert won.outcome == "won"


async def test_a_render_that_rolled_back_leaves_no_token_to_reuse(dossier_pool) -> None:
    action_id = await _park(dossier_pool, card=True)

    with pytest.raises(RuntimeError, match="synthetic"):
        async with dossier_pool.acquire() as conn, conn.transaction():
            action = await conn.fetchrow(
                "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE", action_id
            )
            await bridge.present_on_dashboard(conn, action)
            raise RuntimeError("synthetic")

    card = await _render(dossier_pool, action_id)
    won = await _redeem(dossier_pool, action_id, card.approve_token, surface="dashboard")
    assert won.outcome == "won"


async def test_a_restarted_api_mints_again_and_the_new_tokens_decide(dossier_pool) -> None:
    action_id = await _park(dossier_pool, card=True)
    first = await _render(dossier_pool, action_id)
    bridge._dashboard_presentations.clear()

    second = await _render(dossier_pool, action_id)

    assert second.approve_token != first.approve_token
    won = await _redeem(dossier_pool, action_id, second.approve_token, surface="dashboard")
    assert won.outcome == "won"


async def test_the_process_keeps_a_bounded_number_of_cards(
    dossier_pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge, "_DASHBOARD_PRESENTATIONS_KEPT", 1)
    bridge._dashboard_presentations.clear()
    first_action = await _park(dossier_pool, 0, card=True)
    second_action = await _park(dossier_pool, 1, card=True)

    await _render(dossier_pool, first_action)
    await _render(dossier_pool, second_action)
    assert len(bridge._dashboard_presentations) == 1

    card = await _render(dossier_pool, first_action)
    won = await _redeem(dossier_pool, first_action, card.reject_token, surface="dashboard")
    assert won.outcome == "won"
