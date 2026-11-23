"""``/reach add`` matcher parsing: fixed matchers only, never free text (bu-s11n0s.8 S3c-2)."""

import pytest

from butlers.core.reach_matchers import parse_reach_matchers

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" bank.example", {"sender_domain": "bank.example"}),
        ("domain:Bank.Example", {"sender_domain": "bank.example"}),
        ("channel:email scope:always", {"channel": "email", "scope": "always"}),
        ("kind:new_sign_in", {"security_kind": "new_sign_in"}),
        (
            "domain:a.example channel:telegram kind:mfa_changed scope:quiet_hours_only",
            {
                "sender_domain": "a.example",
                "channel": "telegram",
                "security_kind": "mfa_changed",
                "scope": "quiet_hours_only",
            },
        ),
    ],
)
def test_valid_matchers(text, expected):
    assert parse_reach_matchers(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "scope:always",
        "hello world",
        "channel:sms",
        "scope:sometimes channel:email",
        "domain:a.example domain:b.example",
        "x@bank.example",
        "domain:-bad.example",
        "unknown:value",
        "a.example b.example c.example d.example e.example",
        "domain:" + "a" * 300 + ".example",
    ],
)
def test_invalid_matchers_refuse_the_whole_command(text):
    assert parse_reach_matchers(text) is None
