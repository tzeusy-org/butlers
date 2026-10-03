"""Bearer-material scrubber: codes and links are withheld, ordinary numbers survive.

All fixtures are synthetic.
"""

from __future__ import annotations

import base64

import pytest

from butlers.ingestion_bearer_scrub import (
    KIND_AUTH_CODE,
    KIND_MAGIC_LINK,
    KIND_RESET_LINK,
    scrub_envelope,
    scrub_json,
    scrub_text,
)

DOMAIN = "example.com"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Your code is 482913", "482913"),
        ("Your verification code: 482913. It expires in 10 minutes.", "482913"),
        ("G-482913 is your Google verification code", "482913"),
        ("482913 is your login code", "482913"),
        ("One-time password: 482 913", "482 913"),
        ("Login code: 48291. Do not give this code to anyone.", "48291"),
    ],
)
def test_codes_are_withheld(text: str, secret: str) -> None:
    out, hits = scrub_text(text, provider_domain=DOMAIN)
    assert secret not in out
    assert "[auth-code withheld: example.com]" in out
    assert {h.kind for h in hits} == {KIND_AUTH_CODE}


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://acct.example.com/reset-password?token=abc123def456", KIND_RESET_LINK),
        ("https://acct.example.com/magic/Xk3j9Zq1Lm5Np7Rt2Vw4Yb6", KIND_MAGIC_LINK),
        ("https://acct.example.com/verify?code=991122", KIND_MAGIC_LINK),
    ],
)
def test_bearer_links_are_withheld(url: str, kind: str) -> None:
    out, hits = scrub_text(f"Click {url}.", provider_domain=DOMAIN)
    assert url not in out
    assert out.endswith(".")
    assert [h.kind for h in hits] == [kind]
    assert hits[0].provider_domain == "acct.example.com"


@pytest.mark.parametrize(
    "text",
    [
        "Order number 12345678 shipped on 2026-10-03",
        "Your table is booked for 2026-10-03 at 19:30",
        "See https://shop.example.com/orders/12345 for details",
        "Unsubscribe: https://news.example.com/u?id=42",
        "Zip code 90210",
        "Invoice 20261003 total $1,482.00",
    ],
)
def test_ordinary_text_survives(text: str) -> None:
    out, hits = scrub_text(text, provider_domain=DOMAIN)
    assert out == text
    assert hits == []


def test_scrub_is_idempotent_even_for_numeric_domains() -> None:
    once, _ = scrub_text("Your code is 482913", provider_domain="123456.com")
    twice, hits = scrub_text(once, provider_domain="123456.com")
    assert twice == once
    assert hits == []


def test_expires_hint_is_captured() -> None:
    _, hits = scrub_text("Your code is 482913, expires in 10 minutes", provider_domain=DOMAIN)
    assert hits[0].expires_hint == "10m"


def test_service_sender_scrubs_every_code_shaped_run() -> None:
    envelope = {
        "source": {"provider": "telegram"},
        "sender": {"identity": "777000"},
        "payload": {"raw": {"message": "Telegram 55123 here"}, "normalized_text": "Telegram 55123"},
    }
    out, hits = scrub_envelope(envelope)
    assert "55123" not in out["payload"]["normalized_text"]
    assert "55123" not in str(out["payload"]["raw"])
    assert out["control"]["bearer_scrubbed"] is True
    assert hits


def test_gmail_base64_body_is_scrubbed_and_reencoded() -> None:
    body = base64.urlsafe_b64encode(b"Your code is 482913").decode().rstrip("=")
    raw = {"snippet": "Your code is 482913", "payload": {"body": {"data": body, "size": 19}}}
    out, hits = scrub_json(raw, provider_domain=DOMAIN)
    decoded = base64.urlsafe_b64decode(out["payload"]["body"]["data"] + "==").decode()
    assert "482913" not in decoded
    assert "482913" not in out["snippet"]
    assert hits


def test_untouched_envelope_is_not_flagged() -> None:
    envelope = {
        "source": {"provider": "gmail"},
        "sender": {"identity": "a@example.com"},
        "payload": {"raw": None, "normalized_text": "Lunch on 2026-10-03?"},
        "control": {"policy_tier": "default"},
    }
    out, hits = scrub_envelope(envelope)
    assert hits == []
    assert out["control"] == {"policy_tier": "default"}


def test_ingest_helper_records_typed_artifacts_without_the_code() -> None:
    from datetime import UTC, datetime

    from butlers.tools.switchboard.ingestion.ingest import _scrub_bearer_material
    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    text = "Your code is 482913, expires in 10 minutes. Order 12345678."
    envelope = parse_ingest_envelope(
        {
            "schema_version": "ingest.v1",
            "source": {"channel": "email", "provider": "gmail", "endpoint_identity": "me"},
            "event": {"external_event_id": "e1", "observed_at": datetime.now(UTC).isoformat()},
            "sender": {"identity": "no-reply@example.com"},
            "payload": {"raw": {"body": text}, "normalized_text": text},
        }
    )
    observed = envelope.event.observed_at
    raw = {
        "sender": {"identity": "no-reply@example.com"},
        "payload": {"raw": {"body": text}, "normalized_text": text},
        "control": {},
    }
    new_raw, new_text, fresh = _scrub_bearer_material(envelope, raw, text, observed_at=observed)

    assert "482913" not in new_text + str(new_raw)
    assert "12345678" in new_text
    assert new_raw["control"]["bearer_scrubbed"] is True
    assert new_raw["bearer_artifacts"][0] == {
        "kind": "auth_code",
        "provider_domain": "example.com",
        "expires_hint": "10m",
        "observed_at": observed.isoformat(),
    }
    assert len(fresh) == 1

    again_raw, again_text, again = _scrub_bearer_material(
        envelope, new_raw, new_text, observed_at=observed
    )
    assert again_text == new_text
    assert again == []
    assert again_raw["bearer_artifacts"][0]["kind"] == "auth_code"
