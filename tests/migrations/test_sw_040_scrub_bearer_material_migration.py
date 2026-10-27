"""sw_040: historical message_inbox rows are scrubbed once, and a rerun changes nothing.

bu-q7vx1q.2.  Real PostgreSQL; seeded rows use synthetic codes only.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECEIVED = "2026-10-01T12:00:00+00:00"


def _raw(body: str, sender: str = "no-reply@example.com") -> str:
    return json.dumps(
        {
            "source": {"channel": "email", "provider": "gmail"},
            "event": {"observed_at": _RECEIVED},
            "sender": {"identity": sender},
            "payload": {"raw": {"body": body}, "normalized_text": body},
            "control": {"ingestion_tier": "full"},
        }
    )


def _service_raw() -> str:
    text = "Login code: 55123. Do not share."
    return json.dumps(
        {
            "source": {"channel": "telegram_user_client", "provider": "telegram"},
            "event": {"external_event_id": "123456", "observed_at": _RECEIVED},
            "sender": {"identity": "777000"},
            "payload": {"raw": {"message": text}, "normalized_text": text},
            "control": {"ingestion_tier": "full"},
        }
    )


def _snapshot(engine) -> dict[str, tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id::text, raw_payload::text, normalized_text FROM switchboard.message_inbox"
            )
        )
        return {r[0]: (r[1], r[2]) for r in rows}


def test_sw_040_scrubs_history_once_and_rerun_is_a_noop(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_039")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT switchboard.switchboard_message_inbox_ensure_partition("
                    "CAST(:ts AS timestamptz))"
                ),
                {"ts": _RECEIVED},
            )
            for body in ("Your code is 482913", "Order 12345678 shipped 2026-10-03"):
                conn.execute(
                    text(
                        "INSERT INTO switchboard.message_inbox "
                        "(received_at, raw_payload, normalized_text) "
                        "VALUES (CAST(:ts AS timestamptz), CAST(:raw AS jsonb), :body)"
                    ),
                    {"ts": _RECEIVED, "raw": _raw(body), "body": body},
                )

            conn.execute(
                text(
                    "INSERT INTO switchboard.message_inbox "
                    "(received_at, raw_payload, normalized_text) "
                    "VALUES (CAST(:ts AS timestamptz), CAST(:raw AS jsonb), :body)"
                ),
                {
                    "ts": _RECEIVED,
                    "raw": _service_raw(),
                    "body": "Login code: 55123. Do not share.",
                },
            )

        command.upgrade(config, "switchboard@sw_040")
        after = _snapshot(engine)
        blob = json.dumps(after)
        assert "482913" not in blob
        assert "55123" not in blob
        assert "[auth-code withheld: example.com]" in blob
        assert "Order 12345678 shipped 2026-10-03" in blob
        flagged = [json.loads(raw) for raw, _ in after.values()]
        assert sum(1 for r in flagged if r["control"].get("bearer_scrubbed")) == 2
        service = next(r for r in flagged if r["sender"]["identity"] == "777000")
        assert service["event"] == {"external_event_id": "123456", "observed_at": _RECEIVED}
        assert service["source"]["provider"] == "telegram"

        # Second run: irreversible downgrade is a no-op, re-upgrade changes nothing.
        command.downgrade(config, "switchboard@sw_039")
        command.upgrade(config, "switchboard@sw_040")
        assert _snapshot(engine) == after
    finally:
        engine.dispose()
