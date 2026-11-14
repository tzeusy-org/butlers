"""Response DLP (REQ-public-mcp-authorization-010; RFC 0039 D15, OD17)."""

from __future__ import annotations

import pytest

from butlers.capability_host import Tool
from butlers.capability_host.dlp import normalize, scan_result
from tests.capability_host.helpers import Env, default_fake_tools, error_code, fake_reader

pytestmark = pytest.mark.unit

CARD = "4111111111111111"
IBAN = "GB82 WEST 1234 5698 7654 32"
GHP = "ghp_" + "a1B2c3D4e5" * 4
B64_REF = "Q2xvc2VkUmVmZXJlbmNlMDEyMzQ1Njc4OWFiY2Rl"[:32]
ENTROPIC = "x9Kq2Lm7Pz4Rt8Vw1Ys5Nb3Hc6Jd0Fg2"
PRESIGNED = (
    "https://bucket.s3.amazonaws.com/r.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=AKIDEXAMPLE&X-Amz-Signature=abcdef0123456789"
)


def _text(tool: str, domain: str | None, text: str):
    out, scan = scan_result(tool, domain, {"items": [{"id": "r1", "text": text}]})
    return out["items"][0]["text"], scan


def test_a_card_number_is_redacted():
    text, scan = _text("search", None, f"paid with {CARD} yesterday")
    assert text == "paid with [redacted:luhn] yesterday" and scan.counted == 1


def test_separators_and_invisible_characters_do_not_defeat_the_scan():
    wide = "".join(chr(ord(c) + 0xFEE0) for c in CARD)  # full-width digits
    spaced = " ".join(wide[i : i + 4] for i in range(0, 16, 4))
    hidden = spaced[:7] + "‍" + spaced[7:]
    text, scan = _text("search", None, f"card: {hidden}.")
    assert text == "card: [redacted:luhn]." and scan.counted == 1


def test_normalization_keeps_tab_and_newline():
    assert normalize("a\tb\nc\x00d​e f") == "a\tb\ncde f"


def test_contact_details_are_redacted_without_dropping_the_response():
    emails = " ".join(f"person{i}@example.com" for i in range(7))
    text, scan = _text("search", None, emails)
    assert text.count("[redacted:email]") == 7 and scan.counted == 0
    text, _ = _text("fetch", None, "call +65 9123 4567 or (415) 555-2671")
    assert text == "call [redacted:phone] or [redacted:phone]"


def test_a_booking_code_is_redacted():
    text, scan = _text("search", None, "booking ref Q7XK2P for Lisbon")
    assert text == "booking ref [redacted:pnr] for Lisbon" and scan.counted == 1


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (f"transfer to {IBAN} today", "iban"),
        ("passport no. K1234567 renewal", "document"),
        ("account number 12345678901 closed", "account"),
        ("your verification code is 482913", "otp"),
        ("key AKIAIOSFODNN7EXAMPLE leaked", "aws_key"),
        (f"token {GHP}", "github_token"),
        ("xoxb-1234567890-abcdefghij here", "slack_token"),
        ("sk-ant-api03-abcdefghijklmnopqrstuv", "api_key"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3In0.c2lnbmF0dXJlMTIz", "jwt"),
        ("-----BEGIN PRIVATE KEY-----\nMIIBVgIBADAN\n-----END PRIVATE KEY-----", "pem"),
        ("https://u:p@bridge.example/simplefin", "url_userinfo"),
        (PRESIGNED, "url_secret"),
        ("https://zoom.us/j/123?pwd=abc", "url_secret"),
        ("https://x.example/d/" + ENTROPIC, "url_path"),
        (ENTROPIC, "entropy"),
    ],
)
def test_each_rule_redacts_its_shape(text, rule):
    out, scan = _text("search", None, text)
    assert f"[redacted:{rule}]" in out and scan.rules[rule] == 1


@pytest.mark.parametrize(
    "text",
    [
        "Lunch at Fable with Avery on 2026-11-14T12:30:00+08:00",
        "a1b2c3d4-5566-4788-99aa-bbccddeeff01",
        "https://www.example.com/articles/how-to-write-a-good-blog-post?page=2",
        "internationalization",
        "Total 1234.56 SGD",
    ],
)
def test_ordinary_text_is_left_alone(text):
    out, scan = _text("search", None, text)
    assert out == text and scan.counted == 0 and not scan.rules


def test_shared_finance_and_health_identifiers_are_not_redacted():
    ledger, scan = scan_result(
        "finance_ledger",
        "finance",
        {"rows": [{"id": "t1", "description": f"wire {IBAN}", "amount": "12.00"}]},
    )
    assert ledger["rows"][0]["description"] == f"wire {IBAN}" and scan.counted == 0
    profile, scan = scan_result(
        "health_profile",
        "health",
        {"providers": [{"text": "Dr Tan", "fields": {"metadata.phone": "+65 6123 4567"}}]},
    )
    assert profile["providers"][0]["fields"]["metadata.phone"] == "+65 6123 4567"
    assert scan.counted == 0


def test_marked_reference_columns_keep_long_opaque_ids():
    out, scan = scan_result(
        "finance_records",
        "finance",
        {"items": [{"kind": "transactions", "id": "t1", "fields": {"external_ref": B64_REF}}]},
    )
    assert out["items"][0]["fields"]["external_ref"] == B64_REF and scan.counted == 0


def test_marked_json_paths_keep_long_opaque_ids_and_others_are_scanned():
    item = {
        "kind": "facts",
        "id": "f1",
        "fields": {},
        "json_fields": {"metadata.external_ref": B64_REF, "metadata.note_blob": ENTROPIC},
    }
    out, scan = scan_result("finance_records", "finance", {"items": [item]})
    fields = out["items"][0]["json_fields"]
    assert fields["metadata.external_ref"] == B64_REF
    assert fields["metadata.note_blob"] == "[redacted:entropy]" and scan.counted == 1


