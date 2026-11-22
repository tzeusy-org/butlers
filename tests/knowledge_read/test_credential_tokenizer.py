"""The credential-name rule has one Python reference and pinned SQL twins (bu-y66o00.71).

The SQL twins run on PostgreSQL in tests/migrations/test_public_projection_source.py over the same
case list. These unit checks keep the frozen migration constants and the owner step's rendered
decoder tied to the manifest rule, so a rule change cannot land in one place only.
"""

import re
from pathlib import Path

from butlers.knowledge_read import manifest
from butlers.migrations.public_mcp_owner import _applied_decoder_sql
from butlers.testing.migration import load_migration
from tests.credential_key_cases import BENIGN_KEYS, CREDENTIAL_KEYS

_CORE_356 = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_356_public_mcp_credential_tokenizer.py"
)


def _sql_twin(key: str) -> bool:
    """core_356's functions, evaluated with ``re`` (the patterns are ARE- and re-compatible)."""
    migration = load_migration(_CORE_356)
    for pattern, replacement in migration._SPLITS:
        key = re.sub(pattern, replacement, key)
    tokens = [x for x in re.split(r"[._\s-]+", key.lower()) if x]
    return bool(re.search(migration._CREDENTIAL_PATTERN, "_".join(tokens)))


def test_core_356_freezes_the_manifest_rule():
    migration = load_migration(_CORE_356)
    assert migration._SPLITS == manifest._SPLITS
    assert migration._CREDENTIAL_PATTERN == manifest.CREDENTIAL_PATTERN
    assert "'[._[:space:]-]+'" in migration._TOKENS_BODY
    assert manifest.CREDENTIAL_PATTERN in migration._CREDENTIAL_BODY


def test_the_frozen_sql_rule_agrees_with_python_on_every_case():
    for key in (*CREDENTIAL_KEYS, *BENIGN_KEYS):
        assert _sql_twin(key) == manifest.credential(key), key


def test_the_owner_decoder_uses_the_same_tokenizer_and_pattern():
    sql = _applied_decoder_sql()
    assert manifest.tokens_sql("c") in sql and manifest.tokens_sql("x") in sql
    assert manifest.literal(manifest.CREDENTIAL_PATTERN)[1:-1] in sql
    # The pre-bu-y66o00.71 camel-only split must be gone from every guard.
    assert "'([a-z0-9])([A-Z])'" not in sql
