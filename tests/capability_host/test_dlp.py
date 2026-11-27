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


PHONE_SHAPED_UUID = "e0bce383-8175-4977-b5a7-eb427d30fb38"
PHONE_SHAPED_TAIL = "7f2c91a4-0b3d-4e8f-9123-4567a6a7e968"
# The proposal view (test_public_mcp_proposal_e2e) lost its last two groups the same way.
PHONE_SHAPED_V5 = "2883f486-86be-53ee-9876-5432a6a7e968"


@pytest.mark.parametrize(
    "uuid", [PHONE_SHAPED_UUID, PHONE_SHAPED_TAIL, PHONE_SHAPED_V5, PHONE_SHAPED_UUID.upper()]
)
def test_a_uuid_is_never_phone_redacted_even_when_its_digit_groups_look_like_a_phone(uuid):
    """bu-y66o00.54: a stored id such as ...-8175-4977-... must reach the caller intact."""
    out, scan = scan_result("search", None, {"items": [{"id": uuid, "ref": f"proposal:{uuid}"}]})

    assert out["items"][0] == {"id": uuid, "ref": f"proposal:{uuid}"}
    assert "phone" not in scan.rules


def test_a_uuid_in_free_text_survives_while_a_real_phone_beside_it_is_redacted():
    text, scan = _text(
        "search", None, f"see {PHONE_SHAPED_UUID}, then call 8123 4567 or +65 6123 4567"
    )

    assert PHONE_SHAPED_UUID in text
    assert "8123 4567" not in text and "+65 6123 4567" not in text
    assert text.count("[redacted:phone]") == 2 and scan.rules["phone"] == 2


def test_digits_that_merely_touch_a_uuid_are_not_exempt():
    text, _ = _text("search", None, f"{PHONE_SHAPED_UUID}x call 6123-4567")

    assert "6123-4567" not in text


def test_separators_and_invisible_characters_do_not_defeat_the_scan():
    wide = "".join(chr(ord(c) + 0xFEE0) for c in CARD)  # full-width digits
    spaced = " ".join(wide[i : i + 4] for i in range(0, 16, 4))
    hidden = spaced[:7] + "‍" + spaced[7:]
    text, scan = _text("search", None, f"card: {hidden}.")
    assert text == "card: [redacted:luhn]." and scan.counted == 1


# Task 6.1 DLP evasion: every Cf invisible a writer could slip between digit groups.
@pytest.mark.parametrize(
    "invisible",
    ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u180e", "\u2063"],
    ids=["zwsp", "zwnj", "zwj", "word-joiner", "bom", "soft-hyphen", "mvs", "invisible-sep"],
)
def test_zero_width_characters_inside_a_card_number_are_removed_before_the_scan(invisible):
    hidden = invisible.join(CARD[i : i + 4] for i in range(0, 16, 4))
    text, scan = _text("search", None, f"card: {hidden}.")
    assert text == "card: [redacted:luhn]." and scan.counted == 1


# "Matched after removing spaces and dashes" (REQ-public-mcp-authorization-010): a typographic
# dash is a dash, and NFKC maps the full-width and no-break forms onto these.
@pytest.mark.parametrize(
    "separator",
    ["-", " ", "\u00a0", "\u3000", "\uff0d", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"]
    + ["\u2212"],
    ids=["hyphen-minus", "space", "nbsp", "ideographic-space", "fullwidth-hyphen", "hyphen"]
    + ["nb-hyphen", "figure-dash", "en-dash", "em-dash", "minus"],
)
def test_space_and_dash_separators_do_not_defeat_the_luhn_rule(separator):
    grouped = separator.join(CARD[i : i + 4] for i in range(0, 16, 4))
    text, scan = _text("search", None, f"card: {grouped}.")
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
    ids=[
        "iban",
        "identity-document",
        "account-number",
        "one-time-password",
        "aws-access-key",
        "github-pat",
        "slack-token",
        "api-key",
        "jwt",
        "pem",
        "url-userinfo",
        "url-query-secret",
        "url-fragment-secret",
        "url-path-secret",
        "high-entropy-string",
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


# Task 6.1: the marks skip only the entropy and path-segment rules, so each provider's signed
# URL and every userinfo-with-password form is still redacted, on a marked column and on a
# marked JSON path alike.
_SIGNED_OR_USERINFO = {
    "s3": (PRESIGNED, "url_secret"),
    "s3-upper": ("https://b.example/o?X-AMZ-SIGNATURE=abc&X-AMZ-DATE=20300101", "url_secret"),
    "gcs": ("https://storage.example/b/o?X-Goog-Signature=abc&X-Goog-Expires=60", "url_secret"),
    "azure-sas": ("https://acct.blob.example/c/b?sv=2030-01-01&sp=r&sig=abc", "url_secret"),
    "cloudfront": ("https://cdn.example/o?Expires=1&Signature=abc&Key-Pair-Id=K1", "url_secret"),
    "camel-token": ("https://r.example/x?accessToken=abc", "url_secret"),
    "userinfo": ("https://alice:hunter2@bridge.example/simplefin", "url_userinfo"),
    "userinfo-empty-user": ("https://:hunter2@bridge.example/x", "url_userinfo"),
}


@pytest.mark.parametrize(
    ("url", "rule"), list(_SIGNED_OR_USERINFO.values()), ids=list(_SIGNED_OR_USERINFO)
)
def test_signed_and_userinfo_urls_are_redacted_on_marked_columns_and_paths(url, rule):
    column = {"kind": "transactions", "id": "a", "fields": {"receipt_url": url}}
    path = {"kind": "facts", "id": "f", "fields": {}, "json_fields": {"metadata.external_ref": url}}
    out, scan = scan_result("finance_records", "finance", {"items": [column, path]})
    assert out["items"][0]["fields"]["receipt_url"] == f"[redacted:{rule}]"
    assert out["items"][1]["json_fields"]["metadata.external_ref"] == f"[redacted:{rule}]"
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
    assert len(default_tools()) == 19


# bu-y66o00.70: invisible fillers that are not Cc or Cf, token-shaped userinfo, secret names.
_FILLERS = {
    "hangul-filler": "ㅤ",
    "halfwidth-hangul-filler": "ﾠ",
    "choseong-filler": "ᅟ",
    "jungseong-filler": "ᅠ",
    "cgj": "͏",
    "braille-blank": "⠀",
    "variation-selector": "️",
    "khmer-inherent-vowel": "឴",
    "mongolian-fvs": "᠋",
    "tag-space": "\U000e0020",
}


@pytest.mark.parametrize("filler", list(_FILLERS.values()), ids=list(_FILLERS))
def test_invisible_fillers_inside_a_card_number_are_removed_before_the_scan(filler):
    hidden = CARD[:6] + filler + CARD[6:]
    text, scan = _text("search", None, f"card: {hidden}.")
    assert text == "card: [redacted:luhn]." and scan.counted == 1


def test_combining_marks_and_ordinary_accents_survive_normalization():
    assert normalize("café näive") == "café näive"
    assert normalize("à́b") == "à́b"


_TOKEN = "a1B2c3D4e5" * 2  # synthetic, built at runtime


def test_token_shaped_userinfo_without_a_password_is_redacted():
    text, scan = _text("search", None, f"see https://{_TOKEN}@bridge.example/x now")
    assert text == "see [redacted:url_userinfo] now" and scan.counted == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://git@github.example/o/r.git",
        "ssh://git@host.example/repo",
        "https://user@host.example/x",
        "https://a1B2c3D4e5@host.example/x",  # short userinfo
        "https://abcdefghijklmnopqrstuvwxyz@host.example/x",  # long but not token-shaped
    ],
)
def test_ordinary_userinfo_is_left_alone(url):
    # finance shares contact details, so only the userinfo rule is under test.
    text, scan = _text("finance_ledger", "finance", f"clone {url} here")
    assert text == f"clone {url} here" and scan.counted == 0