def test_json_fields_rows_honour_their_source_path_mark():
    def row(path: str):
        fields = {"source_table": "finance.facts", "path": path, "value_text": B64_REF}
        return {"kind": "json_fields_public", "id": path, "fields": fields}

    out, scan = scan_result(
        "finance_records",
        "finance",
        {"items": [row("metadata.external_ref"), row("metadata.other")]},
    )
    assert out["items"][0]["fields"]["value_text"] == B64_REF
    assert out["items"][1]["fields"]["value_text"] == "[redacted:entropy]"


def test_signed_and_userinfo_urls_are_redacted_on_marked_columns():
    rows = [
        {"kind": "transactions", "id": "a", "fields": {"receipt_url": PRESIGNED}},
        {
            "kind": "transactions",
            "id": "b",
            "fields": {"receipt_url": "https://u:p@bridge.example/simplefin"},
        },
    ]
    out, scan = scan_result("finance_records", "finance", {"items": rows})
    assert out["items"][0]["fields"]["receipt_url"] == "[redacted:url_secret]"
    assert out["items"][1]["fields"]["receipt_url"] == "[redacted:url_userinfo]"
    assert scan.counted == 2


def test_account_numbers_that_pass_luhn_are_shared_in_finance():
    ledger, scan = scan_result(
        "finance_ledger",
        "finance",
        {
            "rows": [
                {"id": "a", "description": f"ref {CARD} settled"},
                {"id": "b", "notes": "card 4111 1111 1111 1111"},
            ]
        },
    )
    assert ledger["rows"][0]["description"] == f"ref {CARD} settled"
    assert ledger["rows"][1]["notes"] == "card [redacted:luhn]"
    assert scan.luhn_uncontexted == 1 and scan.counted == 1


def test_credential_shapes_are_redacted_in_finance_and_health_too():
    rows = [
        {"kind": "transactions", "id": "a", "fields": {"notes": f"token {GHP}"}},
        {
            "kind": "transactions",
            "id": "b",
            "fields": {"receipt_url": "https://r.example/x?token=1"},
        },
    ]
    out, scan = scan_result("finance_records", "finance", {"items": rows})
    assert out["items"][0]["fields"]["notes"] == "token [redacted:github_token]"
    assert out["items"][1]["fields"]["receipt_url"] == "[redacted:url_secret]"
    health, hscan = scan_result(
        "health_records",
        "health",
        {
            "items": [
                {
                    "kind": "facts",
                    "id": "c",
                    "fields": {"content": "join https://zoom.us/j/1?pwd=Zx"},
                }
            ]
        },
    )
    assert health["items"][0]["fields"]["content"] == "join [redacted:url_secret]"
    assert scan.counted == 2 and hscan.counted == 1


def test_amounts_are_not_mistaken_for_secrets():
    ledger, scan = scan_result(
        "finance_ledger", "finance", {"rows": [{"id": "a", "amount": f"{CARD}.00"}]}
    )
    assert ledger["rows"][0]["amount"] == f"{CARD}.00" and not scan.rules
    out, _ = scan_result("search", None, {"items": [{"id": "a", "text": f"{CARD}"}]})
    assert out["items"][0]["text"] == "[redacted:luhn]"


def test_server_fields_are_not_scanned_but_published_nested_ones_are():
    result = {
        "next_cursor": ENTROPIC,
        "items": [
            {
                "url": "https://dash.example/x/" + ENTROPIC,
                "source": {"uri": "butlers://k/fact/" + ENTROPIC},
                "kind": "transactions",
                "fields": {"metadata": {"url": "https://x.example/?token=1"}},
            }
        ],
    }
    out, _ = scan_result("finance_records", "finance", result)
    assert out["next_cursor"] == ENTROPIC and out["items"][0]["url"].endswith(ENTROPIC)
    assert out["items"][0]["fields"]["metadata"]["url"] == "[redacted:url_secret]"


async def test_a_flood_of_matches_blocks_the_response(tmp_path):
    tools = default_fake_tools()
    flood = {"items": [{"id": f"r{i}", "text": f"token {GHP}"} for i in range(6)]}
    tools["trips"] = Tool("trips", "travel", fake_reader("travel", flood))
    five = {"items": [{"id": f"r{i}", "text": f"token {GHP}"} for i in range(5)]}
    tools["entity_lookup"] = Tool("entity_lookup", "relationship", fake_reader(None, five))
    env = Env(tmp_path, tools=tools)
    message = await env.tool("trips")
    assert error_code(message) == "blocked" and GHP not in str(message)
    assert env.host.metrics.get("dlp_blocked", tool="trips") == 1
    served = await env.tool("entity_lookup")
    assert error_code(served) is None and "[redacted:github_token]" in str(served)
    assert env.audits()[-1]["redactions"] == 5
    assert env.host.metrics.get("redactions", tool="entity_lookup", rule="github_token") == 5


async def test_describe_carries_its_digest_unredacted(env: Env):
    from tests.capability_host.helpers import MANIFEST

    described = await env.tool("describe")
    assert described["result"]["structuredContent"]["manifest_digest"] == MANIFEST.digest
    out, _ = scan_result("search", None, {"items": [{"id": "a", "text": MANIFEST.digest}]})
    assert out["items"][0]["text"] == "[redacted:entropy]"


def test_only_describe_skips_dlp():
    from butlers.capability_host import default_tools

    assert {t.name for t in default_tools().values() if not t.dlp} == {"describe"}
    assert len(default_tools()) == 18
