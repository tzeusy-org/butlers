"""Integration tests for GET /api/secrets/inventory.

Covers the four acceptance scenarios from bu-thx5x:
1. Empty store — all three families return empty arrays; meta.needs_hand_count=0.
2. Mixed states — verified-ok, verified-failed, never-verified rows are
   correctly classified and needs_hand_count reflects non-ok rows.
3. Identity filter (projection lens) — ?identity=<uuid> restricts the user
   array to the specified entity; system/cli remain unfiltered.
4. Envelope conformance — response always has {data: {cli,system,user}, meta}.

Additional unit-level tests:
- _fingerprint: sha256[:8] hex, None on empty value
- _derive_state: state machine paths
- _format_probe_time: "HH:MM today" / "yesterday HH:MM" / date fallback
- _needs_hand_count: correct aggregation
- _fetch_probe_logs_bulk: bulk query returns dict keyed by credential_key

Performance assertion note
--------------------------
The p99 < 500ms requirement at 100 creds + 10k probe-log rows is a load-test
concern that depends on real PostgreSQL.  There is no benchmark infra in this
repo, so the requirement is satisfied by design (one bulk probe-log query per
scope — 3 total for system/user/cli — using
ix_secret_probe_log_lookup) and is noted here as a static-check only.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from asyncpg.exceptions import UndefinedTableError
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import (
    _derive_state,
    _fetch_identity_info,
    _fetch_probe_log,
    _fetch_probe_logs_bulk,
    _fingerprint,
    _format_probe_time,
    _get_db_manager,
    _needs_hand_count,
    _row_to_test_result,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)

# Fixed noon-UTC instant used to freeze time in _format_probe_time tests.
# Using noon UTC (12:00) guarantees "today"/"yesterday" transitions never
# land near a calendar-day boundary regardless of when CI runs.
_FROZEN_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)


@contextmanager
def _freeze_time(frozen_now: datetime = _FROZEN_NOW):
    """Freeze ``butlers.api.routers.secrets_v2.datetime.now`` to *frozen_now*.

    Wraps ``datetime`` so all construction/comparison helpers remain intact;
    only ``.now()`` is replaced.  Use this in tests that assert
    ``'today'``/``'yesterday'`` labels from ``_format_probe_time``.
    """
    frozen_dt = MagicMock(wraps=datetime)
    frozen_dt.now = MagicMock(return_value=frozen_now)
    with patch("butlers.api.routers.secrets_v2.datetime", frozen_dt):
        yield frozen_now


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_system_row(
    *,
    key: str = "SOME_API_KEY",
    value: str = "s3cr3t",
    category: str = "general",
    description: str | None = None,
    last_verified: datetime | None = None,
    last_test_ok: bool | None = None,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
    expires_at: datetime | None = None,
) -> MagicMock:
    return _make_row(
        secret_key=key,
        secret_value=value,
        category=category,
        description=description,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        expires_at=expires_at,
        is_sensitive=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_entity_info_row(
    *,
    entity_id: str | None = None,
    info_type: str = "google_oauth_refresh",
    value: str = "tok3n",
    label: str | None = None,
    last_verified: datetime | None = None,
    last_test_ok: bool | None = None,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
) -> MagicMock:
    row_id = uuid4()
    eid = entity_id or str(uuid4())
    return _make_row(
        id=row_id,
        entity_id=eid,
        type=info_type,
        value=value,
        label=label,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        created_at=_NOW,
    )


def _make_db_manager(
    *,
    butler_names: list[str] | None = None,
    system_rows: list[MagicMock] | None = None,
    user_rows: list[MagicMock] | None = None,
    cli_rows: list[MagicMock] | None = None,
    shared_system_rows: list[MagicMock] | None = None,
    probe_row: MagicMock | None = None,
    probe_rows: list[MagicMock] | None = None,
    shared_pool_available: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager for inventory endpoint tests.

    The mock wires:
    - butler_names: list of registered butler names
    - system pool: returns system_rows on fetch('butler_secrets ...')
    - shared pool: returns user_rows on entity_info queries,
                   cli_rows on butler_secrets WHERE category='cli' queries
    - probe_rows: list of probe rows returned by bulk probe-log queries.
                  Each row must have credential_key, ok, code, message, recorded_at.
                  Takes precedence over probe_row when set.
    - probe_row: DEPRECATED legacy single-probe row (now used only for
                 singular fetchrow callers such as per-credential detail
                 endpoints).  For inventory tests, use probe_rows instead.
    """
    butler_names = butler_names or []
    system_rows = system_rows or []
    user_rows = user_rows or []
    cli_rows = cli_rows or []
    shared_system_rows = shared_system_rows or []
    # probe_rows drives the bulk fetch path; fall back to wrapping probe_row
    # in a list so existing tests continue to work.
    if probe_rows is None and probe_row is not None:
        probe_rows = [probe_row]
    bulk_probe_rows: list[MagicMock] = probe_rows or []

    # --- butler schema pool ---
    butler_pool = AsyncMock()

    async def _butler_fetch(sql, *args):
        if "secret_probe_log" in sql:
            # Bulk probe-log query for system credentials.
            return bulk_probe_rows
        if "butler_secrets" in sql and "category = 'cli'" not in sql:
            return system_rows
        return []

    butler_pool.fetch = AsyncMock(side_effect=_butler_fetch)
    # probe log lookup (singular — used by per-credential detail endpoints)
    butler_pool.fetchrow = AsyncMock(return_value=probe_row)

    # --- shared pool ---
    shared_pool = AsyncMock()

    async def _shared_fetch(sql, *args):
        if "secret_probe_log" in sql:
            # Bulk probe-log query for user/cli/shared-system credentials.
            return bulk_probe_rows
        if "category = 'cli'" in sql:
            return cli_rows
        if "entity_info" in sql:
            return user_rows
        if "butler_secrets" in sql:
            # Shared-pool system-secret scan (public.butler_secrets).
            return shared_system_rows
        return []

    shared_pool.fetch = AsyncMock(side_effect=_shared_fetch)
    shared_pool.fetchrow = AsyncMock(return_value=probe_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = butler_names

    def _pool(name):
        return butler_pool

    mock_db.pool = MagicMock(side_effect=_pool)

    if shared_pool_available:
        mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    else:
        mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    """Create a TestClient with the given mock DatabaseManager."""
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


def test_fingerprint_returns_first_8_hex_chars():
    fp = _fingerprint("mysecretvalue")
    assert fp is not None
    assert len(fp) == 8
    # Verify it's hex
    int(fp, 16)


def test_fingerprint_is_deterministic():
    assert _fingerprint("abc") == _fingerprint("abc")


def test_fingerprint_differs_for_different_values():
    assert _fingerprint("abc") != _fingerprint("xyz")


def test_fingerprint_none_on_empty_string():
    assert _fingerprint("") is None


def test_fingerprint_none_on_none():
    assert _fingerprint(None) is None


@pytest.mark.parametrize(
    "is_set,last_test_ok,expires_at,expected",
    [
        (False, None, None, "never_set"),
        (True, None, None, "warn"),
        (True, True, None, "ok"),
        (True, False, None, "failing"),
        (True, True, _NOW - timedelta(days=1), "expired"),
        (True, None, _NOW + timedelta(days=1), "warn"),  # not expired, no probe
    ],
    ids=["never_set", "warn-no-probe", "ok", "failing", "expired", "warn-future-expiry"],
)
def test_derive_state(is_set, last_test_ok, expires_at, expected):
    assert (
        _derive_state(is_set=is_set, last_test_ok=last_test_ok, expires_at=expires_at) == expected
    )


def test_format_probe_time_today():
    # Freeze the formatter's clock to noon UTC so 1h-ago is always "today"
    # regardless of when CI runs.
    with _freeze_time():
        recent = _FROZEN_NOW - timedelta(hours=1)
        result = _format_probe_time(recent)
    assert result is not None
    assert "today" in result


def test_format_probe_time_yesterday():
    # Freeze the formatter's clock to noon UTC so previous-calendar-day stamps
    # reliably produce "yesterday" regardless of when CI runs.
    with _freeze_time():
        yesterday = _FROZEN_NOW - timedelta(days=1, hours=2)
        result = _format_probe_time(yesterday)
    assert result is not None
    assert "yesterday" in result


def test_format_probe_time_yesterday_midnight_boundary():
    """A probe at 23:55 the previous calendar day should be 'yesterday', not 'today'.

    This test guards the calendar-day comparison fix: delta.days-based calculation
    would return 0 (< 24h elapsed) and wrongly say 'today'.

    The formatter's clock is frozen to 00:30 UTC so that 23:55 the previous
    calendar day is always < 24h ago *and* on the previous calendar day — the
    exact edge that exposed the original bug.  Freezing removes wall-clock
    sensitivity so the test is deterministic regardless of when CI runs.
    """
    from datetime import date

    frozen_now = datetime(2024, 6, 15, 0, 30, 0, tzinfo=UTC)  # just after midnight
    prev_day = date(frozen_now.year, frozen_now.month, frozen_now.day) - timedelta(days=1)
    # 23:55 of the previous calendar day — only 35 minutes before frozen_now
    prev_day_late = datetime(prev_day.year, prev_day.month, prev_day.day, 23, 55, tzinfo=UTC)

    with _freeze_time(frozen_now):
        result = _format_probe_time(prev_day_late)

    assert result is not None
    assert "yesterday" in result, (
        f"Expected 'yesterday' for probe at {prev_day_late} "
        f"(frozen_now={frozen_now}), got {result!r}"
    )


def test_format_probe_time_older():
    old = _FROZEN_NOW - timedelta(days=5)
    with _freeze_time():
        result = _format_probe_time(old)
    assert result is not None
    # Should include a date component
    assert len(result) > 5


def test_format_probe_time_none():
    assert _format_probe_time(None) is None


def test_needs_hand_count_all_ok():
    from butlers.api.routers.secrets_v2 import SystemSecret

    items = [
        SystemSecret(key="k1", state="ok", butler="b1"),
        SystemSecret(key="k2", state="ok", butler="b1"),
    ]
    assert _needs_hand_count(items) == 0


def test_needs_hand_count_mixed():
    from butlers.api.routers.secrets_v2 import SystemSecret

    items = [
        SystemSecret(key="k1", state="ok", butler="b1"),
        SystemSecret(key="k2", state="failing", butler="b1"),
        SystemSecret(key="k3", state="warn", butler="b1"),
        SystemSecret(key="k4", state="never_set", butler="b1"),
    ]
    assert _needs_hand_count(items) == 3


# ---------------------------------------------------------------------------
# Scenario 1: Empty store
# ---------------------------------------------------------------------------


def test_inventory_empty_store():
    """All three families return empty arrays; meta.needs_hand_count=0."""
    mock_db = _make_db_manager(butler_names=[], system_rows=[], user_rows=[], cli_rows=[])
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Envelope conformance
    assert "data" in body
    assert "meta" in body

    data = body["data"]
    assert data["cli"] == []
    assert data["system"] == []
    assert data["user"] == []

    meta = body["meta"]
    assert meta["needs_hand_count"] == 0


def test_inventory_no_shared_pool_returns_empty_user_and_cli():
    """When shared pool is unavailable, user and cli are empty but system works."""
    system_row = _make_system_row(key="API_KEY", value="v1", last_test_ok=True)
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[system_row],
        user_rows=[],
        cli_rows=[],
        shared_pool_available=False,
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["user"] == []
    assert body["data"]["cli"] == []
    # System may or may not have rows depending on pool mock
    assert "system" in body["data"]


def test_inventory_surfaces_shared_pool_system_secrets_as_read_only():
    """Shared-pool (public.butler_secrets) system secrets appear in the System
    family flagged read_only=true.

    These are the shared application credentials (Google OAuth app keys, etc.)
    that the consolidated /secrets page surfaces after /settings/owner was
    removed. They must be marked read_only so the passport does not offer the
    generic mutate path (which targets the switchboard schema, not the shared
    pool).
    """
    butler_row = _make_system_row(key="LOCAL_KEY", value="v1", last_test_ok=True)
    shared_row = _make_system_row(
        key="GOOGLE_OAUTH_CLIENT_ID",
        value="client-id-value",
        category="google",
        last_test_ok=True,
    )
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[butler_row],
        shared_system_rows=[shared_row],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    system = {row["key"]: row for row in resp.json()["data"]["system"]}

    # Both the per-butler row and the shared-pool row are present.
    assert "LOCAL_KEY" in system
    assert "GOOGLE_OAUTH_CLIENT_ID" in system

    # Per-butler rows stay editable; shared-pool rows are read-only.
    assert system["LOCAL_KEY"]["read_only"] is False
    assert system["GOOGLE_OAUTH_CLIENT_ID"]["read_only"] is True
    assert system["GOOGLE_OAUTH_CLIENT_ID"]["butler"] == "shared"


def test_inventory_shared_pool_cli_rows_excluded_from_system_family():
    """category='cli' rows in the shared pool are NOT surfaced in the System
    family — CLI runtime tokens have their own family (_fetch_cli_secrets reads
    them separately from the same pool). Including them in both would double-list
    and double-count them in meta.needs_hand_count.
    """
    google_row = _make_system_row(
        key="GOOGLE_OAUTH_CLIENT_ID", value="cid", category="google", last_test_ok=True
    )
    # A category='cli' row lives in the shared pool and is owned by the CLI family.
    cli_row = _make_system_row(key="cli-token", value="tok", category="cli", last_test_ok=True)
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[],
        shared_system_rows=[google_row, cli_row],
        cli_rows=[cli_row],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    system_keys = {row["key"] for row in body["data"]["system"]}

    # Google app key is surfaced; the cli-category row is not in the System family.
    assert "GOOGLE_OAUTH_CLIENT_ID" in system_keys
    assert "cli-token" not in system_keys
    # The CLI token is present exactly once, in the cli family.
    assert any(row["key"] == "cli-token" for row in body["data"]["cli"])


# ---------------------------------------------------------------------------
# Scenario 2: Mixed states
# ---------------------------------------------------------------------------


def test_inventory_mixed_states_needs_hand_count():
    """Mixed states: ok, failing, never-verified rows; needs_hand_count is correct."""
    ok_row = _make_system_row(key="KEY_OK", value="v1", last_test_ok=True)
    fail_row = _make_system_row(key="KEY_FAIL", value="v2", last_test_ok=False)
    # never-verified = is_set, last_test_ok=None → state=warn
    warn_row = _make_system_row(key="KEY_WARN", value="v3", last_test_ok=None)

    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[ok_row, fail_row, warn_row],
        user_rows=[],
        cli_rows=[],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    system = body["data"]["system"]
    assert len(system) == 3

    states = {row["key"]: row["state"] for row in system}
    assert states["KEY_OK"] == "ok"
    assert states["KEY_FAIL"] == "failing"
    assert states["KEY_WARN"] == "warn"

    # needs_hand_count = failing + warn = 2
    assert body["meta"]["needs_hand_count"] == 2


def test_inventory_never_set_credential():
    """A row with empty value gets state=never_set."""
    empty_row = _make_system_row(key="UNSET_KEY", value="", last_test_ok=None)
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[empty_row],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    system = body["data"]["system"]
    assert len(system) == 1
    assert system[0]["state"] == "never_set"
    assert system[0]["fingerprint"] is None


def test_inventory_fingerprints_present_for_set_credentials():
    """Credentials with a value have fingerprint set; never_set rows have fingerprint=null."""
    set_row = _make_system_row(key="SET_KEY", value="mysecret", last_test_ok=True)
    unset_row = _make_system_row(key="UNSET_KEY", value="", last_test_ok=None)
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[set_row, unset_row],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()
    by_key = {row["key"]: row for row in body["data"]["system"]}
    assert by_key["SET_KEY"]["fingerprint"] is not None
    assert len(by_key["SET_KEY"]["fingerprint"]) == 8
    assert by_key["UNSET_KEY"]["fingerprint"] is None


def test_inventory_probe_log_lru_attached():
    """When a probe row exists, test field is populated on credential rows.

    The inventory helpers now issue a single bulk probe-log query per scope
    (DISTINCT ON credential_key ... ANY($2)).  The mock returns a probe row
    with the matching credential_key so the in-memory join can attach it.
    """
    system_row = _make_system_row(key="KEY1", value="v1", last_test_ok=True)
    # Bulk probe rows must include credential_key so _row_to_test_result can
    # build the dict {credential_key: TestResult}.
    probe_row_bulk = _make_row(
        credential_key="KEY1", ok=True, code=200, message=None, recorded_at=_NOW
    )
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[system_row],
        probe_rows=[probe_row_bulk],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()
    system = body["data"]["system"]
    assert len(system) == 1
    test = system[0].get("test")
    # The test field is populated from the bulk probe query result
    assert test is not None
    assert test["ok"] is True
    assert test["code"] == 200


# ---------------------------------------------------------------------------
# Scenario 3: Identity filter (projection-lens)
# ---------------------------------------------------------------------------


def test_inventory_identity_filter_restricts_user_array():
    """?identity=<uuid> restricts the user array to the specified entity."""
    target_entity = str(uuid4())
    other_entity = str(uuid4())

    target_user_row = _make_entity_info_row(
        entity_id=target_entity, info_type="google_oauth_refresh", value="tok"
    )
    other_user_row = _make_entity_info_row(
        entity_id=other_entity, info_type="spotify_refresh", value="tok2"
    )

    # The mock returns only target_user_row when identity filter matches
    shared_pool = AsyncMock()

    async def _shared_fetch(sql, *args):
        if "category = 'cli'" in sql:
            return []
        if "entity_info" in sql and "entity_id = $1" in sql:
            # Identity filter path — return only matching rows
            if args and str(args[0]) == target_entity:
                return [target_user_row]
            return []
        if "entity_info" in sql:
            # Owner path — return all rows
            return [target_user_row, other_user_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_shared_fetch)
    shared_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.pool = MagicMock(side_effect=KeyError("no butler pool"))
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get(f"/api/secrets/inventory?identity={target_entity}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    user = body["data"]["user"]
    assert len(user) == 1
    assert user[0]["entity_id"] == target_entity
    assert user[0]["type"] == "google_oauth_refresh"


def test_inventory_no_identity_uses_owner_default():
    """When ?identity= is omitted, the owner entity is used (projection-lens default)."""
    owner_row = _make_entity_info_row(info_type="google_oauth_refresh", value="tok")

    shared_pool = AsyncMock()

    async def _shared_fetch(sql, *args):
        if "category = 'cli'" in sql:
            return []
        # Owner path: query joins entities with owner role
        if "entity_info" in sql and "owner" in sql:
            return [owner_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_shared_fetch)
    shared_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.pool = MagicMock(side_effect=KeyError)
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    user = body["data"]["user"]
    assert len(user) == 1
    assert user[0]["type"] == "google_oauth_refresh"


# ---------------------------------------------------------------------------
# Scenario 4: Envelope conformance
# ---------------------------------------------------------------------------


def test_inventory_envelope_has_data_and_meta():
    """Response MUST have {data: {cli, system, user}, meta: {needs_hand_count}}."""
    mock_db = _make_db_manager()
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape
    assert set(body.keys()) >= {"data", "meta"}

    # data keys
    assert "cli" in body["data"]
    assert "system" in body["data"]
    assert "user" in body["data"]

    # meta keys
    assert "needs_hand_count" in body["meta"]
    assert isinstance(body["meta"]["needs_hand_count"], int)


def test_inventory_response_does_not_include_raw_values():
    """Raw credential values MUST NOT appear in any field of the response."""
    secret_val = "ultra-secret-value-xyz"
    system_row = _make_system_row(key="SECRET", value=secret_val, last_test_ok=True)

    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[system_row],
    )
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    resp_text = resp.text
    assert secret_val not in resp_text, f"Raw secret value leaked into response: {secret_val!r}"


def test_inventory_no_extra_top_level_fields():
    """No arrays or scalars at the top level — only data and meta."""
    mock_db = _make_db_manager()
    client = _build_app(mock_db)
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()
    top_level_keys = set(body.keys())
    # Only data and meta at the top level (RFC 0007 envelope)
    assert top_level_keys <= {"data", "meta", "error"}


# ---------------------------------------------------------------------------
# Multi-butler system secrets aggregation
# ---------------------------------------------------------------------------


def test_inventory_aggregates_across_butler_schemas():
    """system array includes rows from multiple butler schemas."""
    row_a = _make_system_row(key="A_KEY", value="va", last_test_ok=True)
    row_b = _make_system_row(key="B_KEY", value="vb", last_test_ok=False)

    # Each butler pool returns different rows.  The bulk probe-log query goes
    # through pool.fetch too, so we need a side_effect to route by SQL keyword.
    pool_a = AsyncMock()

    async def _pool_a_fetch(sql, *args):
        if "secret_probe_log" in sql:
            return []  # no probes
        return [row_a]

    pool_a.fetch = AsyncMock(side_effect=_pool_a_fetch)
    pool_a.fetchrow = AsyncMock(return_value=None)

    pool_b = AsyncMock()

    async def _pool_b_fetch(sql, *args):
        if "secret_probe_log" in sql:
            return []  # no probes
        return [row_b]

    pool_b.fetch = AsyncMock(side_effect=_pool_b_fetch)
    pool_b.fetchrow = AsyncMock(return_value=None)

    shared_pool = AsyncMock()
    shared_pool.fetch = AsyncMock(return_value=[])
    shared_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["alpha", "beta"]
    mock_db.pool = MagicMock(side_effect=lambda name: pool_a if name == "alpha" else pool_b)
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()
    system = body["data"]["system"]
    assert len(system) == 2
    keys = {row["key"] for row in system}
    assert keys == {"A_KEY", "B_KEY"}

    # Butler attribution
    butler_map = {row["key"]: row["butler"] for row in system}
    assert butler_map["A_KEY"] == "alpha"
    assert butler_map["B_KEY"] == "beta"


# ---------------------------------------------------------------------------
# Unit-level probe log helper
# ---------------------------------------------------------------------------


async def test_fetch_probe_log_returns_none_on_missing_table():
    """When secret_probe_log doesn't exist, returns None gracefully."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=UndefinedTableError("relation public.secret_probe_log does not exist")
    )
    result = await _fetch_probe_log(pool, "system", "MY_KEY")
    assert result is None


async def test_fetch_probe_log_returns_test_result_when_row_exists():
    """When a probe row exists, returns a TestResult with ok/code/message/at."""
    row = _make_row(ok=True, code=200, message=None, recorded_at=_NOW)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    result = await _fetch_probe_log(pool, "system", "MY_KEY")
    assert result is not None
    assert result.ok is True
    assert result.code == 200
    assert result.message is None
    assert result.at is not None  # "HH:MM today"


async def test_fetch_probe_log_returns_none_when_no_rows():
    """When no probe row exists for the credential, returns None."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    result = await _fetch_probe_log(pool, "user", "google_oauth_refresh")
    assert result is None


# ---------------------------------------------------------------------------
# Unit-level identity enrichment helper
# ---------------------------------------------------------------------------


def _make_entity_row(
    *,
    entity_id: str | None = None,
    canonical_name: str = "Alice",
    roles: list[str] | None = None,
) -> MagicMock:
    from uuid import UUID

    eid = UUID(entity_id) if entity_id else uuid4()
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "id": eid,
            "canonical_name": canonical_name,
            "roles": roles or [],
        }[k]
    )
    return row


