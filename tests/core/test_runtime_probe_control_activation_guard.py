"""The gate between a mounted signer and an image allowed to use it.

Covers REQ-core-credentials-002 acceptance criteria 1, 3, and 4, and with them
the cutover REQ-dashboard-model-settings-001 asks the Models tab to make.

Criterion 1 asks for a *same-image pre-mount check*: before the production
signer mount becomes active, prove the deferred model-settings adapter
callsites are gone.  A source-scanning test alone cannot do that, because the
thing being prevented is a deployed image in which both a private signer and a
dashboard-local runtime probe exist at once --- a revert, a bad merge, or a
well-meaning fallback puts them back together long after the source test that
approved the cutover was written.  So the check runs *in the signing path*:
:func:`activated_signer_snapshot` refuses to hand out a signer while a local
probe symbol exists on a guarded module, and every caller reaches the signer
only through it.

That makes the guard testable in the one way that matters --- put a probe
symbol back and watch the signer go unavailable --- which is what the
non-vacuity tests below do.  They are the reason this file exists rather than a
grep.

Criterion 3's rotation half lands here too, because rotation is the other case
where "the signer is mounted" and "the signer may sign" come apart: during the
overlap the old key is still issuable through the keyring's retiring entry, and
after the cutover instant it is not, whatever the mount says.

Every key in this file is synthetic, generated inside the test, and never
logged, rendered, or asserted on.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from starlette.applications import Starlette

import butlers.core.runtime_probe_control.endpoint as endpoint
from butlers.core.runtime_probe_control import activation
from butlers.core.runtime_probe_control.client import RuntimeProbeControlClient
from butlers.core.runtime_probe_control.coordinator import ProbeStatus
from butlers.core.runtime_probe_control.keys import (
    SignerSnapshot,
    VerifierSnapshot,
    match_signer_to_keyring,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    retiring_entry,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_ENTRY_ID = UUID("a1b2c3d4-5566-4788-99aa-bbccddeeff02")
_CURRENT_KID = "probe-test-current"
_RETIRING_KID = "probe-test-retiring"


def _encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


# ---------------------------------------------------------------------------
# Criterion 4: the deferred allowlist is empty
# ---------------------------------------------------------------------------


def test_the_deferred_local_probe_allowlist_is_empty() -> None:
    """The exception task 3.6b was allowed to leave behind is spent.

    Written down as an empty collection rather than a deleted constant: a
    module put back on it has to be added here, where a reviewer sees it,
    instead of appearing as an absence nobody diffed.
    """
    assert activation.DEFERRED_LOCAL_PROBE_MODULES == frozenset()


def test_no_guarded_module_still_holds_a_local_probe_symbol() -> None:
    """Criterion 4, asserted through the same function the signing path calls."""
    assert activation.local_model_probe_callsites() == ()


def test_the_guard_actually_reaches_every_module_it_claims_to_check() -> None:
    """Guard the guard: an unimportable name would empty it silently.

    ``local_model_probe_callsites`` imports each guarded module, so a rename or
    a deletion raises here rather than reporting a clean image.
    """
    import importlib

    assert activation.GUARDED_MODULES
    for module_name in activation.GUARDED_MODULES:
        assert importlib.import_module(module_name) is not None


def test_model_settings_is_guarded_so_the_empty_result_is_not_vacuous() -> None:
    """The module that held the deferred exception is the one being watched."""
    assert "butlers.api.routers.model_settings" in activation.GUARDED_MODULES


# ---------------------------------------------------------------------------
# Criterion 1: the pre-mount check is enforced, not advisory
# ---------------------------------------------------------------------------


def _mounted_snapshot() -> tuple[SignerSnapshot, Any]:
    """A fully valid signer/keyring pair, as a provisioned deployment would load."""
    seed, public_key = synthetic_keypair()
    sign_from = datetime.now(UTC) - timedelta(days=1)
    signer_key = parse_signer_document(
        _encode(signer_document(seed, kid=_CURRENT_KID, sign_from=sign_from))
    )
    keyring = parse_verifier_keyring_document(
        _encode(keyring_document(current_entry(public_key, kid=_CURRENT_KID, sign_from=sign_from)))
    )
    return (
        SignerSnapshot(
            signer=signer_key,
            keyring=keyring,
            matched=match_signer_to_keyring(signer_key, keyring),
        ),
        keyring,
    )


@pytest.fixture
def reintroduced_local_probe(monkeypatch: pytest.MonkeyPatch):
    """Put one dashboard-local probe symbol back on the module that lost it.

    This is the revert this guard exists for, staged as narrowly as possible:
    one attribute, on one guarded module, removed again when the test ends.
    """
    from butlers.api.routers import model_settings

    monkeypatch.setattr(model_settings, "get_adapter", object(), raising=False)
    return "butlers.api.routers.model_settings.get_adapter"


def test_a_reintroduced_probe_callsite_is_detected(reintroduced_local_probe: str) -> None:
    """Non-vacuity for the empty-result test above: it can report non-empty."""
    assert activation.local_model_probe_callsites() == (reintroduced_local_probe,)


def test_a_clean_image_hands_out_the_mounted_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The healthy half of the pair below: nothing blocks a clean image.

    Without this, the blocked test could pass because the snapshot was never
    available in the first place.
    """
    mounted, _ = _mounted_snapshot()
    monkeypatch.setattr(activation, "signer_snapshot", lambda: mounted)

    assert activation.activated_signer_snapshot().signer is not None


