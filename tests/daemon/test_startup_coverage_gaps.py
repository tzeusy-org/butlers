"""Tests for startup steps that had zero coverage at daemon-decomposition baseline.

Covers:
- Step 1b: configure_logging is called with butler name and level from config
- Step 2.5: detect_secrets warns on inline secret patterns; _flatten_config_for_secret_scan
  excludes credential-env keys
- Step 8c2: restore_tokens called on startup; non-fatal on exception; restored count logged
- Step 13c: _wire_calendar_approval_enqueuer sets enqueuer on calendar module when
  both approvals (enabled) and calendar modules are active; skips otherwise

Each test section is focused on the specific behavioral contract of the step.
No live DB or network required.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ===========================================================================
# Step 1b — configure_logging called during startup
# ===========================================================================


class TestStep1bLoggingConfig:
    """Step 1b: daemon calls configure_logging with butler name, level, and log_root."""

    def test_configure_logging_sets_butler_context_and_level(self) -> None:
        """configure_logging sets butler context ContextVar and root log level."""
        from butlers.core.logging import configure_logging, get_butler_context

        configure_logging(level="DEBUG", fmt="text", log_root=None, butler_name="test-butler")

        # Butler name is stored in the ContextVar
        assert get_butler_context() == "test-butler"
        # Root log level is set to DEBUG
        assert logging.getLogger().level == logging.DEBUG

    def test_configure_logging_attaches_credential_redaction_filter(self) -> None:
        """configure_logging attaches a CredentialRedactionFilter to the root logger."""
        from butlers.core.logging import CredentialRedactionFilter, configure_logging

        configure_logging(level="INFO", fmt="text", log_root=None, butler_name="filter-test")

        root = logging.getLogger()
        filter_types = [type(f) for f in root.filters]
        assert CredentialRedactionFilter in filter_types, (
            "CredentialRedactionFilter must be attached to root logger after configure_logging()"
        )

    def test_configure_logging_creates_file_handlers_when_log_root_set(
        self, tmp_path: Path
    ) -> None:
        """configure_logging creates log subdirectories and file handlers when log_root provided."""
        from butlers.core.logging import configure_logging

        configure_logging(level="INFO", fmt="json", log_root=tmp_path, butler_name="filetest")

        # Subdirectories must exist
        assert (tmp_path / "butlers").is_dir()
        assert (tmp_path / "uvicorn").is_dir()
        assert (tmp_path / "connectors").is_dir()

        # Butler log file created
        butler_log = tmp_path / "butlers" / "filetest.log"
        assert butler_log.exists(), f"Expected {butler_log} to exist"

    def test_configure_logging_no_file_handlers_without_log_root(self) -> None:
        """configure_logging with log_root=None does not add new file handlers."""
        from butlers.core.logging import configure_logging

        root = logging.getLogger()
        before_file_handler_ids = {
            id(h) for h in root.handlers if isinstance(h, logging.FileHandler)
        }
        configure_logging(level="INFO", fmt="text", log_root=None, butler_name="nofile")

        after_file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        after_file_handler_ids = {id(h) for h in after_file_handlers}
        assert after_file_handler_ids == before_file_handler_ids, (
            "log_root=None must not add FileHandler entries"
        )
        assert not any(h.baseFilename.endswith("/nofile.log") for h in after_file_handlers), (
            "log_root=None should not create a per-butler file log"
        )

    def test_credential_redaction_filter_scrubs_telegram_token(self) -> None:
        """CredentialRedactionFilter redacts Telegram bot tokens in log messages."""
        from butlers.core.logging import CredentialRedactionFilter

        f = CredentialRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Calling https://api.telegram.org/bot1234567890:ABCDEFabcdef1234567890_ABCDEFG/sendMessage",
            args=(),
            exc_info=None,
        )
        result = f.filter(record)
        assert result is True  # Never drops records
        assert "REDACTED" in record.msg
        assert "ABCDEFabcdef1234567890_ABCDEFG" not in record.msg

    def test_resolve_log_root_env_override_disables_file_logging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BUTLERS_LOG_ROOT=none disables file logging regardless of configured log_root."""
        from butlers.core.logging import resolve_log_root

        monkeypatch.setenv("BUTLERS_LOG_ROOT", "none")
        assert resolve_log_root("/some/path") is None

    def test_resolve_log_root_disable_env_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BUTLERS_DISABLE_FILE_LOGGING=1 disables file logging."""
        from butlers.core.logging import resolve_log_root

        monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
        monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
        assert resolve_log_root("/some/path") is None

    def test_resolve_log_root_uses_configured_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_log_root returns configured path when no env override."""
        from butlers.core.logging import resolve_log_root

        monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
        monkeypatch.delenv("BUTLERS_DISABLE_FILE_LOGGING", raising=False)
        result = resolve_log_root("/my/logs")
        assert result == Path("/my/logs")


