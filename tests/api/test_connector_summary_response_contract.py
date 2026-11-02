"""The canonical connector summary wire contract is typed without losing evidence."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.routers import ingestion_connectors as connectors

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "failed_source", [None, "registry", "hourly", "devices", "cadence", "empty"]
)
async def test_canonical_summary_serialization_and_schema(failed_source):
    now = datetime.now(UTC)

    def row(identity, **changes):
        return (
            dict(
                connector_type="owntracks",
                endpoint_identity=identity,
                operational_role="runtime_instance",
                state="error",
                error_message="Synthetic source unavailable",
                version="synthetic-v1",
                uptime_s=120,
                last_heartbeat_at=now,
                first_seen_at=now - timedelta(days=60),
                archived_at=None,
                counter_messages_ingested=999,
                counter_messages_failed=2,
                parent_endpoint_identity=None,
                checkpoint_cursor=None,
                checkpoint_updated_at=None,
            )
            | changes
        )

    rows = [
        row("synthetic-online"),
        row("synthetic-offline", state="healthy", last_heartbeat_at=None),
        row("synthetic-unknown", operational_role="unknown"),
        row(
            "synthetic-online:cursor",
            operational_role="checkpoint",
            parent_endpoint_identity="synthetic-online",
            checkpoint_cursor="synthetic-position",
            checkpoint_updated_at=now,
        ),
        row("synthetic-orphan", operational_role="checkpoint"),
    ]

    async def fetch(sql, *args):
        if "FROM connector_registry" in sql:
            source = "registry"
            data = [] if failed_source == "empty" else rows
        elif "FROM connectors.owntracks_points" in sql:
            source = "cadence"
            data = []
        elif "MAX(last_seen_at)" in sql:
            source = "devices"
            data = [
                dict(
                    connector_type="owntracks",
                    sender_identity=f"synthetic-device-{n}",
                    last_seen_at=now - timedelta(days=n),
                )
                for n in range(4)
            ]
        else:
            assert "hour_bucket" in sql
            source = "hourly"
            data = [
                dict(
                    connector_type="owntracks",
                    endpoint_identity="synthetic-online",
                    hour_bucket=now.replace(minute=0, second=0, microsecond=0),
                    source=kind,
                    event_count=count,
                )
                for kind, count in [("ingested", 7), ("filtered", 11)]
            ]
        if source == failed_source:
            raise OSError(f"synthetic {source} source unavailable")
        return data

    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=fetch)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = FastAPI()
    app.include_router(connectors.router)
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/ingestion/connectors/summaries")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "meta"} and body["meta"] == {}
    data = body["data"]
    if failed_source == "registry":
        assert data == {"connectors": [], "connector_registry_available": False}
    else:
        assert set(data) == {
            "connectors",
            "connector_registry_available",
            "unparented_checkpoints",
            "unclassified_count",
            "device_liveness_available",
            "hourly_events_available",
            "owntracks_cadence_available",
        }
        assert data["connector_registry_available"] is True
        for source, field in [
            ("hourly", "hourly_events_available"),
            ("devices", "device_liveness_available"),
            ("cadence", "owntracks_cadence_available"),
        ]:
            assert data[field] is (failed_source != source)
        if failed_source == "empty":
            assert data["connectors"] == []
            assert data["unclassified_count"] == 0
        else:
            entries = {entry["endpoint_identity"]: entry for entry in data["connectors"]}
            assert set(entries) == {"synthetic-online", "synthetic-offline", "synthetic-unknown"}
            online = entries["synthetic-online"]
            assert set(online) == {
                "connector_type",
                "endpoint_identity",
                "operational_role",
                "liveness",
                "state",
                "error_message",
                "version",
                "uptime_s",
                "last_heartbeat_at",
                "first_seen_at",
                "archived",
                "archived_at",
                "archive_candidate",
                "operational_warnings",
                "today",
                "hourly_events",
                "hourly_filtered_events",
                "devices",
                "checkpoints",
            }
            assert (online["liveness"], online["state"], online["operational_role"]) == (
                "online",
                "error",
                "runtime_instance",
            )
            assert (
                entries["synthetic-offline"]["liveness"],
                entries["synthetic-offline"]["state"],
            ) == ("offline", "healthy")
            assert entries["synthetic-unknown"]["liveness"] == "unclassified"
            assert data["unclassified_count"] == 1
            assert online["today"] == {
                "messages_ingested": 0 if failed_source == "hourly" else 7,
                "messages_failed": 2,
            }
            assert online["hourly_events"] == [0] * 23 + [0 if failed_source == "hourly" else 7]
            assert online["hourly_filtered_events"] == [0] * 23 + [
                0 if failed_source == "hourly" else 11
            ]
            assert bool(online["operational_warnings"]) is (failed_source != "cadence")
            if failed_source == "devices":
                assert online["devices"] is None
            else:
                assert len(online["devices"]) == 4
                assert online["devices"][0] == {
                    "sender_identity": "synthetic-device-0",
                    "last_seen_at": now.isoformat(),
                    "stale": False,
                }
                assert online["devices"][-1]["stale"] is True
            checkpoint = online["checkpoints"][0]
            assert checkpoint == dict(
                connector_type="owntracks",
                endpoint_identity="synthetic-online:cursor",
                parent_endpoint_identity="synthetic-online",
                label="cursor",
                checkpoint_cursor="synthetic-position",
                checkpoint_updated_at=now.isoformat(),
                archived=False,
            )
            assert data["unparented_checkpoints"][0]["endpoint_identity"] == "synthetic-orphan"
            assert "liveness" not in checkpoint and "state" not in checkpoint

    schema = app.openapi()
    schemas = schema["components"]["schemas"]
    root_schema = schema["paths"]["/api/ingestion/connectors/summaries"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    envelope = schemas[root_schema["$ref"].rsplit("/", 1)[1]]
    summaries = schemas[envelope["properties"]["data"]["$ref"].rsplit("/", 1)[1]]
    entry_schema = schemas[summaries["properties"]["connectors"]["items"]["$ref"].rsplit("/", 1)[1]]
    assert {
        "connector_registry_available",
        "hourly_events_available",
        "device_liveness_available",
        "owntracks_cadence_available",
    } <= set(summaries["properties"])
    assert {
        "connector_type",
        "endpoint_identity",
        "liveness",
        "state",
        "error_message",
        "version",
        "uptime_s",
        "last_heartbeat_at",
        "first_seen_at",
        "operational_role",
        "checkpoints",
    } <= set(entry_schema["required"])
    assert "today" in entry_schema["properties"] and "today" not in entry_schema["required"]

    if failed_source is None:
        # Exercise the actual declared response model through FastAPI: an
        # optional today is omitted, unknown secret-like fields are discarded,
        # and malformed typed data is refused rather than leaked as a dict.
        route = next(
            r for r in connectors.router.routes if r.path == "/api/ingestion/connectors/summaries"
        )
        probe_data = deepcopy(data)
        probe_entry = probe_data["connectors"][0]
        probe_entry.pop("today")
        probe_entry["access_token"] = "synthetic-credential-must-not-serialize"

        @app.get(
            "/contract-probe",
            response_model=route.response_model,
            response_model_exclude_unset=True,
        )
        def probe(invalid: bool = False):
            result = deepcopy(probe_data)
            if invalid:
                result["connectors"][0]["uptime_s"] = "not-an-integer"
            return {"data": result, "meta": {}}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            valid = await client.get("/contract-probe")
            invalid = await client.get("/contract-probe?invalid=true")
        assert valid.status_code == 200
        actual = valid.json()["data"]["connectors"][0]
        assert "today" not in actual and "access_token" not in actual
        assert "synthetic-credential-must-not-serialize" not in valid.text
        assert invalid.status_code == 500
