"""Strict Codex authority coverage for Passport CLI mutation surfaces."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from butlers.api.routers.secrets_v2 import (
    CliRotateRequest,
    reauthorize_cli_credential,
    revoke_cli_credential,
    rotate_cli_credential,
)

pytestmark = pytest.mark.unit

_MODULE = "butlers.api.routers.secrets_v2"


def _db_with_shared_pool() -> tuple[MagicMock, MagicMock]:
    shared_pool = MagicMock()
    shared_pool.execute = AsyncMock()
    db = MagicMock()
    db.credential_shared_pool.return_value = shared_pool
    return db, shared_pool


async def test_passport_codex_paste_uses_explicit_global_authority() -> None:
    """REQ-core-credentials-001: Passport cannot bypass Codex authority on persist."""
    db, shared_pool = _db_with_shared_pool()
    authority = MagicMock()
    authority.load_codex_cli_auth = AsyncMock(return_value='{"previous": true}')
    authority.store_codex_cli_auth = AsyncMock()

    with (
        patch(f"{_MODULE}.CredentialStore", return_value=authority) as store_cls,
        patch(f"{_MODULE}._write_cli_audit", new=AsyncMock()),
    ):
        response = await rotate_cli_credential(
            "cli-auth/codex",
            body=CliRotateRequest(value='{"valid": true}'),
            db=db,
        )

    assert response.data.value == '{"valid": true}'
    store_cls.assert_called_once_with(shared_pool, system_global_pool=shared_pool)
    authority.load_codex_cli_auth.assert_awaited_once_with()
    authority.store_codex_cli_auth.assert_awaited_once_with('{"valid": true}')
    shared_pool.execute.assert_not_awaited()


async def test_passport_codex_revoke_uses_explicit_global_authority() -> None:
    """REQ-core-credentials-001: Passport cannot bypass Codex authority on revoke."""
    db, shared_pool = _db_with_shared_pool()
    authority = MagicMock()
    authority.load_codex_cli_auth = AsyncMock(return_value='{"previous": true}')
    authority.delete_codex_cli_auth = AsyncMock(return_value=True)

    with (
        patch(f"{_MODULE}.CredentialStore", return_value=authority) as store_cls,
        patch(f"{_MODULE}._write_cli_audit", new=AsyncMock()),
    ):
        response = await revoke_cli_credential("cli-auth/codex", db=db)

    assert response.data.status == "revoked"
    store_cls.assert_called_once_with(shared_pool, system_global_pool=shared_pool)
    authority.load_codex_cli_auth.assert_awaited_once_with()
    authority.delete_codex_cli_auth.assert_awaited_once_with()
    shared_pool.execute.assert_not_awaited()


async def test_passport_codex_device_auth_error_redacts_provider_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-core-credentials-001: Codex device-auth errors never expose provider diagnostics."""
    db, _ = _db_with_shared_pool()
    provider = MagicMock()
    provider.name = "codex"
    provider.auth_mode = "device_code"
    provider.is_available.return_value = True
    provider.binary.return_value = "codex"
    marker = "opaque-provider-diagnostic"
    session = MagicMock()
    session.start = AsyncMock(side_effect=RuntimeError(marker))

    with (
        patch.dict(f"{_MODULE}.PROVIDERS", {"codex": provider}, clear=True),
        patch(f"{_MODULE}.CLIAuthSession", return_value=session),
        caplog.at_level("WARNING", logger=_MODULE),
        pytest.raises(HTTPException) as error,
    ):
        await reauthorize_cli_credential("cli-auth/codex", db=db)

    assert error.value.status_code == 503
    assert error.value.detail == "Failed to start Codex device-code session."
    assert marker not in caplog.text
    assert marker not in str(error.value.detail)
