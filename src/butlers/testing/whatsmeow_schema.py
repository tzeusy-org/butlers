"""Execute the pinned external LID-store migration for disposable SQL fixtures.

This table belongs to Whatsmeow, not a Butlers Alembic chain. The complete
upstream upgrade8 bytes are retained without rewriting its constraints. This
proves only that external prerequisite, not a full bridge/device-store upgrade.
No Go download, application connection, or provider operation occurs here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_VERSION = "v0.0.0-20260722203353-e9a033b24933"
_SQL = _ROOT / "tests/fixtures/whatsmeow/08-lid-mapping.sql"
_SHA256 = "ea76d211e68bd5d8c91a249aac5a63c717509766f556cf778d73da3688592b86"


def lid_store_sql() -> str:
    """Refuse dependency or literal drift before executing any fixture SQL."""
    declaration = f"go.mau.fi/whatsmeow {_VERSION}"
    module = (_ROOT / "whatsapp-bridge/go.mod").read_text()
    checksum = (_ROOT / "whatsapp-bridge/go.sum").read_text()
    source = _SQL.read_bytes()
    if (
        declaration not in module
        or declaration + " h1:" not in checksum
        or hashlib.sha256(source).hexdigest() != _SHA256
    ):
        raise RuntimeError("external Whatsmeow schema provenance changed")
    return source.decode("utf-8")


async def install_lid_store(connection) -> None:
    """Apply the actual external upgrade to one fresh disposable database."""
    await connection.execute(lid_store_sql())
