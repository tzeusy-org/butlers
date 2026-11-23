from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from butlers.db import register_jsonb_codec
from butlers.jobs import meeting_card
from tests.migrations.test_core_365_calendar_source_subject import _run


@pytest.fixture
def card_url(fresh_db):
    return fresh_db("core", "memory", "relationship", schemas={"core": "general"})


async def test_card_includes_an_already_running_household_event(card_url, monkeypatch):
    now = datetime(2026, 11, 23, 12, tzinfo=UTC)
    db_url = card_url
    for name, starts, ends, role, metadata in (
        (
            "owner",
            10,
            40,
            "owner",
            '{"attendees":[{"email":"synthetic@example.test","responseStatus":"accepted"}]}',
        ),
        ("Ongoing", -10, 25, "household_member", "{}"),
        ("Upcoming", 12, 20, "household_member", "{}"),
    ):
        subject = None if role == "owner" else uuid4()
        if subject is not None:
            _run(
                db_url,
                "INSERT INTO public.entities (id, canonical_name, entity_type) "
                "VALUES (:id, :name, 'person')",
                {"id": subject, "name": name},
            )
        _run(
            db_url,
            "WITH source AS (INSERT INTO calendar_sources "
            "(source_key, source_kind, time_role, role_basis, role_subject_entity_id) "
            "VALUES (:name,'provider_event',:role,'owner_confirmed',:subject) RETURNING id) "
            "INSERT INTO calendar_events "
            "(source_id,origin_ref,title,timezone,starts_at,ends_at,status,metadata,source_butler) "
            "SELECT id,:name,'opaque fixture','UTC',:starts,:ends,'confirmed',CAST(:meta AS jsonb),'general' "
            "FROM source",
            {
                "name": name,
                "role": role,
                "subject": subject,
                "starts": now + timedelta(minutes=starts),
                "ends": now + timedelta(minutes=ends),
                "meta": metadata,
            },
        )
    monkeypatch.setattr(meeting_card, "MEETING_CARD_LIVE", True)
    proposer = AsyncMock(return_value={"status": "accepted"})
    dsn = make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)
    async with asyncpg.create_pool(
        dsn, init=register_jsonb_codec, server_settings={"search_path": "general,public"}
    ) as pool:
        result = await meeting_card.run_meeting_card(
            pool,
            insight_proposer=proposer,
            insight_withdrawer=AsyncMock(),
            now=now,
        )
    assert result["proposed"] == 1
    message = proposer.await_args.kwargs["message"]
    assert "Also on Upcoming's calendar" in message, (
        "future overlap should provide a positive control"
    )
    assert "Also on Ongoing's calendar" in message, "already-running overlap was omitted"