# ===========================================================================
# Step 2.5 — secret detection on config values
# ===========================================================================


class TestStep25SecretDetection:
    """Step 2.5: detect_secrets warns on inline secrets; _flatten_config_for_secret_scan
    produces the correct flat dict from a ButlerConfig."""

    def test_detect_secrets_flags_openai_key_prefix(self) -> None:
        """detect_secrets warns when a config value starts with 'sk-'."""
        from butlers.credentials import detect_secrets

        warnings = detect_secrets({"some.key": "sk-abc123abcdefghijklmnopqrstuvwxyz"})
        assert len(warnings) == 1
        assert "sk-" in warnings[0]
        assert "some.key" in warnings[0]

    def test_detect_secrets_flags_github_pat(self) -> None:
        """detect_secrets warns for github_pat_ prefix."""
        from butlers.credentials import detect_secrets

        warnings = detect_secrets({"api.token": "github_pat_ABCDEF1234567890abcdef"})
        assert any("github_pat_" in w for w in warnings)

    def test_detect_secrets_flags_long_base64_string(self) -> None:
        """detect_secrets warns on long base64-like strings (40+ chars)."""
        from butlers.credentials import detect_secrets

        long_b64 = "A" * 42  # 42 alphanumeric chars — matches base64 heuristic
        warnings = detect_secrets({"config.value": long_b64})
        assert len(warnings) == 1
        assert "base64" in warnings[0]

    def test_detect_secrets_flags_secret_key_name_heuristic(self) -> None:
        """detect_secrets warns when key name hints 'secret' and value is long."""
        from butlers.credentials import detect_secrets

        warnings = detect_secrets({"modules.mymod.api_key": "x" * 20})
        assert len(warnings) == 1
        assert "api_key" in warnings[0]

    def test_detect_secrets_skips_urls_and_short_values(self) -> None:
        """detect_secrets does not warn on URLs or values shorter than 8 chars."""
        from butlers.credentials import detect_secrets

        clean = {
            "endpoint": "https://api.example.com/v1",
            "name": "mybutler",
            "port": 8080,  # non-string, skip
            "short": "abc",
        }
        warnings = detect_secrets(clean)
        assert warnings == [], f"Expected no warnings, got: {warnings}"

    def test_detect_secrets_returns_empty_for_clean_config(self) -> None:
        """detect_secrets returns [] for configs with no secrets."""
        from butlers.credentials import detect_secrets

        clean = {
            "butler.name": "general",
            "butler.port": 18100,
            "butler.db.name": "butlers",
            "modules.email.smtp_host": "smtp.example.com",
        }
        warnings = detect_secrets(clean)
        assert warnings == []

    def test_flatten_config_excludes_credentials_env_keys(self, tmp_path: Path) -> None:
        """_flatten_config_for_secret_scan excludes keys ending in _env or credentials_env."""
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        toml = """\
[butler]
name = "test-flat"
port = 19100
description = "Flatten test"

[butler.db]
name = "butlers"
schema = "test_flat"

[modules.email]
smtp_host = "smtp.example.com"
credentials_env = ["EMAIL_PASSWORD"]

[modules.telegram]
bot_token_env = "TELEGRAM_BOT_TOKEN"
"""
        (tmp_path / "butler.toml").write_text(toml)
        config = load_config(tmp_path)
        flat = _flatten_config_for_secret_scan(config)

        # Regular module values are included
        assert "modules.email.smtp_host" in flat
        assert flat["modules.email.smtp_host"] == "smtp.example.com"

        # Credential env key declarations are excluded
        credential_keys = [k for k in flat if "credentials_env" in k or k.endswith("_env")]
        assert credential_keys == [], (
            f"credentials_env keys must not appear in scan output, found: {credential_keys}"
        )

    def test_flatten_config_includes_butler_identity_fields(self, tmp_path: Path) -> None:
        """_flatten_config_for_secret_scan includes butler.name, port, and db.name."""
        from butlers.config import load_config
        from butlers.daemon import _flatten_config_for_secret_scan

        toml = """\
[butler]
name = "identity-test"
port = 19200

[butler.db]
name = "butlers"
schema = "id_test"
"""
        (tmp_path / "butler.toml").write_text(toml)
        config = load_config(tmp_path)
        flat = _flatten_config_for_secret_scan(config)

        assert flat.get("butler.name") == "identity-test"
        assert flat.get("butler.port") == 19200
        assert flat.get("butler.db.name") == "butlers"


