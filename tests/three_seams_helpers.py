"""Immutable public source controls for the run16 seam regressions.

Stored bodies and hashes are exact public source at
461e03b32ac3b88e2a92d487432f77a73aa2b829. Only the private compiled
create_catalog_entry copy adapts its transaction boundary for current owned
audit append; its historical SQL and allowance omission remain unchanged.
They execute against the same disposable migrated PostgreSQL fixture as the fix;
no shallow-checkout git fetch, applied migration edit or live connection is used.
Virtual filenames live under tests/fixtures/historical/<revision>/<original path>
so the coverage tracer cannot attribute old bodies to current production source.
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
    body = item["body"]
    if name == "create_catalog_entry":
        # Keep the stored body exact. Adapt its old definition-time dependency
        # in the private namespace, and only its compiled transaction boundary
        # for the current audit owner; preserve the historical SQL/arguments.
        from butlers.api.deps import get_db
        from butlers.core.audit_transaction import audit_transaction

        namespace["_get_db_manager"] = get_db
        original_context = "async with pool.acquire() as connection, connection.transaction():"
        owned_context = "async with pool.acquire() as connection, audit_transaction(connection):"
        assert body.count(original_context) == 1
        assert owned_context not in body
        adapted_body = body.replace(original_context, owned_context, 1)
        assert adapted_body.replace(owned_context, original_context, 1) == body
        body = adapted_body
        namespace["audit_transaction"] = audit_transaction
    filename = Path(__file__).parent / "fixtures" / "historical" / item["git_sha"] / item["path"]
    exec(compile(body, str(filename), "exec"), namespace)
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
                "sender-dns-dot-code",
                "person@482.913.example.test",
                code,
                "Your verification code is [auth-code withheld: " + fallback + "]",
                {"source": {"provider": "gmail"}},
            ),
            (
                "sender-dns-prefixed-code",
                "person@482.913.example.test",
                "Your verification code is G-482913",
                "Your verification code is [auth-code withheld: " + fallback + "]",
                {"source": {"provider": "gmail"}},
            ),
            (
                "sender-dns-dot-token",
                "person@synthetic.reset.token.example.test",
                code + " " + url,
                "Your verification code is [auth-code withheld: "
                + fallback
                + "] [reset-link withheld: example.test]",
                {"source": {"provider": "gmail"}},
            ),
            (
                "provider-token-slug",
                "777000",
                "482913 " + url,
                "[auth-code withheld: " + fallback + "] [reset-link withheld: example.test]",
                {"source": {"provider": "synthetic_reset_token"}},
            ),
            (
                "existing-dot-code-label",
                "777000",
                "[auth-code withheld: 482.913.example.test]",
                "[auth-code withheld: " + fallback + "]",
                {},
            ),
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