async def test_fetch_identity_info_returns_owner_role():
    """Entity with 'owner' in roles gets role='owner'."""
    eid = str(uuid4())
    entity_row = _make_entity_row(entity_id=eid, canonical_name="Alice Owner", roles=["owner"])
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[entity_row])

    result = await _fetch_identity_info(pool, [eid])
    assert len(result) == 1
    assert result[0].entity_id == eid
    assert result[0].name == "Alice Owner"
    assert result[0].role == "owner"


async def test_fetch_identity_info_returns_member_role_for_non_owner():
    """Entity without 'owner' in roles gets role='member'."""
    eid = str(uuid4())
    entity_row = _make_entity_row(entity_id=eid, canonical_name="Bob", roles=["google_account"])
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[entity_row])

    result = await _fetch_identity_info(pool, [eid])
    assert len(result) == 1
    assert result[0].role == "member"
    assert result[0].name == "Bob"


async def test_fetch_identity_info_empty_input():
    """Empty entity_ids list returns empty result without querying the DB."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])

    result = await _fetch_identity_info(pool, [])
    assert result == []
    pool.fetch.assert_not_called()


async def test_fetch_identity_info_graceful_on_missing_table():
    """Silently returns empty list when public.entities does not exist (UndefinedTableError)."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=UndefinedTableError("relation public.entities does not exist")
    )

    result = await _fetch_identity_info(pool, [str(uuid4())])
    assert result == []