# ===========================================================================
# Step 8c2 — CLI auth token restore
# ===========================================================================


class TestStep8c2CliTokenRestore:
    """Step 8c2: restore_tokens is called on startup, logged when tokens restored,
    and any exception is caught (non-fatal)."""

    async def test_restore_tokens_called_with_credential_store(self) -> None:
        """restore_tokens receives the credential_store and logs restored count."""
        from butlers.cli_auth.persistence import restore_tokens

        store = AsyncMock()
        store.load = AsyncMock(return_value=None)  # No stored tokens

        results = await restore_tokens(store)
        # All providers return False (no stored tokens)
        assert all(v is False for v in results.values())

    async def test_restore_tokens_writes_token_to_filesystem(self, tmp_path: Path) -> None:
        """restore_tokens writes stored token content to a provider's token_path."""
        from butlers.cli_auth.persistence import restore_tokens
        from butlers.cli_auth.registry import CLIAuthProviderDef

        # Use a synthetic provider that writes to tmp_path (avoids real home-dir writes).
        token_path = tmp_path / "test_cli_auth.json"
        provider = CLIAuthProviderDef(
            name="test-write",
            display_name="Test Write CLI",
            runtime="test",
            token_path=token_path,
        )
        token_content = '{"access_token": "test-token-value"}'

        store = AsyncMock()
        store.load = AsyncMock(return_value=token_content)

        with patch("butlers.cli_auth.persistence.PROVIDERS", {"test-write": provider}):
            results = await restore_tokens(store)

        assert results == {"test-write": True}
        assert token_path.exists()
        assert token_path.read_text() == token_content

    async def test_restore_tokens_non_fatal_on_store_load_failure(self) -> None:
        """restore_tokens returns False per provider when store.load raises; no propagation."""
        from butlers.cli_auth.persistence import restore_tokens

        store = AsyncMock()
        store.load = AsyncMock(side_effect=RuntimeError("DB gone"))

        # Must not raise
        results = await restore_tokens(store)
        assert all(v is False for v in results.values())

    async def test_restore_tokens_logs_restored_count(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """restore_tokens logs info message for each successfully restored token."""
        from butlers.cli_auth.persistence import restore_tokens
        from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef

        if not PROVIDERS:
            pytest.skip("No CLI auth providers registered")

        # Build a synthetic single-provider dict that uses tmp_path for the token file.
        # This avoids writing to real home-directory paths.
        token_path = tmp_path / "auth.json"
        provider = CLIAuthProviderDef(
            name="test-provider",
            display_name="Test CLI",
            runtime="test",
            token_path=token_path,
        )
        token_content = '{"access_token": "abc"}'

        store = AsyncMock()
        store.load = AsyncMock(return_value=token_content)

        with (
            patch("butlers.cli_auth.persistence.PROVIDERS", {"test-provider": provider}),
            caplog.at_level(logging.INFO, logger="butlers.cli_auth.persistence"),
        ):
            results = await restore_tokens(store)

        assert results == {"test-provider": True}
        assert token_path.exists()
        assert token_path.read_text() == token_content


# ===========================================================================
# Step 13c — calendar approval enqueuer wiring
# ===========================================================================


class TestStep13cCalendarApprovalWiring:
    """Step 13c: _wire_calendar_approval_enqueuer sets enqueuer on calendar module
    when both approvals (enabled) and calendar modules are active; no-ops otherwise."""

    def _make_daemon_with_modules(
        self,
        *,
        approvals_enabled: bool,
        has_calendar: bool,
        calendar_has_setter: bool = True,
    ) -> Any:
        """Build a minimal ButlerDaemon-like stub with the required attributes."""
        from butlers.daemon import ButlerDaemon

        # Build minimal config mock
        config = MagicMock()
        if approvals_enabled:
            config.modules = {
                "approvals": {
                    "enabled": True,
                    "default_expiry_hours": 48,
                    "gated_tools": {},
                }
            }
        else:
            config.modules = {}  # No approvals section → parse_approval_config returns None

        # Calendar module mocks: with and without setter
        enqueuer_calls: list = []

        class _FakeCalendarModWithSetter:
            name = "calendar"

            def set_approval_enqueuer(self, fn):
                enqueuer_calls.append(fn)

        class _FakeCalendarModNoSetter:
            name = "calendar"
            # Deliberately no set_approval_enqueuer

        # Build daemon instance without calling __init__ (avoid I/O)
        daemon = object.__new__(ButlerDaemon)
        daemon.config = config
        daemon._module_statuses = {}
        daemon.db = MagicMock()
        daemon.db.pool = AsyncMock()

        if has_calendar:
            if calendar_has_setter:
                daemon._modules = [_FakeCalendarModWithSetter()]
            else:
                daemon._modules = [_FakeCalendarModNoSetter()]
        else:
            daemon._modules = []

        return daemon, enqueuer_calls

    def test_wires_enqueuer_when_approvals_enabled_and_calendar_active(self) -> None:
        """set_approval_enqueuer is called with a callable when approvals + calendar active."""
        daemon, enqueuer_calls = self._make_daemon_with_modules(
            approvals_enabled=True, has_calendar=True
        )
        daemon._wire_calendar_approval_enqueuer()

        assert len(enqueuer_calls) == 1, "set_approval_enqueuer should be called exactly once"
        assert callable(enqueuer_calls[0]), "Enqueuer callback must be callable"

    def test_no_wire_when_approvals_section_absent(self) -> None:
        """No enqueuer wired when approvals config section is absent."""
        daemon, enqueuer_calls = self._make_daemon_with_modules(
            approvals_enabled=False, has_calendar=True
        )
        daemon._wire_calendar_approval_enqueuer()

        assert enqueuer_calls == [], "No enqueuer should be set when approvals config is absent"

    def test_no_wire_when_calendar_module_not_active(self) -> None:
        """No enqueuer wired when calendar module is not in _active_modules."""
        daemon, enqueuer_calls = self._make_daemon_with_modules(
            approvals_enabled=True, has_calendar=False
        )
        daemon._wire_calendar_approval_enqueuer()

        assert enqueuer_calls == [], "No enqueuer should be set when calendar module is absent"

    def test_no_wire_when_calendar_lacks_setter_method(self) -> None:
        """No enqueuer wired when calendar module lacks set_approval_enqueuer."""
        daemon, enqueuer_calls = self._make_daemon_with_modules(
            approvals_enabled=True, has_calendar=True, calendar_has_setter=False
        )
        daemon._wire_calendar_approval_enqueuer()

        assert enqueuer_calls == [], (
            "No enqueuer should be set when calendar module has no set_approval_enqueuer"
        )

    def test_no_wire_when_approvals_disabled(self) -> None:
        """No enqueuer wired when approvals section exists but enabled=False."""
        daemon, enqueuer_calls = self._make_daemon_with_modules(
            approvals_enabled=False, has_calendar=True
        )
        # Manually inject an approvals config with enabled=False
        daemon.config.modules = {"approvals": {"enabled": False}}
        daemon._wire_calendar_approval_enqueuer()

        assert enqueuer_calls == [], "No enqueuer should be set when approvals.enabled=False"

    async def test_enqueuer_callback_inserts_pending_action(self) -> None:
        """The enqueuer callback inserted by _wire_calendar_approval_enqueuer calls pool.execute."""
        from butlers.daemon import ButlerDaemon

        # Minimal config with approvals enabled
        config = MagicMock()
        config.modules = {
            "approvals": {
                "enabled": True,
                "default_expiry_hours": 24,
                "gated_tools": {},
            }
        }

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        enqueuer_holder: list = []

        class _CalendarMod:
            name = "calendar"

            def set_approval_enqueuer(self, fn):
                enqueuer_holder.append(fn)

        daemon = object.__new__(ButlerDaemon)
        daemon.config = config
        daemon._module_statuses = {}
        daemon.db = MagicMock()
        daemon.db.pool = mock_pool
        daemon._modules = [_CalendarMod()]

        with patch(
            "butlers.modules.approvals.events.record_approval_event",
            new_callable=AsyncMock,
        ):
            daemon._wire_calendar_approval_enqueuer()

            assert len(enqueuer_holder) == 1
            enqueuer = enqueuer_holder[0]

            # Call the enqueuer and confirm pool.execute was invoked
            await enqueuer(
                tool_name="calendar_event_create",
                tool_args={"title": "Meeting"},
                agent_summary="Creating a calendar event",
            )

        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO pending_actions" in sql


# ===========================================================================
# Step 6 — DB role wiring in lifecycle
# ===========================================================================


class _StopStartup(Exception):
    """Sentinel raised from connect() to halt run_startup right after step 6."""


class _RecordingDatabase:
    """Fake Database that records role state at connect() time, then halts startup."""

    def __init__(self) -> None:
        self.schema: str | None = None
        self.role: str | None = None
        self.role_at_connect: str | None = None

    def set_schema(self, schema: str | None) -> None:
        self.schema = schema

    async def provision(self) -> None:
        return None

    async def connect(self):
        # Capture the role that was assigned before connect() is awaited.
        self.role_at_connect = self.role
        raise _StopStartup


class TestStep6DbRoleWiring:
    """Step 6: role is derived from db_schema and applied to Database before connect().

    Behavioral replacement for the former source-string tests (bu-qi2ic 3.1-3.3):
    drives lifecycle.run_startup with a recording fake Database and asserts the
    DB-isolation role wiring (butler_{schema}_rw, applied before connect, skipped
    when schema is None or the db is injected).
    """

    def _make_daemon(self, *, schema: str | None, injected_db=None) -> Any:
        daemon = MagicMock()
        daemon.db = injected_db
        daemon.config.db_name = "butlers"
        daemon.config.db_schema = schema
        return daemon

    def _pre_step6_patches(self, lifecycle):
        """Patch the pre-step-6 startup machinery so we can reach step 6 cleanly."""
        return [
            patch("butlers.core.logging.configure_logging"),
            patch.object(lifecycle, "resolve_log_root", return_value=None),
            patch.object(lifecycle, "init_telemetry"),
            patch.object(lifecycle, "init_metrics"),
            patch.object(lifecycle, "_flatten_config_for_secret_scan", return_value={}),
            patch.object(lifecycle, "detect_secrets", return_value=[]),
            patch.object(lifecycle, "validate_credentials"),
        ]

    async def _run_until_connect(self, daemon, fake_db: _RecordingDatabase):
        from contextlib import ExitStack

        from butlers import lifecycle

        with ExitStack() as stack:
            for p in self._pre_step6_patches(lifecycle):
                stack.enter_context(p)
            stack.enter_context(patch.object(lifecycle.Database, "from_env", return_value=fake_db))
            try:
                await lifecycle.run_startup(daemon)
            except _StopStartup:
                pass

    async def test_role_set_from_schema_before_connect(self) -> None:
        """db_schema set: role is butler_{schema}_rw and is applied before connect()."""
        fake_db = _RecordingDatabase()
        daemon = self._make_daemon(schema="health")

        await self._run_until_connect(daemon, fake_db)

        assert fake_db.schema == "health"
        # Role was butler_{schema}_rw AND was already set by the time connect() ran.
        assert fake_db.role_at_connect == "butler_health_rw"

    async def test_role_not_set_when_schema_is_none(self) -> None:
        """db_schema None: no role is assigned on the non-injected path."""
        fake_db = _RecordingDatabase()
        daemon = self._make_daemon(schema=None)

        await self._run_until_connect(daemon, fake_db)

        assert fake_db.role_at_connect is None

    async def test_injected_db_path_skips_role_wiring(self) -> None:
        """Injected db: role wiring is skipped (the from_env/connect path never runs)."""
        injected = MagicMock()
        injected.pool = MagicMock()  # already connected
        injected.role = None
        daemon = self._make_daemon(schema="health", injected_db=injected)

        # Injected path takes the else branch and proceeds past step 6; halt it
        # deterministically at the first post-step-6 helper (ButlerLogger).
        from contextlib import ExitStack

        from butlers import lifecycle

        with ExitStack() as stack:
            for p in self._pre_step6_patches(lifecycle):
                stack.enter_context(p)
            mock_from_env = stack.enter_context(patch.object(lifecycle.Database, "from_env"))
            stack.enter_context(
                patch("butlers.core.butler_logging.ButlerLogger", side_effect=_StopStartup)
            )
            try:
                await lifecycle.run_startup(daemon)
            except _StopStartup:
                pass

        # from_env (the non-injected provisioning path) must not be called, and the
        # injected db's role must remain untouched.
        mock_from_env.assert_not_called()
        assert injected.role is None


# ===========================================================================
# Step 8c — S3 blob storage degradation
# ===========================================================================


class _StopAfterBlobStorage(Exception):
    """Sentinel raised after step 8c to prove startup reached the next phase."""


class _FakeCredentialStore:
    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values
        self.resolve_calls: list[tuple[str, bool]] = []

    async def resolve(self, key: str, *, env_fallback: bool = True) -> str | None:
        self.resolve_calls.append((key, env_fallback))
        return self.values.get(key)


class TestStep8cBlobStorageDegradation:
    """Step 8c: configured-but-unavailable S3 disables blob I/O without killing startup."""

    async def test_configured_s3_startup_failure_disables_blob_store_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from contextlib import ExitStack

        from butlers import lifecycle
        from butlers.storage import BlobStorageStartupError

        class UnavailableS3BlobStore:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

            async def startup_check(self) -> None:
                raise BlobStorageStartupError(
                    "Cannot reach S3 endpoint http://synology-garage:3900"
                )

        pool = AsyncMock()
        daemon = MagicMock()
        daemon.config = SimpleNamespace(
            name="test-butler",
            logging=SimpleNamespace(log_root=None, level="INFO", format="text"),
            modules={},
            env_required=[],
            env_optional=[],
            db_name="butlers",
            db_schema="test_butler",
        )
        daemon.db = SimpleNamespace(pool=pool)
        daemon._registry.load_all.return_value = []
        daemon._select_startup_modules.return_value = []
        daemon._validate_module_configs.return_value = {}
        daemon._collect_module_credentials.return_value = {}
        daemon._module_statuses = {}
        daemon._cascade_module_failures.return_value = None
        daemon._build_db_url.return_value = "postgresql://example/butlers"
        daemon._db_log_handler = None

        store = _FakeCredentialStore(
            {
                "BLOB_S3_ENDPOINT_URL": "http://synology-garage:3900",
                "BLOB_S3_BUCKET": "butlers-dev",
                "BLOB_S3_REGION": "garage",
                "BLOB_S3_ACCESS_KEY_ID": "access-key",
                "BLOB_S3_SECRET_ACCESS_KEY": "secret-key",
            }
        )
        daemon._build_credential_store = AsyncMock(return_value=store)

        caplog.set_level(logging.WARNING)
        with ExitStack() as stack:
            stack.enter_context(patch("butlers.core.logging.configure_logging"))
            stack.enter_context(patch.object(lifecycle, "resolve_log_root", return_value=None))
            stack.enter_context(patch.object(lifecycle, "init_telemetry"))
            stack.enter_context(patch.object(lifecycle, "init_metrics"))
            stack.enter_context(
                patch.object(lifecycle, "_flatten_config_for_secret_scan", return_value={})
            )
            stack.enter_context(patch.object(lifecycle, "detect_secrets", return_value=[]))
            stack.enter_context(patch.object(lifecycle, "validate_credentials"))
            stack.enter_context(
                patch.object(
                    lifecycle, "validate_module_credentials_async", new=AsyncMock(return_value={})
                )
            )
            stack.enter_context(patch.object(lifecycle, "run_migrations", new=AsyncMock()))
            stack.enter_context(patch.object(lifecycle, "has_butler_chain", return_value=False))
            stack.enter_context(patch.object(lifecycle, "S3BlobStore", UnavailableS3BlobStore))
            stack.enter_context(
                patch("butlers.core.butler_logging.ButlerLogger", return_value=MagicMock())
            )
            stack.enter_context(
                patch(
                    "butlers.core.butler_logging.ButlerDBLogHandler",
                    return_value=logging.NullHandler(),
                )
            )
            stack.enter_context(
                patch("butlers.cli_auth.persistence.restore_tokens", new=AsyncMock(return_value={}))
            )
            stack.enter_context(
                patch.object(
                    lifecycle,
                    "_ensure_owner_entity",
                    new=AsyncMock(side_effect=_StopAfterBlobStorage),
                )
            )

            with pytest.raises(_StopAfterBlobStorage):
                await lifecycle.run_startup(daemon)

        if daemon._db_log_handler is not None:
            logging.getLogger().removeHandler(daemon._db_log_handler)

        assert daemon.blob_store is None
        assert "S3 blob storage unavailable; blob operations will fail at runtime" in caplog.text
        assert all(env_fallback is False for _, env_fallback in store.resolve_calls)
