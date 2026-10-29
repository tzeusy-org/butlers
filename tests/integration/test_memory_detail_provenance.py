"""Persisted provenance survives real dashboard SQL projections and lifecycle filters.

REQ-module-memory-013; this is read-model proof, not authority-classifier or owner-auth proof.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_persisted_author_and_endorser_survive_dashboard_reads(postgres_container):
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "memory"],
        {"core": "public", "memory": "memory"},
    )
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "memory,public"},
    )
    try:
        author, subject, endorser, fact_id, rule_id = (uuid.uuid4() for _ in range(5))
        for entity, name in [
            (author, "A correspondent"),
            (subject, "The subject"),
            (endorser, "The owner"),
        ]:
            await pool.execute(
                "INSERT INTO public.entities(id, canonical_name, entity_type) VALUES($1,$2,'person')",
                entity,
                name,
            )
        await pool.execute(
            "INSERT INTO facts(id,subject,predicate,content,entity_id,content_authority,"
            "authority_entity_id,request_id) VALUES($1,'The subject','is','A fact',$2,'third_party',$3,$4)",
            fact_id,
            subject,
            author,
            "owner-scheduled-imaginary-device",
        )
        await pool.execute(
            "INSERT INTO rules(id,content,content_authority,authority_entity_id)"
            " VALUES($1,'A rule','third_party',$2)",
            rule_id,
            author,
        )
        db = DatabaseManager()
        db._pools["general"] = pool
        db._butler_schemas["general"] = "memory"
        db._butler_modules["general"] = frozenset({"memory"})
        app = create_authenticated_domain_app()
        app.dependency_overrides[get_db] = lambda: db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            fact = await client.get(f"/api/memory/facts/{fact_id}")
            assert fact.status_code == 200
            data = fact.json()["data"]
            assert data["content_authority"] == "third_party"
            assert data["authority_entity_id"] == str(author)
            assert data["authority_entity_name"] == "A correspondent"
            assert data["entity_name"] == "The subject"
            facts = (await client.get("/api/memory/facts")).json()["data"]
            assert facts[0]["content_authority"] == "third_party"
            assert facts[0]["authority_entity_id"] == str(author)
            held = (await client.get("/api/memory/rules?held=true")).json()["data"]
            assert [row["id"] for row in held] == [str(rule_id)]

            endorsed_at = datetime(2026, 10, 20, 14, tzinfo=UTC)
            await pool.execute(
                "UPDATE rules SET endorsed_at=$2,endorsed_by=$3 WHERE id=$1",
                rule_id,
                endorsed_at,
                endorser,
            )
            rule = await client.get(f"/api/memory/rules/{rule_id}")
            assert rule.status_code == 200
            data = rule.json()["data"]
            assert data["endorsed_by"] == str(endorser)
            assert data["endorsed_by_name"] == "The owner"
            assert data["authority_entity_name"] == "A correspondent"
            assert datetime.fromisoformat(data["endorsed_at"]) == endorsed_at
            assert data["held"] is False
            assert (await client.get("/api/memory/rules?held=true")).json()["data"] == []

            await pool.execute(
                "UPDATE rules SET endorsed_at=NULL,endorsed_by=NULL,retired_at=now() WHERE id=$1",
                rule_id,
            )
            assert (await client.get("/api/memory/rules?held=true")).json()["data"] == []
            await pool.execute(
                "UPDATE rules SET retired_at=NULL,metadata=$2 WHERE id=$1",
                rule_id,
                {"forgotten": True},
            )
            assert (await client.get("/api/memory/rules?held=true")).json()["data"] == []

            await pool.execute(
                "UPDATE facts SET content_authority=NULL,authority_entity_id=NULL WHERE id=$1",
                fact_id,
            )
            unstamped = (await client.get(f"/api/memory/facts/{fact_id}")).json()["data"]
            assert unstamped["content_authority"] is None
            assert unstamped["authority_entity_id"] is None
            assert unstamped["authority_entity_name"] is None
    finally:
        await pool.close()