async def test_fetch_identity_info_returns_empty_on_transient_error():
    """Returns empty list (with warning) on transient database errors."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=Exception("connection timeout"))

    result = await _fetch_identity_info(pool, [str(uuid4())])
    assert result == []


async def test_fetch_identity_info_preserves_input_order():
    """Result order matches entity_ids input order (owner first)."""
    eid_a = str(uuid4())
    eid_b = str(uuid4())
    row_a = _make_entity_row(entity_id=eid_a, canonical_name="Owner A", roles=["owner"])
    row_b = _make_entity_row(entity_id=eid_b, canonical_name="Member B", roles=[])
    pool = AsyncMock()
    # asyncpg may return rows in any order; we return b before a.
    pool.fetch = AsyncMock(return_value=[row_b, row_a])

    result = await _fetch_identity_info(pool, [eid_a, eid_b])
    assert len(result) == 2
    # Must follow the entity_ids input order, not the fetch order.
    assert result[0].entity_id == eid_a
    assert result[1].entity_id == eid_b


# ---------------------------------------------------------------------------
# Inventory endpoint: identities[] enrichment integration
# ---------------------------------------------------------------------------


def test_inventory_includes_identity_info_with_real_names():
    """GET /api/secrets/inventory returns identities[] with real entity names."""
    eid = str(uuid4())
    user_row = _make_entity_info_row(entity_id=eid, info_type="google_oauth_refresh", value="tok")

    entity_row = _make_entity_row(entity_id=eid, canonical_name="Alice Owner", roles=["owner"])

    shared_pool = AsyncMock()

    async def _shared_fetch(sql, *args):
        if "category = 'cli'" in sql:
            return []
        if "entity_info" in sql:
            return [user_row]
        if "public.entities" in sql:
            return [entity_row]
        return []

    shared_pool.fetch = AsyncMock(side_effect=_shared_fetch)
    shared_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.pool = MagicMock(side_effect=KeyError)
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    identities = body["data"]["identities"]
    assert len(identities) == 1
    assert identities[0]["entity_id"] == eid
    assert identities[0]["name"] == "Alice Owner"
    assert identities[0]["role"] == "owner"


def test_inventory_identities_empty_when_no_user_secrets():
    """identities[] is empty when there are no user secrets."""
    mock_db = _make_db_manager(butler_names=[], user_rows=[], cli_rows=[])
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["identities"] == []


# ---------------------------------------------------------------------------
# Provider catalog in inventory response [bu-ej5dr]
# ---------------------------------------------------------------------------


def test_inventory_includes_providers_field():
    """GET /api/secrets/inventory response.data includes a non-empty providers dict."""
    mock_db = _make_db_manager()
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    body = resp.json()

    assert "providers" in body["data"], "data.providers must be present in inventory response"
    providers = body["data"]["providers"]
    assert isinstance(providers, dict)
    assert len(providers) > 0, "providers catalog must be non-empty"


def test_inventory_providers_contains_expected_keys():
    """providers catalog contains at least the canonical provider slugs."""
    from butlers.secrets_provider_catalog import PROVIDER_CATALOG

    mock_db = _make_db_manager()
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    providers = resp.json()["data"]["providers"]

    # Every key in the Python catalog must appear in the response.
    missing = set(PROVIDER_CATALOG.keys()) - set(providers.keys())
    assert not missing, f"providers catalog missing keys: {missing}"


def test_inventory_provider_entry_shape():
    """Each provider entry has the required display fields."""
    mock_db = _make_db_manager()
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    providers = resp.json()["data"]["providers"]

    required_fields = {"id", "label", "glyph", "kind", "authority", "brief", "cadence"}
    for slug, entry in providers.items():
        missing = required_fields - set(entry.keys())
        assert not missing, f"provider '{slug}' missing fields: {missing}"
        # id must match the dict key
        assert entry["id"] == slug, f"provider '{slug}' has id={entry['id']!r} (mismatch)"
        # kind must be a known value
        assert entry["kind"] in {"oauth", "token", "apikey", "webhook"}, (
            f"provider '{slug}' has unexpected kind={entry['kind']!r}"
        )


def test_inventory_providers_is_additive():
    """Adding providers does not remove any previously existing data.data fields."""
    mock_db = _make_db_manager()
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    data = resp.json()["data"]

    # All previously existing fields must still be present.
    for field in ("cli", "system", "user", "identities"):
        assert field in data, f"existing field {field!r} missing from inventory response"


# ---------------------------------------------------------------------------
# Unit tests: _row_to_test_result (bu-5vb5m)
# ---------------------------------------------------------------------------


def test_row_to_test_result_maps_fields_correctly():
    """_row_to_test_result maps ok/code/message/recorded_at to TestResult."""
    # Freeze the formatter's clock so "now" and recorded_at are on the same
    # calendar day regardless of when CI runs.
    row = _make_row(ok=True, code=200, message="all good", recorded_at=_FROZEN_NOW)
    with _freeze_time():
        result = _row_to_test_result(row)
    assert result.ok is True
    assert result.code == 200
    assert result.message == "all good"
    assert result.at is not None
    assert "today" in result.at  # recent timestamp → "HH:MM today"


def test_row_to_test_result_none_message():
    """_row_to_test_result handles None message without error."""
    _now = datetime.now(tz=UTC)
    row = _make_row(ok=True, code=200, message=None, recorded_at=_now)
    result = _row_to_test_result(row)
    assert result.message is None


# ---------------------------------------------------------------------------
# Unit tests: _fetch_probe_logs_bulk (bu-5vb5m)
# ---------------------------------------------------------------------------


async def test_fetch_probe_logs_bulk_returns_dict_for_all_keys():
    """Bulk query returns a dict with one TestResult entry per key that has a probe."""
    _now = datetime.now(tz=UTC)
    row_a = _make_row(credential_key="KEY_A", ok=True, code=200, message=None, recorded_at=_now)
    row_b = _make_row(credential_key="KEY_B", ok=False, code=500, message="fail", recorded_at=_now)

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[row_a, row_b])

    result = await _fetch_probe_logs_bulk(pool, "system", ["KEY_A", "KEY_B"])

    assert set(result.keys()) == {"KEY_A", "KEY_B"}
    assert result["KEY_A"].ok is True
    assert result["KEY_A"].code == 200
    assert result["KEY_B"].ok is False
    assert result["KEY_B"].code == 500
    assert result["KEY_B"].message == "fail"


async def test_fetch_probe_logs_bulk_omits_keys_with_no_probe():
    """Keys with no probe row are absent from the result dict (caller treats as None)."""
    _now = datetime.now(tz=UTC)
    row_a = _make_row(credential_key="KEY_A", ok=True, code=200, message=None, recorded_at=_now)

    pool = AsyncMock()
    # DB returns only KEY_A (KEY_B has no probe row)
    pool.fetch = AsyncMock(return_value=[row_a])

    result = await _fetch_probe_logs_bulk(pool, "system", ["KEY_A", "KEY_B"])

    assert "KEY_A" in result
    assert "KEY_B" not in result
    assert result["KEY_A"].ok is True


async def test_fetch_probe_logs_bulk_returns_empty_dict_for_empty_keys():
    """Empty keys list returns empty dict without issuing a DB query."""
    pool = AsyncMock()
    pool.fetch = AsyncMock()

    result = await _fetch_probe_logs_bulk(pool, "system", [])

    assert result == {}
    pool.fetch.assert_not_called()


async def test_fetch_probe_logs_bulk_returns_empty_dict_on_missing_table():
    """Silently returns empty dict when secret_probe_log does not exist."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=UndefinedTableError("relation public.secret_probe_log does not exist")
    )

    result = await _fetch_probe_logs_bulk(pool, "system", ["KEY_A"])

    assert result == {}


