"""Finance and health readers over a genuinely replicated projection (RFC 0039 D10).

Source rows are written on the primary, replicate through the real publication and are read as
``mcp_public_ro``. Covers REQ-public-mcp-surface-009, -010, -011 and -014.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text

from butlers.knowledge_read.core import MAX_RESPONSE_BYTES, ReadContext, ReadError, read_context
from butlers.knowledge_read.finance import (
    finance_ledger,
    finance_obligations,
    finance_records,
    finance_summary,
)
from butlers.knowledge_read.health import health_profile, health_records, health_series
from butlers.knowledge_read.manifest import load_manifest
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program
from butlers.testing.public_mcp import public_mcp_topology

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

KEY = b"process-lifetime-test-key-000000"


def _wait(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while not (value := predicate()):
        assert time.monotonic() < deadline, "the subscriber did not reach the observation"
        time.sleep(0.05)
    return value


def _scalar(engine, sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


@pytest.fixture(scope="module")
def projected():
    with public_mcp_topology() as topology:
        projection = topology.projection_engine()
        manifest = load_manifest()
        try:
            with topology.engine.begin() as conn:
                reconcile_connection(conn, manifest)
            result = topology.execute(render_owner_program(manifest))
            assert result.exit_code == 0, repr(result.output)[-4000:]
            _wait(
                lambda: _scalar(
                    projection,
                    "SELECT COALESCE(bool_and(copy_complete AND digest=:d), false) "
                    "FROM mcp_read.manifest_applied",
                    {"d": manifest.digest},
                )
            )
            yield topology, projection
        finally:
            projection.dispose()


def _barrier(projected):
    """Wait until a committed write to an already-published control row has replicated."""
    topology, projection = projected
    with topology.engine.begin() as conn:
        changed = conn.execute(
            text(
                "UPDATE mcp_surface.controls SET changed_at=GREATEST(clock_timestamp(),"
                "changed_at+interval '1 microsecond') WHERE domain='all' RETURNING changed_at"
            )
        ).scalar_one()
    _wait(
        lambda: _scalar(
            projection,
            "SELECT changed_at=:c FROM mcp_surface.controls WHERE domain='all'",
            {"c": changed},
        )
    )


@pytest.fixture
async def primary(projected):
    topology, _ = projected
    url = topology.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2, command_timeout=15)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def reader(projected):
    topology, _ = projected
    conn = await topology.reader_connection()
    try:
        assert await conn.fetchval("SELECT current_user") == "mcp_public_ro"
        yield conn
    finally:
        await conn.close()


async def _context(reader, *, disabled=frozenset()) -> ReadContext:
    context = await read_context(reader, sub="github|1", cursor_key=KEY, disabled_domains=disabled)
    assert context.freshness == "fresh"
    return context


async def _refused(call) -> ReadError:
    with pytest.raises(ReadError) as caught:
        await call
    return caught.value


def _day(offset: int):
    return datetime.now(UTC).date() + timedelta(days=offset)


async def test_finance_summary_obligations_and_records(projected, primary, reader):
    """REQ-public-mcp-surface-009 and -014 against replicated finance rows."""
    account = await primary.fetchval(
        "INSERT INTO finance.accounts(institution,type,name,last_four,currency) "
        "VALUES ('Sum Bank','checking','Sum Account','9876','SGD') RETURNING id"
    )
    spent = _day(-3)
    posted = datetime(spent.year, spent.month, spent.day, 10, tzinfo=UTC)
    transactions, bills, subscriptions, snapshots, facts = [], [], [], [], []
    try:
        for merchant, amount, currency, category, direction, deleted in (
            ("Cafe", "12.30", "SGD", "dining", "debit", False),
            ("Bistro", "7.70", "SGD", "dining", "debit", False),
            ("Airline", "100.05", "USD", "travel", "debit", False),
            ("Own Transfer", "500.00", "SGD", "transfer", "debit", False),
            ("Salary", "900.00", "SGD", "income", "credit", False),
            ("Ghost", "99.00", "SGD", "dining", "debit", True),
        ):
            transactions.append(
                await primary.fetchval(
                    "INSERT INTO finance.transactions(account_id,posted_at,merchant,amount,"
                    "currency,direction,category,deleted_at) VALUES ($1,$2,$3,$4::numeric,$5,$6,"
                    "$7,CASE WHEN $8 THEN now() END) RETURNING id",
                    account,
                    posted,
                    merchant,
                    amount,
                    currency,
                    direction,
                    category,
                    deleted,
                )
            )
        bills.append(
            await primary.fetchval(
                "INSERT INTO finance.bills(payee,amount,currency,due_date,frequency,status,"
                "payment_method) VALUES ('Landlord',1200.50,'SGD',$1,'monthly','pending','giro') "
                "RETURNING id",
                _day(10),
            )
        )
        for payee, status in (("Late Pending", "pending"), ("Late Paid", "paid")):
            bills.append(
                await primary.fetchval(
                    "INSERT INTO finance.bills(payee,amount,currency,due_date,frequency,status) "
                    "VALUES ($1,7,'SGD',$2,'monthly',$3) RETURNING id",
                    payee,
                    _day(-20),
                    status,
                )
            )
        bills.append(
            await primary.fetchval(
                "INSERT INTO finance.bills(payee,amount,currency,due_date,frequency,status) "
                "VALUES ('Paid Up',5,'SGD',$1,'monthly','paid') RETURNING id",
                _day(5),
            )
        )
        for service, renewal in (("Soon Stream", _day(12)), ("Far Stream", _day(45))):
            subscriptions.append(
                await primary.fetchval(
                    "INSERT INTO finance.subscriptions(service,amount,currency,frequency,"
                    "next_renewal,status) VALUES ($1,9.99,'USD','monthly',$2,'active') "
                    "RETURNING id",
                    service,
                    renewal,
                )
            )
        for offset, balance in ((-30, "1000.10"), (-10, "1200.20"), (-200, "5.00")):
            snapshots.append(
                await primary.fetchval(
                    "INSERT INTO finance.balance_snapshots(account_id,balance,currency,as_of_date,"
                    "source) VALUES ($1,$2::numeric,'SGD',$3,'test') RETURNING id",
                    account,
                    balance,
                    _day(offset),
                )
            )
        facts.append(
            await primary.fetchval(
                "INSERT INTO finance.facts(subject,predicate,content,sensitivity,metadata,search_vector) "
                "VALUES ('owner','note','Prefers aisle seats on long flights','confidential',"
                '\'{"hint":"seat-pref"}\'::jsonb,'
                "to_tsvector('english','Prefers aisle seats on long flights')) RETURNING id"
            )
        )
        facts.append(
            await primary.fetchval(
                "INSERT INTO finance.facts(subject,predicate,content,sensitivity,metadata) "
                "VALUES ('owner','budget','Monthly grocery cap is high','normal','{}'::jsonb) "
                "RETURNING id"
            )
        )
        _barrier(projected)
        context = await _context(reader)

        window = {"start": spent.isoformat(), "end": spent.isoformat()}
        summary = await finance_summary(reader, context, {**window, "group_by": "category"})
        # Nothing is dropped or netted: transfers and credits count, per direction.
        assert [
            (t["currency"], t["direction"], t["total"], t["count"]) for t in summary["totals"]
        ] == [
            ("SGD", "credit", "900.00", 1),
            ("SGD", "debit", "520.00", 3),
            ("USD", "debit", "100.05", 1),
        ]
        assert {
            (g["key"], g["currency"], g["direction"], g["total"]) for g in summary["groups"]
        } == {
            ("dining", "SGD", "debit", "20.00"),
            ("transfer", "SGD", "debit", "500.00"),
            ("income", "SGD", "credit", "900.00"),
            ("travel", "USD", "debit", "100.05"),
        }
        month = await finance_summary(reader, context, {**window, "group_by": "month"})
        assert {g["key"] for g in month["groups"]} == {spent.strftime("%Y-%m")}
        usd_only = await finance_summary(
            reader, context, {**window, "group_by": "merchant", "currency": "usd"}
        )
        assert [(g["key"], g["direction"], g["total"]) for g in usd_only["groups"]] == [
            ("Airline", "debit", "100.05")
        ]
        assert "Ghost" not in json.dumps(await finance_ledger(reader, context, window))

        obligations = await finance_obligations(reader, context, {"horizon_days": 30})
        assert [(i["kind"], i["payee"], i["amount"]) for i in obligations["items"]] == [
            ("bill", "Late Pending", "7.00"),
            ("bill", "Landlord", "1200.50"),
            ("subscription", "Soon Stream", "9.99"),
        ]
        assert "Late Paid" not in json.dumps(obligations)
        assert "Far Stream" not in json.dumps(obligations)
        assert "Paid Up" not in json.dumps(obligations)
        bill = obligations["items"][1]
        assert bill["next_due"] == _day(10).isoformat() and bill["cancel_by"] is None
        assert bill["source"]["uri"] == f"butlers://finance/bills/{bills[0]}"
        wide = await finance_obligations(reader, context, {"horizon_days": 90})
        assert "Far Stream" in json.dumps(wide)
        assert (
            await _refused(finance_obligations(reader, context, {"horizon_days": 91}))
        ).code == ("invalid_argument")

        # Balance snapshots: a 90-day window lists the two recent ones, exact decimals.
        found = await finance_records(
            reader,
            context,
            {
                "kind": "balance_snapshots",
                "since": _day(-90).isoformat(),
                "until": _day(0).isoformat(),
            },
        )
        assert [(i["fields"]["balance"], i["fields"]["as_of_date"]) for i in found["items"]] == [
            ("1200.20", _day(-10).isoformat()),
            ("1000.10", _day(-30).isoformat()),
        ]
        first = found["items"][0]
        assert (
            first["fields"]["account_id"] == str(account) and first["fields"]["currency"] == "SGD"
        )
        assert first["source"]["uri"] == f"butlers://finance/balance_snapshots/{first['id']}"
        paged = await finance_records(reader, context, {"kind": "balance_snapshots", "limit": 2})
        rest = await finance_records(
            reader,
            context,
            {"kind": "balance_snapshots", "limit": 2, "cursor": paged["next_cursor"]},
        )
        ids = [i["id"] for i in paged["items"] + rest["items"]]
        assert ids == [str(snapshots[1]), str(snapshots[0]), str(snapshots[2])]
        assert rest["next_cursor"] is None

        # Lexical fact search, json_fields, and fact-only filters.
        hits = await finance_records(reader, context, {"kind": "facts", "query": "aisle seats"})
        assert [i["id"] for i in hits["items"]] == [str(facts[0])]
        assert "search_vector" not in hits["items"][0]["fields"]
        assert any(v == "seat-pref" for v in hits["items"][0]["json_fields"].values())
        by_predicate = await finance_records(
            reader, context, {"kind": "facts", "predicate": "budget"}
        )
        assert [i["id"] for i in by_predicate["items"]] == [str(facts[1])]
        tx = await finance_records(
            reader,
            context,
            {
                "kind": "transactions",
                # The manifest's time column for transactions is created_at (row creation).
                "since": _day(-1).isoformat(),
                "until": _day(2).isoformat(),
            },
        )
        assert {i["fields"]["merchant"] for i in tx["items"]} == {
            "Cafe",
            "Bistro",
            "Airline",
            "Own Transfer",
            "Salary",
        }
        account_row = await finance_records(reader, context, {"kind": "accounts"})
        assert account_row["items"][0]["fields"]["last_four"] == "9876"

        # Refusals: outside the manifest, no time column, fact filter on another kind.
        for bad in (
            {"kind": "butler_secrets"},
            {"kind": "payment_destinations", "since": "2026-01-01"},
            {"kind": "bills", "predicate": "budget"},
        ):
            assert (await _refused(finance_records(reader, context, bad))).code == (
                "invalid_argument"
            )
        off = await _context(reader, disabled=frozenset({"finance"}))
        refused = await _refused(finance_summary(reader, off, {**window, "group_by": "month"}))
        assert (refused.code, refused.domain) == ("domain_disabled", "finance")
    finally:
        for table, rows in (
            ("transactions", transactions),
            ("bills", bills),
            ("subscriptions", subscriptions),
            ("balance_snapshots", snapshots),
            ("facts", facts),
        ):
            for row in rows:
                await primary.execute(f"DELETE FROM finance.{table} WHERE id=$1", row)
        await primary.execute("DELETE FROM finance.accounts WHERE id=$1", account)
        _barrier(projected)


async def _health_fact(primary, predicate, content, metadata, *, valid_at=None):
    return await primary.fetchval(
        "INSERT INTO health.facts(subject,predicate,content,metadata,valid_at,sensitivity,scope,"
        "search_vector) VALUES ('owner '||$5::text,$1,$2,$3::jsonb,$4,'confidential','arbitrary',"
        "to_tsvector('english',$2)) RETURNING id",
        predicate,
        content,
        json.dumps(metadata),
        valid_at,
        uuid.uuid4().hex,
    )


async def test_health_series_profile_and_records(projected, primary, reader):
    """REQ-public-mcp-surface-010 and -014 against replicated health rows."""
    facts, calendars, events, briefs = [], [], [], []
    day = _day(-5)
    try:
        for hour, value in ((8, 70.0), (20, 71.0)):
            facts.append(
                await _health_fact(
                    primary,
                    "measurement_weight",
                    "weigh-in",
                    {"value": value, "unit": "kg"},
                    valid_at=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
                )
            )
        facts.append(
            await _health_fact(
                primary,
                "measurement_weight",
                "weigh-in",
                {"value": 69.0, "unit": "kg"},
                valid_at=datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC),
            )
        )
        medication = await _health_fact(
            primary,
            "medication",
            "Metformin 500mg",
            {
                "name": "Metformin",
                "dosage": "500mg",
                "frequency": "daily",
                "schedule": ["08:00", "20:00"],
                "notes": "take with food",
                "active": True,
            },
        )
        stopped = await _health_fact(
            primary, "medication", "Old pill", {"name": "Old pill", "active": False}
        )
        condition = await _health_fact(
            primary,
            "condition",
            "Asthma",
            {"name": "Asthma", "status": "active", "diagnosed_at": "2020-05-01", "notes": "mild"},
        )
        allergy = await _health_fact(
            primary, "allergy", "allergic to penicillin", {"severity": "high"}
        )
        doctor = await _health_fact(
            primary, "doctor_primary_care", "Dr Tan at Central Clinic", {"clinic": "Central"}
        )
        facts += [medication, stopped, condition, allergy, doctor]
        calendar = await primary.fetchval(
            "INSERT INTO health.calendar_sources(source_key,source_kind,metadata) "
            "VALUES ('fh-test','calendar','{}') RETURNING id"
        )
        calendars.append(calendar)
        starts = datetime.now(UTC) + timedelta(days=3)
        events.append(
            await primary.fetchval(
                "INSERT INTO health.calendar_events(source_id,origin_ref,title,description,"
                "location,timezone,starts_at,ends_at,source_butler,metadata) VALUES ($1,"
                "'fh-origin','Dentist','Cleaning','Central Clinic','UTC',$2,$3,'health',"
                '\'{"organizer":"front-desk"}\') RETURNING id',
                calendar,
                starts,
                starts + timedelta(hours=1),
            )
        )
        briefs.append(
            await primary.fetchval(
                "INSERT INTO health.visit_briefs(event_id,event_start,period_start,digest) "
                "VALUES ('fh-brief-1',$1,$1,'{}') RETURNING event_id",
                starts,
            )
        )
        _barrier(projected)
        context = await _context(reader)

        # Series: raw readings, day mean, unknown metric.
        window = {
            "metric": "measurement_weight",
            "start": day.isoformat(),
            "end": (day + timedelta(days=1)).isoformat(),
        }
        raw = await health_series(reader, context, {**window, "bucket": "raw"})
        assert raw["unit"] == "kg" and [p["value"] for p in raw["points"]] == [70.0, 71.0, 69.0]
        daily = await health_series(reader, context, {**window, "bucket": "day"})
        assert daily["points"] == [
            {"t": day.isoformat(), "value": 70.5},
            {"t": (day + timedelta(days=1)).isoformat(), "value": 69.0},
        ]
        weekly = await health_series(reader, context, {**window, "bucket": "week"})
        assert len(weekly["points"]) in (1, 2) and weekly["unit"] == "kg"
        assert weekly["source"]["uri"] == "butlers://health/health_series"
        unknown = await _refused(
            health_series(
                reader, context, {**window, "metric": "measurement_nothing", "bucket": "raw"}
            )
        )
        assert unknown.code == "invalid_argument"

        # Profile.
        profile = await health_profile(reader, context, {})
        meds = {m["name"]: m for m in profile["medications"]}
        assert meds["Metformin"]["status"] == "active" and meds["Old pill"]["status"] == "stopped"
        assert (
            meds["Metformin"]["dose"] == "500mg" and meds["Metformin"]["notes"] == "take with food"
        )
        assert meds["Metformin"]["schedule"] == ["08:00", "20:00"]
        assert (
            meds["Metformin"]["source"]["uri"]
            == f"butlers://health/medications_public/{medication}"
        )
        assert [(c["name"], c["since"], c["notes"]) for c in profile["conditions"]] == [
            ("Asthma", "2020-05-01", "mild")
        ]
        assert [a["text"] for a in profile["allergies"]] == ["allergic to penicillin"]
        assert any(v == "high" for v in profile["allergies"][0]["fields"].values())
        assert profile["allergies"][0]["source"]["uri"] == f"butlers://health/facts/{allergy}"
        assert [p["text"] for p in profile["providers"]] == ["Dr Tan at Central Clinic"]
        narrow = await health_profile(reader, context, {"sections": ["conditions"]})
        assert [c["name"] for c in narrow["conditions"]] == ["Asthma"]
        assert narrow["medications"] == narrow["allergies"] == narrow["providers"] == []

        # Records: appointments, fact filters, kinds outside the manifest.
        visits = await health_records(
            reader,
            context,
            {
                "kind": "calendar_events",
                "since": _day(0).isoformat(),
                "until": _day(10).isoformat(),
            },
        )
        visit = visits["items"][0]["fields"]
        assert (visit["title"], visit["description"], visit["location"]) == (
            "Dentist",
            "Cleaning",
            "Central Clinic",
        )
        assert any(v == "front-desk" for v in visits["items"][0]["json_fields"].values())
        brief = await health_records(reader, context, {"kind": "visit_briefs"})
        assert brief["items"][0]["id"] == "fh-brief-1"
        assert brief["items"][0]["source"]["uri"] == "butlers://health/visit_briefs/fh-brief-1"
        measured = await health_records(
            reader,
            context,
            {
                "kind": "measurements_public",
                "since": day.isoformat(),
                "until": (day + timedelta(days=2)).isoformat(),
            },
        )
        assert len(measured["items"]) == 3
        facts_found = await health_records(
            reader, context, {"kind": "facts", "query": "penicillin", "predicate": "allergy"}
        )
        assert [i["id"] for i in facts_found["items"]] == [str(allergy)]
        refused = await _refused(
            health_records(reader, context, {"kind": "visit_briefs", "predicate": "allergy"})
        )
        assert refused.code == "invalid_argument"
        off = await _context(reader, disabled=frozenset({"health"}))
        disabled = await _refused(health_records(reader, off, {"kind": "meals"}))
        assert (disabled.code, disabled.domain) == ("domain_disabled", "health")
    finally:
        for brief in briefs:
            await primary.execute("DELETE FROM health.visit_briefs WHERE event_id=$1", brief)
        for event in events:
            await primary.execute("DELETE FROM health.calendar_events WHERE id=$1", event)
        for calendar in calendars:
            await primary.execute("DELETE FROM health.calendar_sources WHERE id=$1", calendar)
        for fact in facts:
            await primary.execute("DELETE FROM health.facts WHERE id=$1", fact)
        _barrier(projected)


async def test_finance_and_health_reads_are_pure(projected, primary, reader):
    """REQ-public-mcp-surface-011: the six readers change no row version in the projection."""
    topology, projection = projected
    fact = await _health_fact(primary, "allergy", "purity pollen", {"severity": "low"})
    try:
        _barrier(projected)

        def versions():
            with projection.connect() as conn:
                tables = (
                    conn.execute(
                        text(
                            "SELECT format('%I.%I',schemaname,tablename) FROM pg_tables "
                            "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                            "ORDER BY 1"
                        )
                    )
                    .scalars()
                    .all()
                )
                return {
                    t: conn.execute(
                        text(
                            f"SELECT count(*)||':'||COALESCE(md5(string_agg(xmin::text||ctid::text,"
                            f"',' ORDER BY ctid)),'') FROM {t}"
                        )
                    ).scalar_one()
                    for t in tables
                }

        before = versions()
        assert len(before) > 40
        window = {"start": "2026-01-01", "end": "2026-12-31"}
        for _ in range(3):
            context = await _context(reader)
            await finance_summary(reader, context, {**window, "group_by": "month"})
            await finance_obligations(reader, context, {"horizon_days": 90})
            await finance_records(reader, context, {"kind": "facts", "query": "anything"})
            await finance_records(reader, context, {"kind": "transactions", "limit": 5})
            await health_profile(reader, context, {})
            await health_records(reader, context, {"kind": "facts", "query": "pollen"})
            await health_records(reader, context, {"kind": "measurements_public"})
            metrics = await reader.fetchval(
                "SELECT count(*) FROM mcp_read.health__measurements_public"
            )
            if metrics:
                one = await reader.fetchval(
                    "SELECT fields->>'metric' FROM mcp_read.health__measurements_public LIMIT 1"
                )
                await health_series(reader, context, {**window, "metric": one, "bucket": "day"})
        assert versions() == before
    finally:
        await primary.execute("DELETE FROM health.facts WHERE id=$1", fact)
        _barrier(projected)


async def test_health_series_serves_only_the_newest_unit(projected, primary, reader):
    """A window mixing units serves the newest unit and names the rest (REQ-public-mcp-surface-010)."""
    day = _day(-8)
    facts = []
    try:
        for hour, value, unit in ((6, 70.0, "kg"), (9, 71.0, "kg"), (18, 154.0, "lb")):
            facts.append(
                await _health_fact(
                    primary,
                    "measurement_weight",
                    "mixed",
                    {"value": value, "unit": unit},
                    valid_at=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
                )
            )
        _barrier(projected)
        context = await _context(reader)
        window = {"metric": "measurement_weight", "start": day.isoformat(), "end": day.isoformat()}
        raw = await health_series(reader, context, {**window, "bucket": "raw"})
        assert raw["unit"] == "lb" and [p["value"] for p in raw["points"]] == [154.0]
        assert raw["units_omitted"] == ["kg"]
        daily = await health_series(reader, context, {**window, "bucket": "day"})
        assert daily["points"] == [{"t": day.isoformat(), "value": 154.0}]
    finally:
        for fact in facts:
            await primary.execute("DELETE FROM health.facts WHERE id=$1", fact)
        _barrier(projected)


async def test_health_profile_is_cut_at_the_response_cap(projected, primary, reader):
    """REQ-public-mcp-surface-012: a 200-medication profile fits 256 KiB and says it was cut."""
    await primary.execute(
        "INSERT INTO health.facts(subject,predicate,content,metadata,sensitivity,scope) "
        "SELECT 'cap '||i||' '||$1::text,'medication','cap med '||i,"
        "jsonb_build_object('name','Cap '||lpad(i::text,3,'0'),'notes',repeat('n',1500),"
        "'active',true),'confidential','arbitrary' FROM generate_series(1,200) AS i",
        uuid.uuid4().hex,
    )
    try:
        _barrier(projected)
        context = await _context(reader)
        cut = await health_profile(reader, context, {"sections": ["medications"]})
        assert len(json.dumps(cut, separators=(",", ":")).encode()) <= MAX_RESPONSE_BYTES
        assert cut["truncated"] is True and 0 < len(cut["medications"]) < 200
        ordered = await reader.fetch(
            "SELECT fields->>'name' AS name FROM mcp_read.health__medications_public "
            "WHERE fields->>'name' LIKE 'Cap %' "
            "ORDER BY (fields->>'recorded_at')::timestamptz DESC NULLS LAST, source_id"
        )
        kept = [m["name"] for m in cut["medications"]]
        assert kept == [r["name"] for r in ordered][: len(kept)]
        small = await health_profile(reader, context, {"sections": ["conditions"]})
        assert "truncated" not in small
    finally:
        await primary.execute("DELETE FROM health.facts WHERE subject LIKE 'cap %'")
        _barrier(projected)


async def test_finance_summary_is_cut_at_the_response_cap(projected, primary, reader):
    """Unbounded merchant group keys cannot push finance_summary past 256 KiB."""
    day = _day(-40)
    posted = datetime(day.year, day.month, day.day, 9, tzinfo=UTC)
    await primary.execute(
        "INSERT INTO finance.transactions(posted_at,merchant,amount,currency,direction,category) "
        "SELECT $1,'cap '||lpad(i::text,3,'0')||' '||repeat('m',2000),1,'SGD','debit','shopping' "
        "FROM generate_series(1,300) AS i",
        posted,
    )
    try:
        _barrier(projected)
        context = await _context(reader)
        window = {"start": day.isoformat(), "end": day.isoformat(), "group_by": "merchant"}
        cut = await finance_summary(reader, context, window)
        assert len(json.dumps(cut, separators=(",", ":")).encode()) <= MAX_RESPONSE_BYTES
        assert cut["truncated"] is True and 0 < len(cut["groups"]) < 300
        assert cut["totals"] == [
            {"currency": "SGD", "direction": "debit", "total": "300.00", "count": 300}
        ]
        ordered = await reader.fetch(
            "SELECT fields->>'display_merchant' AS merchant FROM mcp_read.finance__transactions "
            "WHERE fields->>'display_merchant' LIKE 'cap %' GROUP BY 1 ORDER BY 1"
        )
        kept = [g["key"] for g in cut["groups"]]
        assert kept == [r["merchant"] for r in ordered][: len(kept)]
    finally:
        await primary.execute("DELETE FROM finance.transactions WHERE merchant LIKE 'cap %'")
        _barrier(projected)
