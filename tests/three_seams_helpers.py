"""Immutable public source controls for the run16 seam regressions.

Bodies are exact public source at 461e03b32ac3b88e2a92d487432f77a73aa2b829.
They execute against the same disposable migrated PostgreSQL fixture as the fix;
no shallow-checkout git fetch, applied migration edit or live connection is used.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_FIXTURE_SHA256 = "c247d36c3d64fcbb2f19421b66609e7c3c717b32182ffa4fb96624c75753132d"


def baseline_function(name: str, globals_: dict[str, Any]):
    raw = (Path(__file__).parent / "fixtures" / "three_seams_baseline.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256
    item = json.loads(raw)[name]
    assert hashlib.sha256(item["body"].encode()).hexdigest() == item["body_sha256"]
    namespace = dict(globals_)
    exec(compile(item["body"], item["path"] + "@" + item["git_sha"], "exec"), namespace)
    return namespace[name]


def preview_label_controls(fallback: str) -> list[tuple[str, str, str, str, dict]]:
    """Synthetic current/legacy label leaks with genuine safe-domain companions."""
    code = "Your verification code is 482913"
    url = "https://example.test/reset?token=synthetic-reset-token"
    cases = [
        ("provider-path", "777000", "482913", "telegram/482913", fallback),
        ("provider-url", "777000", "482913", url, fallback),
        ("provider-dns-code", "777000", "482913", "482913.example.test", fallback),
        ("provider-nonstring", "777000", "482913", 7, fallback),
        ("provider-safe", "777000", "482913", "telegram", "telegram"),
        ("sender-path", "person@telegram/482913", code, "gmail", fallback),
        ("sender-url", "person@" + url, code, "gmail", fallback),
        ("sender-dns-code", "person@482913.example.test", code, "gmail", fallback),
        ("sender-safe", "person@safe.example.test", code, "gmail", "safe.example.test"),
        ("sender-google", "security@accounts.google.com", code, "gmail", "accounts.google.com"),
    ]
    controls = [
        (
            name,
            sender,
            preview,
            preview.replace("482913", "[auth-code withheld: " + domain + "]"),
            {"source": {"provider": provider}},
        )
        for name, sender, preview, provider, domain in cases
    ]
    controls.extend(
        [
            (
                "sender-dns-split-code",
                "person@482-913.example.test",
                "Your verification code is 482 913",
                "Your verification code is [auth-code withheld: " + fallback + "]",
                {"source": {"provider": "gmail"}},
            ),
            (
                "sender-dns-token",
                "person@synthetic-reset-token.example.test",
                code + " " + url,
                "Your verification code is [auth-code withheld: "
                + fallback
                + "] [reset-link withheld: example.test]",
                {"source": {"provider": "gmail"}},
            ),
            (
                "existing-code-label",
                "777000",
                "[auth-code withheld: telegram/482913]",
                "[auth-code withheld: " + fallback + "]",
                {},
            ),
            (
                "existing-token-label",
                "777000",
                "[auth-code withheld: " + url + "]",
                "[auth-code withheld: " + fallback + "]",
                {},
            ),
        ]
    )
    return controls