async def test_fetch_probe_logs_bulk_issues_single_query():
    """Bulk variant calls pool.fetch exactly once regardless of the number of keys."""
    _now = datetime.now(tz=UTC)
    keys = [f"KEY_{i}" for i in range(10)]
    rows = [
        _make_row(credential_key=k, ok=True, code=200, message=None, recorded_at=_now) for k in keys
    ]

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)

    result = await _fetch_probe_logs_bulk(pool, "system", keys)

    # Exactly one DB call — not 10 (one per key).
    assert pool.fetch.call_count == 1
    assert len(result) == 10


# ---------------------------------------------------------------------------
# Regression: inventory endpoint test results match per-row behavior
# ---------------------------------------------------------------------------


def test_inventory_probe_results_match_per_row_expectations():
    """Inventory credential rows carry the correct probe result from the bulk query.

    Regression guard: after the N+1 → bulk refactor, the data shape returned
    for each credential must match what a per-row fetchrow would have produced.
    """
    _now = datetime.now(tz=UTC)
    system_row_ok = _make_system_row(key="API_KEY", value="v1", last_test_ok=True)
    system_row_fail = _make_system_row(key="FAIL_KEY", value="v2", last_test_ok=False)

    # Bulk probe rows keyed by credential_key
    probe_row_ok = _make_row(
        credential_key="API_KEY", ok=True, code=200, message=None, recorded_at=_now
    )
    probe_row_fail = _make_row(
        credential_key="FAIL_KEY", ok=False, code=401, message="auth error", recorded_at=_now
    )

    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[system_row_ok, system_row_fail],
        probe_rows=[probe_row_ok, probe_row_fail],
    )
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    system = resp.json()["data"]["system"]
    assert len(system) == 2

    by_key = {row["key"]: row for row in system}

    # API_KEY probe attached correctly
    assert by_key["API_KEY"]["test"] is not None
    assert by_key["API_KEY"]["test"]["ok"] is True
    assert by_key["API_KEY"]["test"]["code"] == 200

    # FAIL_KEY probe attached correctly
    assert by_key["FAIL_KEY"]["test"] is not None
    assert by_key["FAIL_KEY"]["test"]["ok"] is False
    assert by_key["FAIL_KEY"]["test"]["code"] == 401
    assert by_key["FAIL_KEY"]["test"]["message"] == "auth error"


