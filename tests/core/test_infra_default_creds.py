"""Tests for infra default-credential detection (check_infra_default_creds).

The function lives in butlers.db alongside is_hardened_posture().

Scenarios covered:
1. Known-default credentials in dev posture → warns, does NOT raise.
2. Known-default credentials in hardened posture → raises RuntimeError.
3. All credentials set to non-default values → neither warns nor raises.
4. Partial defaults (some set, some absent) → appropriate behaviour per posture.
5. Absent env vars are treated as defaults (mirrors docker-compose :-fallback).
"""

from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_infra_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all infra-cred env vars so absence-as-default logic triggers."""
    for var in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "GF_SECURITY_ADMIN_USER",
        "GF_SECURITY_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


def _set_strong_infra_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all infra-cred env vars to non-default values."""
    monkeypatch.setenv("MINIO_ROOT_USER", "my-minio-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "s3cr3t-minio-pw")
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "grafana-ops")
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "str0ng-grafana-pw")


# ---------------------------------------------------------------------------
# Dev-posture tests
# ---------------------------------------------------------------------------


def test_known_defaults_dev_posture_warns_not_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Known-default infra creds under dev posture must warn, never raise."""
    from butlers.db import check_infra_default_creds

    monkeypatch.delenv("BUTLERS_POSTURE", raising=False)
    _clear_infra_creds(monkeypatch)  # absent → treated as default

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()  # must NOT raise

    # At least one WARNING logged for MinIO and Grafana defaults.
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("MinIO" in m for m in warning_messages), (
        "Expected at least one WARNING mentioning MinIO default credentials"
    )
    assert any("Grafana" in m for m in warning_messages), (
        "Expected at least one WARNING mentioning Grafana default credentials"
    )


def test_explicit_known_default_values_dev_posture_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Explicitly setting credentials to known-default values also triggers warning in dev."""
    from butlers.db import check_infra_default_creds

    monkeypatch.delenv("BUTLERS_POSTURE", raising=False)
    monkeypatch.setenv("MINIO_ROOT_USER", "minioadmin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "admin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "admin")

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()  # must NOT raise

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_messages) == 4, (
        f"Expected 4 warnings (one per credential), got {len(warning_messages)}: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# Hardened-posture tests
# ---------------------------------------------------------------------------


def test_known_defaults_hardened_posture_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known-default infra creds under hardened posture must raise RuntimeError."""
    from butlers.db import check_infra_default_creds

    monkeypatch.setenv("BUTLERS_POSTURE", "hardened")
    _clear_infra_creds(monkeypatch)

    with pytest.raises(RuntimeError, match="known-default"):
        check_infra_default_creds()


def test_known_defaults_hardened_posture_error_names_offenders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The RuntimeError message must identify the offending credentials."""
    from butlers.db import check_infra_default_creds

    monkeypatch.setenv("BUTLERS_POSTURE", "hardened")
    monkeypatch.setenv("MINIO_ROOT_USER", "minioadmin")  # default
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")  # default
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "grafana-ops")  # non-default
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "grafana-ops")  # non-default

    with pytest.raises(RuntimeError) as exc_info:
        check_infra_default_creds()

    msg = str(exc_info.value)
    assert "MINIO_ROOT_USER" in msg
    assert "MINIO_ROOT_PASSWORD" in msg
    # Non-default credentials must NOT appear in the error message.
    assert "GF_SECURITY_ADMIN_USER" not in msg
    assert "GF_SECURITY_ADMIN_PASSWORD" not in msg


# ---------------------------------------------------------------------------
# Non-default credentials — no warning, no raise in either posture
# ---------------------------------------------------------------------------


def test_strong_creds_dev_posture_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-default credentials in dev posture must not trigger any warning."""
    from butlers.db import check_infra_default_creds

    monkeypatch.delenv("BUTLERS_POSTURE", raising=False)
    _set_strong_infra_creds(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()  # must NOT raise

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not warning_messages, f"Unexpected warnings with strong creds: {warning_messages}"


def test_strong_creds_hardened_posture_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-default credentials in hardened posture must not raise."""
    from butlers.db import check_infra_default_creds

    monkeypatch.setenv("BUTLERS_POSTURE", "hardened")
    _set_strong_infra_creds(monkeypatch)

    check_infra_default_creds()  # must NOT raise


# ---------------------------------------------------------------------------
# Absence-as-default semantics
# ---------------------------------------------------------------------------


def test_absent_cred_treated_as_default_in_hardened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent env var is treated as the known default (mirrors docker-compose :-fallback)."""
    from butlers.db import check_infra_default_creds

    monkeypatch.setenv("BUTLERS_POSTURE", "hardened")
    # Set all to strong except one absent — should still raise.
    monkeypatch.setenv("MINIO_ROOT_USER", "strong-user")
    monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)  # absent → default
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "grafana-ops")
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "str0ng-grafana-pw")

    with pytest.raises(RuntimeError, match="MINIO_ROOT_PASSWORD"):
        check_infra_default_creds()