def test_a_reintroduced_probe_callsite_makes_the_signer_unavailable(
    reintroduced_local_probe: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1: the mount can be present and correct; the image still may not sign.

    The signer here is a complete, valid, matched snapshot --- exactly what a
    provisioned deployment loads --- and the probe symbol is genuinely back on
    the module rather than faked at the guard, so the only thing standing
    between the two is the guard itself.
    """
    mounted, _ = _mounted_snapshot()
    monkeypatch.setattr(activation, "signer_snapshot", lambda: mounted)

    blocked = activation.activated_signer_snapshot()

    assert reintroduced_local_probe in activation.local_model_probe_callsites()
    assert blocked.signer is None
    assert blocked.may_issue_at(datetime.now(UTC)) is False
    assert blocked.unavailable_reason == activation.LOCAL_PROBE_PRESENT_REASON


async def test_a_blocked_image_signs_nothing_and_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard's effect at the wire, not just on a dataclass field."""
    mounted, _ = _mounted_snapshot()
    monkeypatch.setattr(activation, "signer_snapshot", lambda: mounted)
    monkeypatch.setattr(activation, "local_model_probe_callsites", lambda: ("m.get_adapter",))
    reached = False

    async def _unreachable(request: httpx.Request) -> httpx.Response:
        nonlocal reached
        reached = True
        return httpx.Response(200, json={"status": "completed", "ok": True})

    client = RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=activation.activated_signer_snapshot,
        transport=httpx.MockTransport(_unreachable),
    )

    result = await client.probe(_ENTRY_ID)

    assert result.status is ProbeStatus.UNAVAILABLE
    assert reached is False