def test_inventory_credential_with_no_probe_has_null_test():
    """Credentials with no probe row in the bulk result have test=null in the response."""
    system_row = _make_system_row(key="NO_PROBE_KEY", value="v1", last_test_ok=None)

    # No probe_rows — bulk query returns empty list
    mock_db = _make_db_manager(
        butler_names=["switchboard"],
        system_rows=[system_row],
        probe_rows=[],  # no probes
    )
    client = _build_app(mock_db)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200
    system = resp.json()["data"]["system"]
    assert len(system) == 1
    assert system[0]["test"] is None


# ---------------------------------------------------------------------------
# bu-2kejb: Owner-default inventory surfaces primary Google account
# ---------------------------------------------------------------------------
#
# These tests cover the four acceptance scenarios from the spec:
#   (a) Owner-default INCLUDES the primary account's google_oauth_refresh
#   (b) Owner-default EXCLUDES non-primary account credentials
#   (c) Existing owner creds (telegram/home_assistant) still included
#   (d) Expired primary (status='expired') still surfaces its credential
#
# The new owner-default SQL uses UNION ALL to merge:
#   1. Owner entity credentials (anchored on the entity with 'owner' in roles)
#   2. Primary Google account companion entity credentials
#      (anchored on the entity pointed to by google_accounts WHERE
#       is_primary=true AND status != 'revoked')
#
# The mock distinguishes the owner-default UNION query (contains
# 'google_accounts') from the identity-specific query (contains 'entity_id = $1').
# Both paths contain 'entity_info', so the mock checks for 'google_accounts'
# to route the owner-default path.
# ---------------------------------------------------------------------------


