"""bu-nz4sn: the probe and reauthorize routes are content-blind.

Two routes in ``routers/secrets_v2.py`` used to bypass the content-blind
bridge and echo persisted credential free text straight back to the caller:

1. ``POST /api/secrets/user/<provider>/reauthorize`` put the stored
   ``entity_info.label`` (the account email) into ``redirect_url`` as
   ``account_hint=<label>``.
2. ``POST /api/secrets/user/<provider>/probe`` and
   ``POST /api/secrets/system/<key>/probe`` returned the persisted failure
   tail (``entity_info.last_test_message``) — or the provider's own response
   text — as ``TestResult.message``.

Owner decision Option C (2026-08-13) forbids both: probe evidence on the wire
is a category drawn from a closed vocabulary, never a persisted or
provider-supplied string.

Every assertion below is an ABSENCE assertion against the raw response bytes,
using a sentinel generated in-test.  The sentinels are obviously synthetic —
no real credential material appears here, and none is ever reproduced in an
assertion message.

Spec anchor
-----------
openspec/specs/dashboard-api/spec.md § Secrets — the content-blind requirement
is stated there for the summary and detail reads; extending it to cover the
probe and reauthorize mutations needs a spec delta, which this bead does not
carry.
"""

from __future__ import annotations

import uuid

import pytest

from butlers.api.routers.secrets_v2 import _system_probe_timestamps

from .test_secrets_v2_mutations import (
    _build_app,
    _make_db,
    _make_entity_info_row,
)
from .test_secrets_v2_system_mutations import _build_app as _build_system_app
from .test_secrets_v2_system_mutations import _make_butler_secrets_row
from .test_secrets_v2_system_mutations import _make_db as _make_system_db

pytestmark = pytest.mark.unit

# Spelled out rather than imported from the router on purpose: this is the
# published contract, so widening ``PROBE_FAILURE_VOCABULARY`` must fail here
# and be re-approved, not ride along silently.
_PUBLISHED_FAILURE_CATEGORIES = frozenset(
    {
        "not_set",
        "expired",
        "rejected",
        "rate_limited",
        "provider_error",
        "malformed",
        "unverified",
        "other",
    }
)


def _sentinel(kind: str) -> str:
    """A synthetic, per-run marker that cannot collide with real payload text."""
    return f"zzsentinel-{kind}-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# reauthorize: the stored label never reaches redirect_url
# ---------------------------------------------------------------------------


def test_reauthorize_redirect_url_contains_no_credential_label():
    """The account hint is an opaque entity reference, never the stored label."""
    label = _sentinel("label")
    row = _make_entity_info_row(info_type="google_oauth_refresh", label=label)
    mock_db = _make_db(user_row=row, oauth_app_configured=True)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")

    assert resp.status_code == 200, resp.status_code
    assert label.encode() not in resp.content, (
        "reauthorize published the persisted credential label in its response"
    )
    assert "account_hint" not in resp.text, (
        "reauthorize must not emit account_hint; the hint is resolved server-side"
    )


def test_reauthorize_redirect_url_still_carries_an_account_reference():
    """Omitting the hint entirely would regress re-auth, so a reference remains.

    ``/oauth/<provider>/start`` uses the hint to recognise a re-authorization
    of an existing Google account; without any hint it treats the dance as a
    brand-new connection and returns 409 once the account limit is reached.
    The reference is the entity UUID the caller already supplied on the
    request, so it discloses nothing the caller did not already hold.
    """
    entity_id = str(uuid.uuid4())
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        entity_id=entity_id,
        label=_sentinel("label"),
    )
    mock_db = _make_db(user_row=row, oauth_app_configured=True)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")

    assert resp.status_code == 200, resp.status_code
    redirect_url = resp.json()["data"]["redirect_url"]
    assert f"account_ref={entity_id}" in redirect_url, redirect_url


# ---------------------------------------------------------------------------
# probe: the persisted failure tail never reaches TestResult.message
# ---------------------------------------------------------------------------


def test_user_probe_message_contains_no_persisted_failure_tail():
    """A failing credential's stored failure tail stays server-side."""
    tail = _sentinel("tail")
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=False,
        last_test_message=tail,
    )
    mock_db = _make_db(user_row=row)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200, resp.status_code
    assert tail.encode() not in resp.content, (
        "probe published the persisted failure tail as TestResult.message"
    )


