"""Known-default diagnostics remain independent of runtime role authority."""

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


def _own_warning_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return only this module's own INSECURE-DEFAULT warning messages.

    ``caplog.at_level(level, logger="butlers.db")`` narrows the *level* of the
    named logger, but pytest's capture handler is still attached at the ROOT
    logger — so ``caplog.records`` picks up WARNING+ records from *any*
    logger that propagates to root during the ``with`` block (e.g. a stray
    "no running event loop" line from ``butlers.core.butler_logging`` left
    over by an unrelated test's global logging state; see bu-lvoqq). Filter
    to records actually emitted by ``butlers.db`` so cross-test bleed cannot
    inflate the count.
    """
    return [
        r.message for r in caplog.records if r.levelno == logging.WARNING and r.name == "butlers.db"
    ]


# ---------------------------------------------------------------------------
# Dev-posture tests
# ---------------------------------------------------------------------------


def test_known_defaults_dev_posture_warns_not_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Known-default infra creds under the retained diagnostic must warn, never raise."""
    from butlers.db import check_infra_default_creds

    _clear_infra_creds(monkeypatch)  # absent → treated as default

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()  # must NOT raise

    # At least one WARNING logged for MinIO and Grafana defaults.
    warning_messages = _own_warning_messages(caplog)
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

    monkeypatch.setenv("MINIO_ROOT_USER", "minioadmin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "admin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "admin")

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()  # must NOT raise

    warning_messages = _own_warning_messages(caplog)
    assert len(warning_messages) == 4, (
        f"Expected 4 warnings (one per credential), got {len(warning_messages)}: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# Hardened-posture tests
# ---------------------------------------------------------------------------


def test_known_defaults_warn_without_role_posture(monkeypatch, caplog) -> None:
    from butlers.db import check_infra_default_creds

    _clear_infra_creds(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()
    assert len(_own_warning_messages(caplog)) == 4


def test_default_warning_names_only_offending_service_keys(monkeypatch, caplog) -> None:
    from butlers.db import check_infra_default_creds

    monkeypatch.setenv("MINIO_ROOT_USER", "minioadmin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "grafana-ops")
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "synthetic-private-password")
    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()
    msg = "\n".join(_own_warning_messages(caplog))
    assert "MINIO_ROOT_USER" in msg
    assert "MINIO_ROOT_PASSWORD" in msg
    assert "GF_SECURITY_ADMIN_USER" not in msg
    assert "GF_SECURITY_ADMIN_PASSWORD" not in msg
    assert "minioadmin" not in msg
    assert "synthetic-private-password" not in msg


# ---------------------------------------------------------------------------
# Non-default credentials — no warning, no raise in either posture
# ---------------------------------------------------------------------------


def test_strong_creds_dev_posture_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-default credentials in the retained diagnostic must not trigger any warning."""
    from butlers.db import check_infra_default_creds

    _set_strong_infra_creds(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()  # must NOT raise

    warning_messages = _own_warning_messages(caplog)
    assert not warning_messages, f"Unexpected warnings with strong creds: {warning_messages}"


def test_strong_creds_hardened_posture_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-default credentials in the retained diagnostic must not raise."""
    from butlers.db import check_infra_default_creds

    _set_strong_infra_creds(monkeypatch)

    check_infra_default_creds()  # must NOT raise


# ---------------------------------------------------------------------------
# Absence-as-default semantics
# ---------------------------------------------------------------------------


def test_absent_compose_credential_retains_default_diagnostic(monkeypatch, caplog) -> None:
    from butlers.db import check_infra_default_creds

    _set_strong_infra_creds(monkeypatch)
    monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)
    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        check_infra_default_creds()
    messages = _own_warning_messages(caplog)
    assert len(messages) == 1
    assert "MINIO_ROOT_PASSWORD" in messages[0]