def _make_google_inventory_pool(
    *,
    primary_rows: list[MagicMock],
    owner_rows: list[MagicMock],
    identity_rows: dict[str, list[MagicMock]] | None = None,
    entity_rows: list[MagicMock] | None = None,
) -> AsyncMock:
    """Build a shared pool mock for google account inventory tests.

    Routes:
    - SQL containing 'google_accounts' → UNION ALL result: owner_rows + primary_rows
    - SQL containing 'entity_id = $1' (identity-specific) → identity_rows[str(arg)] or []
    - SQL containing 'public.entities' (identity enrichment) → entity_rows or []
    - SQL containing "category = 'cli'" → []
    - SQL containing 'secret_probe_log' → []
    """
    identity_rows = identity_rows or {}
    entity_rows = entity_rows or []

    shared_pool = AsyncMock()

    async def _fetch(sql, *args):
        if "secret_probe_log" in sql:
            return []
        if "category = 'cli'" in sql:
            return []
        if "entity_id = $1" in sql and args:
            # Identity-specific path: return rows for the requested entity.
            return identity_rows.get(str(args[0]), [])
        if "google_accounts" in sql:
            # Owner-default UNION ALL path.
            return list(owner_rows) + list(primary_rows)
        if "entity_info" in sql:
            # Fallback: owner entity only (old-style path — should not be reached
            # by the new UNION query, but kept as safety net).
            return list(owner_rows)
        if "public.entities" in sql:
            return entity_rows
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)
    shared_pool.fetchrow = AsyncMock(return_value=None)
    return shared_pool


