"""Prospective canonical migrated-Postgres HA custody proof, never a live DB.

Spec: REQ-connector-home-assistant-001, REQ-connector-home-assistant-002.
"""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.home_assistant import persist_ha_history
from butlers.connectors.home_assistant_filter import HAFilterPersistence, ha_full_payload
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def ha_custody_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard", "relationship"],
        schemas={"switchboard": "switchboard", "relationship": "relationship"},
    )


@pytest.mark.pg_clock
async def test_ha_bearer_custody_real_jsonb_role_history_and_replay(ha_custody_db_url):
    """Actual producer/flush/role/readback, object+legacy replay, failure/rollback."""
    pool = await asyncpg.create_pool(
        ha_custody_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        setup=connector_setup_role,
    )
    endpoint = "home_assistant:synthetic-custody.test:8123"
    at = datetime(2026, 10, 15, 9, tzinfo=UTC)
    event = {
        "event_type": "state_changed",
        "time_fired": at.isoformat(),
        "data": {
            "entity_id": "sensor.synthetic_notification",
            "old_state": {"state": "not_home", "attributes": {}},
            "new_state": {
                "state": "Your code is 482913",
                "attributes": {"nested": [{"note": "Your code is 482913"}]},
            },
        },
    }
    submitted = []

    async def submit(envelope):
        assert "482913" not in json.dumps(envelope)
        submitted.append(envelope)
        await asyncio.sleep(0)  # force lock overlap between the two drain callers

    persistence = HAFilterPersistence(endpoint, pool, submit)
    try:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_user") == "connector_writer"
        persistence.record_discretion_ignore(
            entity_id=event["data"]["entity_id"],
            ha_event=event,
            time_fired=at.isoformat(),
            new_state=event["data"]["new_state"],
        )
        await persistence.flush()
        # Separate acquire proves committed DB representation, not the producer's dict.
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_user") == "connector_writer"
            row = await conn.fetchrow(
                "SELECT id, received_at, full_payload, jsonb_typeof(full_payload) AS kind "
                "FROM connectors.filtered_events WHERE endpoint_identity=$1",
                endpoint,
            )
        assert row is not None and row["kind"] == "object"
        assert isinstance(row["full_payload"], dict)
        assert "482913" not in json.dumps(row["full_payload"])
        assert "auth-code withheld" in row["full_payload"]["payload"]["raw"]["new_state"]["state"]
        # R1: assembled friendly-name context must protect every durable copy,
        # not only the final normalized field. Keep the original inline case.
        contextual_event = copy.deepcopy(event)
        contextual_event["data"]["new_state"] = {
            "state": "482913",
            "attributes": {"friendly_name": "Your verification code", "echo": "482913"},
        }
        contextual_endpoint = endpoint + ":context"
        contextual = HAFilterPersistence(contextual_endpoint, pool, submit)
        contextual.record_discretion_ignore(
            entity_id=event["data"]["entity_id"],
            ha_event=contextual_event,
            time_fired=at.isoformat(),
            friendly_name="Your verification code",
            new_state=contextual_event["data"]["new_state"],
        )
        await contextual.flush()
        async with pool.acquire() as conn:
            contextual_payload = await conn.fetchval(
                "SELECT full_payload FROM connectors.filtered_events WHERE endpoint_identity=$1",
                contextual_endpoint,
            )
        assert isinstance(contextual_payload, dict)
        assert "482913" not in json.dumps(contextual_payload)
        assert "auth-code withheld" in contextual_payload["payload"]["raw"]["new_state"]["state"]
        assert await persist_ha_history(
            pool,
            entity_id="person.synthetic_custody",
            state="home",
            attributes=event["data"]["new_state"]["attributes"],
            recorded_at=at,
        )
        async with pool.acquire() as conn:
            history = await conn.fetchrow(
                "SELECT state, attributes, recorded_at FROM connectors.home_assistant_history "
                "WHERE entity_id=$1",
                "person.synthetic_custody",
            )
        assert history["state"] == "home" and history["recorded_at"] == at
        assert isinstance(history["attributes"], dict)
        assert "482913" not in json.dumps(history["attributes"])

        # Plant a synthetic pre-existing legacy string through the real JSONB
        # codec. Production writes above must remain object-shaped. No backfill.
        legacy = ha_full_payload(
            endpoint_identity=endpoint,
            entity_id="sensor.synthetic_legacy",
            time_fired=at.isoformat(),
            ha_event=event,
        )
        legacy = copy.deepcopy(legacy)
        legacy["payload"]["normalized_text"] = "Your code is 482913"
        legacy["event"]["external_event_id"] = "synthetic-legacy-id"
        await pool.execute(
            "INSERT INTO connectors.filtered_events "
            "(connector_type,endpoint_identity,external_message_id,source_channel,sender_identity,"
            "filter_reason,status,full_payload) VALUES "
            "('home_assistant',$1,'synthetic-legacy','home_assistant','sensor.synthetic_legacy',"
            "'discretion_ignore','replay_pending',$2)",
            endpoint,
            json.dumps(legacy),
        )
        await pool.execute(
            "UPDATE connectors.filtered_events SET status='replay_pending' WHERE id=$1 "
            "AND received_at=$2",
            row["id"],
            row["received_at"],
        )
        await asyncio.gather(persistence.drain_replay(), persistence.drain_replay())
        assert len(submitted) == 2
        assert len({e["event"]["external_event_id"] for e in submitted}) == 2
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT external_message_id,status,error_detail,jsonb_typeof(full_payload) AS kind "
                "FROM connectors.filtered_events WHERE endpoint_identity=$1",
                endpoint,
            )
        assert all(r["status"] == "replay_complete" and r["error_detail"] is None for r in rows)
        assert {r["kind"] for r in rows} == {"object", "string"}

        async def fail_submit(envelope):
            raise RuntimeError("Your code is 482913")

        failing = HAFilterPersistence(endpoint, pool, fail_submit)
        await pool.execute(
            "UPDATE connectors.filtered_events SET status='replay_pending' "
            "WHERE id=$1 AND received_at=$2",
            row["id"],
            row["received_at"],
        )
        await failing.drain_replay()
        async with pool.acquire() as conn:
            failure = await conn.fetchrow(
                "SELECT status,error_detail FROM connectors.filtered_events "
                "WHERE id=$1 AND received_at=$2",
                row["id"],
                row["received_at"],
            )
        assert failure["status"] == "replay_failed"
        assert failure["error_detail"] == "Home Assistant replay submission failed"
        assert "482913" not in failure["error_detail"]

        # Prove rollback visibility using the real helper and a real transaction.
        class RollBackProbe(Exception):
            pass

        with pytest.raises(RollBackProbe):
            async with pool.acquire() as conn:
                async with conn.transaction():
                    assert await persist_ha_history(
                        conn,
                        entity_id="person.rollback_custody",
                        state="home",
                        attributes={},
                        recorded_at=at,
                    )
                    raise RollBackProbe()
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM connectors.home_assistant_history WHERE entity_id=$1",
                    "person.rollback_custody",
                )
                == 0
            )
    finally:
        await pool.close()
