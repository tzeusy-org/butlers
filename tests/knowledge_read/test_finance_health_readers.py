"""Finance and health readers over a fake connection: arguments, enums, cursors and shapes."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from butlers.knowledge_read.core import ReadContext, ReadError
from butlers.knowledge_read.finance import finance_obligations, finance_records, finance_summary
from butlers.knowledge_read.health import health_profile, health_records, health_series
from butlers.knowledge_read.records import record_kinds

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CONTEXT = ReadContext(
    sub="github|1", as_of=NOW - timedelta(seconds=5), now=NOW, cursor_key=b"k" * 32
)


class FakeConn:
    """Answers each ``fetch`` from the first script entry whose key occurs in the SQL."""

    def __init__(self, script: dict[str, list[dict[str, Any]]] | None = None):
        self.script = script or {}
        self.calls: list[tuple[str, tuple]] = []

    def is_in_transaction(self) -> bool:
        return True

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        for key, rows in self.script.items():
            if key in sql:
                return rows
        return []


def _refused(coro) -> ReadError:
    import asyncio

    with pytest.raises(ReadError) as caught:
        asyncio.run(coro)
    return caught.value


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _row(source_id: str, **values: Any) -> dict[str, Any]:
    return {"source_id": source_id, "fields": json.dumps(values)}


def test_kind_enums_are_generated_from_the_manifest():
    finance = set(record_kinds("finance", "finance_records"))
    health = set(record_kinds("health", "health_records"))
    assert {"facts", "balance_snapshots", "claim_match_bindings", "json_fields_public"} <= finance
    assert {"facts", "visit_briefs", "calendar_events", "measurements_public"} <= health
    assert finance.isdisjoint(health - {"facts", "json_fields_public"})
    assert not finance & {"butler_secrets", "sessions", "state"}


@pytest.mark.parametrize("tool", [finance_records, health_records])
def test_records_refuse_before_any_query(tool):
    conn = FakeConn()
    bad = [
        {},
        {"kind": "butler_secrets"},
        {"kind": "sessions"},
        {"kind": "facts", "query": "x" * 257},
        {"kind": "facts", "limit": 201},
        {"kind": "facts", "bogus": 1},
        {"kind": "facts", "since": "2025-01-01", "until": "2026-06-01"},
        {"kind": "visit_briefs", "predicate": "allergy"},
        {"kind": "claim_match_bindings", "query": "x"},
        {"kind": "facts", "since": "yesterday"},
    ]
    for arguments in bad:
        assert _refused(tool(conn, CONTEXT, arguments)).code == "invalid_argument"
    assert conn.calls == []


def test_a_kind_without_a_time_column_refuses_since_and_until():
    conn = FakeConn()
    error = _refused(
        finance_records(conn, CONTEXT, {"kind": "payment_destinations", "since": "2026-01-01"})
    )
    assert (error.code, error.argument) == ("invalid_argument", "since")
    assert conn.calls == []


def test_records_domain_disabled_and_stale():
    off = replace(CONTEXT, disabled_domains=frozenset({"health"}))
    error = _refused(health_records(FakeConn(), off, {"kind": "meals"}))
    assert (error.code, error.domain) == ("domain_disabled", "health")
    stale = replace(
        CONTEXT, as_of=NOW - timedelta(minutes=16), disabled_domains=frozenset({"health"})
    )
    assert _refused(health_records(FakeConn(), stale, {"kind": "meals"})).code == "projection_stale"


def test_records_shape_json_fields_and_paging():
    rows = [
        _row("b2", account_id="a1", balance="1200.50", currency="SGD", as_of_date="2026-03-02"),
        _row("b1", account_id="a1", balance="9.00", currency="SGD", as_of_date="2026-03-01"),
    ]
    for row, day in zip(rows, ("2026-03-02", "2026-03-01"), strict=True):
        row["at"] = day
        row["at_text"] = day
    json_rows = [
        {
            "fields": json.dumps(
                {
                    "row_id": "b2",
                    "path": "metadata.n",
                    "value_type": "number",
                    "value_numeric": "3",
                    "value_text": "3",
                }
            )
        },
        {
            "fields": json.dumps(
                {
                    "row_id": "b2",
                    "path": "metadata.ok",
                    "value_type": "boolean",
                    "value_text": "true",
                    "value_numeric": None,
                }
            )
        },
    ]
    conn = FakeConn(
        {"FROM mcp_read.finance__json_fields_public": json_rows, "balance_snapshots": rows}
    )
    args = {"kind": "balance_snapshots", "since": "2026-01-01", "until": "2026-04-01", "limit": 1}
    page = _run(finance_records(conn, CONTEXT, args))
    assert [i["id"] for i in page["items"]] == ["b2"]
    item = page["items"][0]
    assert item["kind"] == "balance_snapshots"
    assert item["fields"]["balance"] == "1200.50"
    assert item["json_fields"] == {"metadata.n": "3", "metadata.ok": True}
    assert item["source"]["uri"] == "butlers://finance/balance_snapshots/b2"
    assert page["next_cursor"]
    # The window is applied as dates on a date column.
    assert conn.calls[0][1][1] == date(2026, 1, 1) and conn.calls[0][1][2] == date(2026, 4, 1)
    # The cursor is bound to its arguments: a different kind or limit refuses it.
    follow = {**args, "cursor": page["next_cursor"]}
    _run(finance_records(FakeConn(), CONTEXT, follow))
    assert _refused(finance_records(FakeConn(), CONTEXT, {**follow, "limit": 2})).code == (
        "invalid_argument"
    )
    assert _refused(finance_records(FakeConn(), CONTEXT, {**follow, "kind": "bills"})).code == (
        "invalid_argument"
    )
    other = replace(CONTEXT, sub="github|2")
    assert _refused(finance_records(FakeConn(), other, follow)).code == "invalid_argument"
    # A cursor of another tool is refused.
    assert (
        _refused(
            health_records(FakeConn(), CONTEXT, {"kind": "meals", "cursor": page["next_cursor"]})
        ).code
        == "invalid_argument"
    )


def test_fact_query_is_lexical_and_drops_the_search_vector_column():
    facts = [
        {
            **_row("f1", predicate="allergy", content="penicillin", search_vector="'x':1"),
            "at": 0.5,
            "at_text": "0.5",
        }
    ]
    conn = FakeConn({"search_vector": facts})
    page = _run(health_records(conn, CONTEXT, {"kind": "facts", "query": "penicillin"}))
    sql, params = conn.calls[0]
    assert "websearch_to_tsquery" in sql and "ts_rank" in sql
    assert params[3] == "penicillin"
    assert "search_vector" not in page["items"][0]["fields"]
    assert page["next_cursor"] is None


def test_finance_summary_arguments_and_shape():
    for bad in (
        {"start": "2026-01-01", "end": "2026-01-31"},
        {"start": "2026-01-01", "end": "2026-01-31", "group_by": "week"},
        {"start": "2025-01-01", "end": "2026-06-01", "group_by": "month"},
        {"start": "2026-01-01", "end": "2026-01-31", "group_by": "month", "currency": "SG"},
    ):
        assert _refused(finance_summary(FakeConn(), CONTEXT, bad)).code == "invalid_argument"
    conn = FakeConn(
        {
            "GROUP BY currency": [
                {"currency": "SGD", "total": "12.30", "n": 1},
                {"currency": "USD", "total": "5.00", "n": 2},
            ],
            "grp": [
                {"grp": "2026-01", "currency": "SGD", "total": "12.30", "n": 1},
                {"grp": "2026-01", "currency": "USD", "total": "5.00", "n": 2},
            ],
        }
    )
    result = _run(
        finance_summary(
            conn, CONTEXT, {"start": "2026-01-01", "end": "2026-01-31", "group_by": "month"}
        )
    )
    assert result["totals"] == [
        {"currency": "SGD", "total": "12.30", "count": 1},
        {"currency": "USD", "total": "5.00", "count": 2},
    ]
    assert result["groups"][0] == {
        "key": "2026-01",
        "currency": "SGD",
        "total": "12.30",
        "count": 1,
    }
    assert result["source"]["uri"] == "butlers://finance/finance_summary"
    assert "truncated" not in result


def test_finance_obligations_horizon_and_shape():
    assert _refused(finance_obligations(FakeConn(), CONTEXT, {"horizon_days": 91})).code == (
        "invalid_argument"
    )
    assert _refused(finance_obligations(FakeConn(), CONTEXT, {"horizon_days": 0})).code == (
        "invalid_argument"
    )
    conn = FakeConn(
        {
            "finance__bills": [
                _row(
                    "b1",
                    payee="Landlord",
                    amount="900.00",
                    currency="SGD",
                    frequency="monthly",
                    due_date="2026-11-19",
                    status="pending",
                    payment_method="giro",
                    autopay=True,
                )
            ],
            "finance__subscriptions": [
                _row(
                    "s1",
                    service="Stream",
                    amount="9.99",
                    currency="USD",
                    frequency="monthly",
                    next_renewal="2026-11-12",
                    status="active",
                    auto_renew=True,
                    cancellation_url="https://x/cancel",
                    notice_period_days=7,
                    cancel_by="2026-11-05",
                )
            ],
        }
    )
    result = _run(finance_obligations(conn, CONTEXT, {"horizon_days": 30}))
    assert [(i["kind"], i["payee"], i["next_due"]) for i in result["items"]] == [
        ("subscription", "Stream", "2026-11-12"),
        ("bill", "Landlord", "2026-11-19"),
    ]
    bill = result["items"][1]
    assert bill["cancel_by"] is None and bill["cancellation_url"] is None
    assert bill["autopay"] is True and bill["amount"] == "900.00"
    assert bill["source"]["uri"] == "butlers://finance/bills/b1"
    # Both queries are bounded by today (UTC) and today + horizon.
    assert all(p[1:3] == (date(2026, 11, 9), date(2026, 12, 9)) for _, p in conn.calls)


def test_health_series_refuses_unknown_metric_and_oversized_raw():
    window = {"start": "2026-03-01", "end": "2026-03-31", "bucket": "raw"}
    conn = FakeConn({"DISTINCT": [{"metric": "measurement_weight"}]})
    assert _refused(health_series(conn, CONTEXT, {**window, "metric": "nope"})).code == (
        "invalid_argument"
    )
    assert _refused(
        health_series(conn, CONTEXT, {**window, "metric": "x", "bucket": "hour"})
    ).code == ("invalid_argument")
    many = [{"t": "2026-03-01T00:00:00+00:00", "value": "70", "unit": "kg"}] * 2001
    conn = FakeConn({"DISTINCT": [{"metric": "measurement_weight"}], "measurements_public": many})
    error = _refused(health_series(conn, CONTEXT, {**window, "metric": "measurement_weight"}))
    assert error.code == "too_large"
    assert "70" not in json.dumps(error.to_dict())


def test_health_series_day_bucket_returns_the_mean_in_the_unit():
    conn = FakeConn(
        {
            "DISTINCT": [{"metric": "measurement_weight"}],
            "measurements_public": [{"t": date(2026, 3, 1), "value": "70.5", "unit": "kg"}],
        }
    )
    result = _run(
        health_series(
            conn,
            CONTEXT,
            {
                "metric": "measurement_weight",
                "start": "2026-03-01",
                "end": "2026-03-02",
                "bucket": "day",
            },
        )
    )
    assert result["unit"] == "kg"
    assert result["points"] == [{"t": "2026-03-01", "value": 70.5}]
    assert result["source"]["uri"] == "butlers://health/health_series"


def _profile_conn() -> FakeConn:
    return FakeConn(
        {
            "health__json_fields_public": [
                {
                    "fields": json.dumps(
                        {
                            "row_id": "f1",
                            "path": "metadata.severity",
                            "value_type": "string",
                            "value_text": "high",
                            "value_numeric": None,
                        }
                    )
                }
            ],
            "health__medications_public": [
                _row(
                    "m1",
                    name="Metformin",
                    dosage="500mg",
                    frequency="daily",
                    schedule=["08:00"],
                    active=False,
                    notes="with food",
                    recorded_at="2026-01-02T00:00:00+00:00",
                )
            ],
            "health__conditions_public": [
                _row("c1", name="Asthma", status="active", diagnosed_at="2020-05-01", notes="mild")
            ],
            "health__facts": [
                _row(
                    "f1",
                    predicate="allergy",
                    content="allergic to penicillin",
                    entity_id="e1",
                    valid_at="2026-02-01T00:00:00+00:00",
                )
            ],
        }
    )


def test_health_profile_sections():
    conn = _profile_conn()
    full = _run(health_profile(conn, CONTEXT, {}))
    med = full["medications"][0]
    assert (med["name"], med["dose"], med["status"], med["notes"]) == (
        "Metformin",
        "500mg",
        "stopped",
        "with food",
    )
    assert med["schedule"] == ["08:00"] and med["since"] == "2026-01-02T00:00:00+00:00"
    assert full["conditions"][0]["since"] == "2020-05-01"
    allergy = full["allergies"][0]
    assert allergy["text"] == "allergic to penicillin" and allergy["fields"] == {
        "metadata.severity": "high"
    }
    assert allergy["source"]["uri"] == "butlers://health/facts/f1"
    assert full["providers"][0]["entity_id"] == "e1"

    narrow = _run(health_profile(_profile_conn(), CONTEXT, {"sections": ["conditions"]}))
    assert [c["name"] for c in narrow["conditions"]] == ["Asthma"]
    assert narrow["medications"] == narrow["allergies"] == narrow["providers"] == []
    assert _refused(health_profile(FakeConn(), CONTEXT, {"sections": ["labs"]})).code == (
        "invalid_argument"
    )
    off = replace(CONTEXT, disabled_domains=frozenset({"health"}))
    assert _refused(health_profile(FakeConn(), off, {})).domain == "health"