def _build_app_with_shared_pool(shared_pool: AsyncMock) -> TestClient:
    """Create a TestClient wired to a custom shared pool (no butler schemas)."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.pool = MagicMock(side_effect=KeyError("no butler pool"))
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


# --- (a) Primary active google account appears in owner-default inventory ---


def test_primary_google_account_surfaces_in_owner_default():
    """Owner-default inventory INCLUDES the primary active Google account credential.

    Spec: §Owner-Default Inventory Surfaces Primary Google Account
    When a primary active Google account exists, google_oauth_refresh MUST appear
    in the user array without needing ?identity=.
    """
    primary_entity_id = str(uuid4())
    primary_row = _make_entity_info_row(
        entity_id=primary_entity_id,
        info_type="google_oauth_refresh",
        value="primary-refresh-token",
    )

    shared_pool = _make_google_inventory_pool(
        primary_rows=[primary_row],
        owner_rows=[],
    )
    client = _build_app_with_shared_pool(shared_pool)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["user"]

    types = [u["type"] for u in user]
    assert "google_oauth_refresh" in types, (
        "Owner-default inventory must include google_oauth_refresh for the primary account"
    )

    google_entry = next(u for u in user if u["type"] == "google_oauth_refresh")
    assert google_entry["entity_id"] == primary_entity_id


# --- (b) Non-primary account EXCLUDED from owner-default ---


def test_non_primary_google_account_excluded_from_owner_default():
    """Owner-default inventory EXCLUDES non-primary Google account credentials.

    Spec: §Multi-Account Leak Prevention (dashboard-google-accounts) and
          §Only the primary account appears in owner-default — non-primary excluded

    This is the core security invariant: a non-primary account (e.g. tzeuse@)
    MUST NOT appear in the owner-default view.
    """
    primary_entity_id = str(uuid4())
    non_primary_entity_id = str(uuid4())

    primary_row = _make_entity_info_row(
        entity_id=primary_entity_id,
        info_type="google_oauth_refresh",
        value="primary-token",
    )
    non_primary_row = _make_entity_info_row(
        entity_id=non_primary_entity_id,
        info_type="google_oauth_refresh",
        value="non-primary-token",
    )

    # The mock simulates the DB enforcing is_primary=true: only primary_row
    # comes back in the google_accounts UNION path.  The non_primary_row is
    # available under an explicit identity= lens only.
    shared_pool = _make_google_inventory_pool(
        primary_rows=[primary_row],
        owner_rows=[],
        identity_rows={non_primary_entity_id: [non_primary_row]},
    )
    client = _build_app_with_shared_pool(shared_pool)

    # Owner-default: must NOT contain the non-primary entity.
    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["user"]

    entity_ids = [u["entity_id"] for u in user]
    assert non_primary_entity_id not in entity_ids, (
        f"Non-primary Google account entity {non_primary_entity_id} "
        "MUST NOT appear in the owner-default inventory (security leak)"
    )

    # Exactly one google_oauth_refresh, belonging to the primary account.
    google_entries = [u for u in user if u["type"] == "google_oauth_refresh"]
    assert len(google_entries) == 1
    assert google_entries[0]["entity_id"] == primary_entity_id

    # Non-primary IS accessible under explicit identity= lens.
    resp2 = client.get(f"/api/secrets/inventory?identity={non_primary_entity_id}")
    assert resp2.status_code == 200, resp2.text
    user2 = resp2.json()["data"]["user"]
    entity_ids2 = [u["entity_id"] for u in user2]
    assert non_primary_entity_id in entity_ids2


# --- (c) Existing owner creds still included alongside primary google account ---


def test_owner_default_includes_existing_creds_alongside_primary_google():
    """Owner-default includes telegram/home_assistant alongside google_oauth_refresh.

    Spec: §Owner-Default Inventory Surfaces Primary Google Account
    The UNION extension MUST NOT drop existing owner credentials.
    """
    owner_entity_id = str(uuid4())
    google_entity_id = str(uuid4())

    telegram_row = _make_entity_info_row(
        entity_id=owner_entity_id,
        info_type="telegram_token",
        value="tg-token",
    )
    ha_row = _make_entity_info_row(
        entity_id=owner_entity_id,
        info_type="home_assistant_token",
        value="ha-token",
    )
    google_row = _make_entity_info_row(
        entity_id=google_entity_id,
        info_type="google_oauth_refresh",
        value="google-token",
    )

    shared_pool = _make_google_inventory_pool(
        primary_rows=[google_row],
        owner_rows=[telegram_row, ha_row],
    )
    client = _build_app_with_shared_pool(shared_pool)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["user"]

    types = {u["type"] for u in user}
    assert "telegram_token" in types, "telegram_token must still appear in owner-default"
    assert "home_assistant_token" in types, (
        "home_assistant_token must still appear in owner-default"
    )
    assert "google_oauth_refresh" in types, "google_oauth_refresh must appear for primary account"
    assert len(user) == 3


# --- (d) Expired primary still surfaces (status='expired' is not 'revoked') ---


def test_expired_primary_google_account_still_surfaces_in_owner_default():
    """Expired primary Google account (status='expired') still appears in owner-default.

    Spec: §Owner-Default Inventory Surfaces Primary Google Account
    The filter is status != 'revoked', which includes 'expired' accounts.
    An expired primary must surface so the owner can reach the reauth CTA.
    The DB enforces the filter; the mock simulates it by including the expired
    primary row in the google_accounts UNION path result.
    """
    primary_entity_id = str(uuid4())
    expired_primary_row = _make_entity_info_row(
        entity_id=primary_entity_id,
        info_type="google_oauth_refresh",
        value="expired-token",
        last_test_ok=False,  # expired token likely fails probe
    )

    shared_pool = _make_google_inventory_pool(
        primary_rows=[expired_primary_row],
        owner_rows=[],
    )
    client = _build_app_with_shared_pool(shared_pool)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["user"]

    types = [u["type"] for u in user]
    assert "google_oauth_refresh" in types, (
        "Expired primary account must still surface google_oauth_refresh "
        "so the owner can reach the reauth CTA (status='expired' != 'revoked')"
    )

    google_entry = next(u for u in user if u["type"] == "google_oauth_refresh")
    assert google_entry["entity_id"] == primary_entity_id


# --- Primary google account entity appears in identities[] switcher ---


def test_primary_google_account_entity_appears_in_identities():
    """Primary Google account entity_id appears in identities[] in the inventory response.

    Spec: §Projection-Lens Identity Switcher (butler-secrets)
    The identity switcher chip SHALL include connected Google accounts as
    selectable identity lenses.  The backend surfaces the primary account's
    companion entity through the UNION, so its entity_id flows into seen_eids
    and is picked up by _fetch_identity_info.
    """
    primary_entity_id = str(uuid4())
    primary_row = _make_entity_info_row(
        entity_id=primary_entity_id,
        info_type="google_oauth_refresh",
        value="google-token",
    )

    # _fetch_identity_info queries public.entities for canonical_name+roles.
    google_entity_row = _make_entity_row(
        entity_id=primary_entity_id,
        canonical_name="google-account:uniquosity@gmail.com",
        roles=["google_account"],
    )

    shared_pool = _make_google_inventory_pool(
        primary_rows=[primary_row],
        owner_rows=[],
        entity_rows=[google_entity_row],
    )
    client = _build_app_with_shared_pool(shared_pool)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    identities = body["data"]["identities"]
    entity_ids = [i["entity_id"] for i in identities]
    assert primary_entity_id in entity_ids, (
        "Primary Google account companion entity must appear in identities[] "
        "so the FE can render the identity switcher chip"
    )

    google_identity = next(i for i in identities if i["entity_id"] == primary_entity_id)
    # google_account role is not 'owner', so it maps to 'member' in the switcher
    assert google_identity["role"] == "member"


# --- No google account connected — google_oauth_refresh absent from owner-default ---


def test_no_primary_google_account_no_google_entry_in_owner_default():
    """When no primary Google account exists, google_oauth_refresh is absent.

    Spec: §No Google account connected — no google_oauth_refresh in owner-default
    The UNION path for google_accounts returns empty when there is no primary
    account (DB enforces is_primary=true AND status!='revoked').
    """
    owner_entity_id = str(uuid4())
    telegram_row = _make_entity_info_row(
        entity_id=owner_entity_id,
        info_type="telegram_token",
        value="tg-token",
    )

    shared_pool = _make_google_inventory_pool(
        primary_rows=[],  # no primary google account
        owner_rows=[telegram_row],
    )
    client = _build_app_with_shared_pool(shared_pool)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["user"]

    types = [u["type"] for u in user]
    assert "google_oauth_refresh" not in types, (
        "google_oauth_refresh must NOT appear when no primary Google account is connected"
    )
    assert "telegram_token" in types


# --- Owner identity always appears first in identities[] regardless of type sort order ---


def test_owner_entity_first_in_identities_when_google_type_sorts_before_owner_type():
    """Owner entity_id appears first in identities[] even when google_oauth_refresh sorts before.

    Regression guard for the ordering bug: prior to the priority-column fix, ORDER BY type
    caused google_oauth_refresh (g) to sort before telegram_token (t), making the Google
    account entity_id appear first in seen_eids and thus first in identities[].

    With the priority-column fix (ORDER BY priority, type), owner credentials (priority=0)
    always appear before google account credentials (priority=1), preserving the owner-first
    contract documented in _fetch_identity_info.

    The mock simulates the DB returning rows already sorted by priority, type (as the real
    DB would), with owner rows (telegram_token) coming before google rows (google_oauth_refresh).
    """
    owner_entity_id = str(uuid4())
    google_entity_id = str(uuid4())

    telegram_row = _make_entity_info_row(
        entity_id=owner_entity_id,
        info_type="telegram_token",
        value="tg-token",
    )
    google_row = _make_entity_info_row(
        entity_id=google_entity_id,
        info_type="google_oauth_refresh",
        value="google-token",
    )

    owner_entity_row = _make_entity_row(
        entity_id=owner_entity_id,
        canonical_name="Owner",
        roles=["owner"],
    )
    google_entity_row = _make_entity_row(
        entity_id=google_entity_id,
        canonical_name="google-account:uniquosity@gmail.com",
        roles=["google_account"],
    )

    # Mock returns owner rows first (priority=0), google rows second (priority=1),
    # simulating the DB's ORDER BY priority, type guarantee.
    shared_pool = _make_google_inventory_pool(
        primary_rows=[google_row],
        owner_rows=[telegram_row],
        entity_rows=[owner_entity_row, google_entity_row],
    )
    client = _build_app_with_shared_pool(shared_pool)

    resp = client.get("/api/secrets/inventory")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    identities = body["data"]["identities"]
    assert len(identities) >= 2, "Both owner and google identities must appear"

    # Owner MUST be first.
    assert identities[0]["entity_id"] == owner_entity_id, (
        "Owner entity must appear first in identities[] — "
        "google_oauth_refresh must not displace it via alphabetical type sort"
    )
    assert identities[0]["role"] == "owner"

    # Google account appears second.
    google_identity = next((i for i in identities if i["entity_id"] == google_entity_id), None)
    assert google_identity is not None, "Google account entity must appear in identities[]"
    assert google_identity["role"] == "member"