def test_user_probe_message_is_a_vocabulary_category():
    """What replaces the free text is a member of the closed vocabulary."""
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=False,
        last_test_message=_sentinel("tail"),
    )
    mock_db = _make_db(user_row=row)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200, resp.status_code
    message = resp.json()["data"]["message"]
    assert message in _PUBLISHED_FAILURE_CATEGORIES, message


def test_user_probe_persists_the_real_failure_tail():
    """The diagnostic is not destroyed — only withheld from the wire.

    The audit row and the probe log keep the real text so an operator can
    still tell *why* a credential failed; the projection happens at the
    response boundary, not at the write.
    """
    tail = _sentinel("tail")
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=False,
        last_test_message=tail,
    )
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200, resp.status_code
    # The production probe persists through an acquired transaction connection.
    # The mutation fixture deliberately keeps that connection independent from
    # pool-level spies so rotation tests can prove their lock/update sequence;
    # inspect both supported write boundaries instead of mistaking the fixture
    # split for an absent durable diagnostic.
    write_methods = [shared_pool.execute]
    transaction_connection = getattr(shared_pool, "_transaction_connection", None)
    if transaction_connection is not None:
        write_methods.append(transaction_connection.execute)
    persisted = any(
        tail in [arg for arg in call.args if isinstance(arg, str)]
        for method in write_methods
        for call in method.await_args_list
    )
    assert persisted, "the real failure tail must still be written to probe_log/entity_info"


# ---------------------------------------------------------------------------
# system probe: the format-check diagnostic never reaches the caller
# ---------------------------------------------------------------------------


def test_system_probe_message_does_not_describe_the_stored_value():
    """The OwnTracks format check reports the token's length; that stays local.

    ``_verify_owntracks_token_format`` builds "token length N != 64 …" from the
    stored value.  A length is a value-derived fact, so the response gets the
    ``malformed`` category and the sentence stays in the probe log.
    """
    _system_probe_timestamps.clear()
    key = "owntracks_webhook_token"
    # Obviously synthetic, and deliberately the wrong length for the check.
    row = _make_butler_secrets_row(secret_key=key, secret_value="0" * 12)
    mock_db = _make_system_db(switchboard_row=row)
    client = _build_system_app(mock_db)

    resp = client.post(f"/api/secrets/system/{key}/probe")

    assert resp.status_code == 200, resp.status_code
    body = resp.json()["data"]
    assert body["ok"] is False, body
    assert body["message"] == "malformed", body
    assert "length" not in resp.text, "probe described the stored value's shape"


def test_system_probe_never_set_reports_a_vocabulary_category():
    _system_probe_timestamps.clear()
    key = "OWNER_UNSET_KEY"
    row = _make_butler_secrets_row(secret_key=key, secret_value="", last_test_ok=None)
    mock_db = _make_system_db(switchboard_row=row)
    client = _build_system_app(mock_db)

    resp = client.post(f"/api/secrets/system/{key}/probe")

    assert resp.status_code == 200, resp.status_code
    body = resp.json()["data"]
    assert body["message"] in _PUBLISHED_FAILURE_CATEGORIES | {None}, body


# ---------------------------------------------------------------------------
# probe-all: outcomes produced outside this router are clamped too
# ---------------------------------------------------------------------------


def test_probe_all_clamps_messages_from_other_subsystems(monkeypatch):
    """The CLI family's message is ``cli_auth.test_api_key``'s free-text detail.

    The sweep re-publishes whatever each family's probe returned, so the
    projection has to happen at this endpoint rather than trusting upstream.
    """
    detail = _sentinel("cli-detail")

    import butlers.jobs.secrets_staleness as staleness

    async def _fake_sweep(_db):
        return [
            staleness.ProbeOutcome(
                key="c:cli-auth/codex",
                family="cli",
                label="cli-auth/codex",
                ok=False,
                message=detail,
            )
        ]

    monkeypatch.setattr(staleness, "run_secrets_probe_all", _fake_sweep)
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/probe-all")

    assert resp.status_code == 200, resp.status_code
    assert detail.encode() not in resp.content, (
        "probe-all republished another subsystem's free-text detail"
    )
    assert resp.json()["data"]["results"][0]["message"] == "other", resp.text