def test_token_shaped_userinfo_is_redacted_on_marked_columns_and_paths():
    url = f"https://{_TOKEN}@bridge.example/x"
    column = {"kind": "transactions", "id": "a", "fields": {"receipt_url": url}}
    path = {"kind": "facts", "id": "f", "fields": {}, "json_fields": {"metadata.external_ref": url}}
    out, scan = scan_result("finance_records", "finance", {"items": [column, path]})
    assert out["items"][0]["fields"]["receipt_url"] == "[redacted:url_userinfo]"
    assert out["items"][1]["json_fields"]["metadata.external_ref"] == "[redacted:url_userinfo]"
    assert scan.counted == 2


def test_git_userinfo_survives_on_marked_columns():
    url = "https://git@host.example/o/r.git"
    row = {"kind": "transactions", "id": "a", "fields": {"receipt_url": url}}
    out, scan = scan_result("finance_records", "finance", {"items": [row]})
    assert out["items"][0]["fields"]["receipt_url"] == url and scan.counted == 0


@pytest.mark.parametrize("name", ["secret", "client_secret", "clientSecret", "apikey", "ApiKey"])
def test_secret_and_apikey_query_names_are_redacted_on_marked_columns(name):
    row = {
        "kind": "transactions",
        "id": "a",
        "fields": {"receipt_url": f"https://r.example/x?{name}=abc"},
    }
    out, scan = scan_result("finance_records", "finance", {"items": [row]})
    assert out["items"][0]["fields"]["receipt_url"] == "[redacted:url_secret]"
    assert scan.counted == 1


def test_secretary_style_query_names_are_not_secret_names():
    url = "https://r.example/x?secretary=abc&page=2"
    text, scan = _text("search", None, url)
    assert text == url and scan.counted == 0


def test_find_canary_sees_through_invisible_fillers():
    from butlers.capability_host.dlp import find_canary

    canary = "CANARY-ZZ-0001"
    assert find_canary({"a": canary[:6] + "ㅤ" + canary[6:]}, (canary,))
    assert find_canary({"a": canary[:6] + "⠀" + canary[6:]}, (canary,))
    assert not find_canary({"a": "café"}, (canary,))


def test_health_metric_names_are_scanned():
    out, scan = scan_result(
        "health_metrics",
        "health",
        {"metrics": ["measurement_weight", f"token {GHP}"], "source": {"kind": "health_metrics"}},
    )
    assert out["metrics"] == ["measurement_weight", "token [redacted:github_token]"]
    assert scan.counted == 1


# bu-8kbzmt: a Luhn-valid digit run inside a UUID is part of an identifier, not a card number.
@pytest.mark.parametrize(
    "uuid",
    [
        "b5d61f25-3742-4417-9518-5da3878a52b2",
        "9befdb99-3549-5338-9858-006d2e3f4a5b",
    ],
)
def test_luhn_runs_inside_a_uuid_are_left_alone(uuid):
    text, scan = _text("search", None, f"entity {uuid} and proposal:{uuid}")
    assert text == f"entity {uuid} and proposal:{uuid}" and scan.counted == 0


def test_a_card_number_next_to_a_uuid_is_still_redacted():
    uuid = "b5d61f25-3742-4417-9518-5da3878a52b2"
    text, scan = _text("search", None, f"{uuid} card {CARD}")
    assert text == f"{uuid} card [redacted:luhn]" and scan.counted == 1