def test_the_blocked_diagnostic_names_only_our_own_modules(
    reintroduced_local_probe: str, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """A log line on the key plane may carry no provider or key-plane value.

    Asserted as absence: the reason is a fixed sentence and the detail is a
    dotted module path this repository owns, so there is nothing here that
    could have come from a document, a request, or a provider.
    """
    mounted, _ = _mounted_snapshot()
    monkeypatch.setattr(activation, "signer_snapshot", lambda: mounted)

    with caplog.at_level(logging.ERROR, logger=activation.__name__):
        activation.activated_signer_snapshot()

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert activation.LOCAL_PROBE_PRESENT_REASON in rendered
    assert reintroduced_local_probe in rendered
    assert _CURRENT_KID not in rendered
    assert "private_key_b64u" not in rendered
    assert "Bearer" not in rendered


# ---------------------------------------------------------------------------
# Criterion 4: one client per caller class, and only registered ones
# ---------------------------------------------------------------------------


def test_each_caller_class_gets_one_client_for_the_life_of_the_process() -> None:
    """The readiness latch is per-client, so a per-request client never latches."""
    activation._reset_clients_for_tests()
    try:
        first = activation.probe_client("dashboard")
        second = activation.probe_client("dashboard")
        scheduler = activation.probe_client("scheduler")

        assert first is second
        assert scheduler is not first
    finally:
        activation._reset_clients_for_tests()


def test_the_base_url_follows_the_dashboard_to_butler_convention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose sets ``BUTLERS_HOST`` on Dashboard and leaves it unset inside all-butlers."""
    monkeypatch.setenv("BUTLERS_HOST", "butlers-up")
    assert activation.switchboard_control_base_url() == "http://butlers-up:41100"

    monkeypatch.delenv("BUTLERS_HOST")
    assert activation.switchboard_control_base_url() == "http://localhost:41100"


# ---------------------------------------------------------------------------
# Criterion 3: rotation decides issuance, not the mount
# ---------------------------------------------------------------------------


def _rotation() -> tuple[SignerSnapshot, SignerSnapshot, Any, datetime]:
    """A keyring mid-rotation plus both signers that could be mounted against it.

    ``cutover`` is one hour out, so "now" sits inside the overlap: the retiring
    key may still issue, and the incoming key may not yet.
    """
    cutover = datetime.now(UTC) + timedelta(hours=1)
    old_seed, old_public = synthetic_keypair()
    new_seed, new_public = synthetic_keypair()

    keyring = parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(new_public, kid=_CURRENT_KID, sign_from=cutover),
                [
                    retiring_entry(
                        old_public,
                        kid=_RETIRING_KID,
                        sign_from=cutover - timedelta(days=1),
                        sign_until=cutover,
                    )
                ],
            )
        )
    )
    old_signer = parse_signer_document(
        _encode(
            signer_document(
                old_seed,
                kid=_RETIRING_KID,
                sign_from=cutover - timedelta(days=1),
                sign_until=cutover,
            )
        )
    )
    new_signer = parse_signer_document(
        _encode(signer_document(new_seed, kid=_CURRENT_KID, sign_from=cutover))
    )

    def _snapshot(signer_key) -> SignerSnapshot:
        return SignerSnapshot(
            signer=signer_key,
            keyring=keyring,
            matched=match_signer_to_keyring(signer_key, keyring),
        )

    return _snapshot(old_signer), _snapshot(new_signer), keyring, cutover


def _readiness_app(keyring) -> Starlette:
    verifier = VerifierSnapshot(keyring=keyring)
    return Starlette(routes=[endpoint.build_runtime_probe_readiness_route(lambda: verifier)])


async def _readiness(keyring, kid: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=_readiness_app(keyring))
    async with httpx.AsyncClient(transport=transport, base_url="http://switchboard") as client:
        return await client.get(endpoint.READINESS_PATH, params={"kid": kid})


async def test_the_pre_cutover_signer_is_ready_through_the_retiring_entry() -> None:
    """Criterion 3: the old key stays issuable until ``sign_until``, then stops."""
    old, _, keyring, cutover = _rotation()

    during_overlap = await _readiness(keyring, _RETIRING_KID)

    assert during_overlap.status_code == 200
    assert during_overlap.content == b'{"status":"ready"}'
    assert old.may_issue_at(cutover - timedelta(seconds=1)) is True
    assert old.may_issue_at(cutover + timedelta(seconds=1)) is False


async def test_the_post_cutover_signer_is_ready_through_the_current_entry() -> None:
    """And the incoming key is not issuable one instant early."""
    _, new, keyring, cutover = _rotation()

    before_cutover = await _readiness(keyring, _CURRENT_KID)

    assert before_cutover.status_code == 503
    assert new.may_issue_at(cutover - timedelta(seconds=1)) is False
    assert new.may_issue_at(cutover + timedelta(seconds=1)) is True


async def test_a_signer_the_verifier_does_not_carry_signs_nothing() -> None:
    """Criterion 3 and 9: the mixed-version state fails closed at the readiness gate.

    Both sides are internally consistent here --- this Dashboard's signer
    matches the keyring *it* loaded --- and they still disagree, which is
    exactly what a half-restarted rotation or an older image beside a newer one
    looks like.  Switchboard answers 503 for a key id it does not carry, so the
    capability is never minted and the control route is never reached.
    """
    snapshot, _ = _mounted_snapshot()
    _, _, other_keyring, _ = _rotation()
    posts: list[str] = []

    async def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == endpoint.READINESS_PATH:
            transport = httpx.ASGITransport(app=_readiness_app(other_keyring))
            return await transport.handle_async_request(request)
        posts.append(str(request.url))
        return httpx.Response(200, json={"status": "completed", "ok": True})

    client = RuntimeProbeControlClient(
        "http://switchboard:9000",
        caller="dashboard",
        signer=lambda: snapshot,
        transport=httpx.MockTransport(_route),
    )

    result = await client.probe(_ENTRY_ID)

    assert result.status is ProbeStatus.UNAVAILABLE
    assert posts == []


async def test_a_mismatched_readiness_answer_discloses_no_configured_key_id() -> None:
    """The 503 is the same bytes whatever the deployment actually loaded."""
    _, _, keyring, _ = _rotation()

    response = await _readiness(keyring, "probe-test-stray")

    rendered = response.text + json.dumps(dict(response.headers))
    assert response.status_code == 503
    assert response.content == b'{"status":"unavailable"}'
    assert _CURRENT_KID not in rendered
    assert _RETIRING_KID not in rendered
