"""Email module — email MCP tools.

Uses IMAP for inbox access and SMTP for sending.
Configured via [modules.email] with optional
[modules.email.user] and [modules.email.bot] credential scopes in butler.toml.

Email ingestion is handled by GmailConnector via the connector-based pipeline.
"""

from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import logging
import re
import smtplib
from email.mime.text import MIMEText
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.core.audit import write_audit_entry
from butlers.core.permissions import EMAIL_SEND_PERMISSION, require_permission
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_env_var_name(value: str, *, scope: str, field_name: str) -> str:
    """Validate a configured env var name for an identity credential field."""
    if not value or not value.strip():
        raise ValueError(
            f"modules.email.{scope}.{field_name} must be a non-empty environment variable name"
        )
    name = value.strip()
    if not _ENV_VAR_NAME_RE.fullmatch(name):
        raise ValueError(
            f"modules.email.{scope}.{field_name} must be a valid environment variable name "
            "(letters, numbers, underscores; cannot start with a number)"
        )
    return name


class EmailUserCredentialsConfig(BaseModel):
    """Identity-scoped credentials for user mailbox operations."""

    enabled: bool = False
    address_env: str = "USER_EMAIL_ADDRESS"
    password_env: str = "USER_EMAIL_PASSWORD"
    model_config = ConfigDict(extra="forbid")

    @field_validator("address_env")
    @classmethod
    def _validate_address_env(cls, value: str) -> str:
        return _validate_env_var_name(value, scope="user", field_name="address_env")

    @field_validator("password_env")
    @classmethod
    def _validate_password_env(cls, value: str) -> str:
        return _validate_env_var_name(value, scope="user", field_name="password_env")


class EmailBotCredentialsConfig(BaseModel):
    """Identity-scoped credentials for bot mailbox operations."""

    enabled: bool = True
    address_env: str = "BUTLER_EMAIL_ADDRESS"
    password_env: str = "BUTLER_EMAIL_PASSWORD"
    model_config = ConfigDict(extra="forbid")

    @field_validator("address_env")
    @classmethod
    def _validate_address_env(cls, value: str) -> str:
        return _validate_env_var_name(value, scope="bot", field_name="address_env")

    @field_validator("password_env")
    @classmethod
    def _validate_password_env(cls, value: str) -> str:
        return _validate_env_var_name(value, scope="bot", field_name="password_env")


class EmailConfig(BaseModel):
    """Configuration for the Email module.

    By default only read/search tools are registered (``send_tools = false``).
    Set ``send_tools = true`` in butler.toml to enable ``email_send_message``
    and ``email_reply_to_thread``.  Only the Messenger butler should enable
    send tools — all other butlers use ``notify()`` for outbound delivery.
    """

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    use_tls: bool = True
    send_tools: bool = False
    user: EmailUserCredentialsConfig = Field(default_factory=EmailUserCredentialsConfig)
    bot: EmailBotCredentialsConfig = Field(default_factory=EmailBotCredentialsConfig)
    model_config = ConfigDict(extra="forbid")


class EmailModule(Module):
    """Email module providing email MCP tools.

    Provides send, reply, search, and read tools for email via IMAP/SMTP.
    Email ingestion is handled by ``GmailConnector`` via the connector-based
    pipeline.
    """

    def __init__(self) -> None:
        self._config: EmailConfig = EmailConfig()
        self._audit_pool: Any = None
        self._butler_name: str = "email"
        # Credentials cached at startup: user-scope from owner entity_info,
        # bot-scope from CredentialStore.
        self._resolved_credentials: dict[str, str] = {}
        # DB pool for OAuth status queries (public.google_accounts).
        self._pool: Any = None

    @property
    def name(self) -> str:
        return "email"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmailConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        """Environment variables required for email authentication."""
        envs: list[str] = []
        if self._config.bot.enabled:
            envs.extend([self._config.bot.address_env, self._config.bot.password_env])
        # User-scope credentials come from owner entity_info, not butler_secrets.
        return envs

    def migration_revisions(self) -> str | None:
        return None  # No custom tables needed

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Declare the recipient address as a safety-critical argument.

        The ``to`` argument controls where an outbound email is delivered, so a
        standing approval rule may only auto-approve a send when it pins ``to``
        to an exact value or pattern.  A rule that leaves ``to`` unconstrained
        (``any``) cannot blanket-approve sends to arbitrary recipients — the
        approval gate parks such calls for explicit owner approval.
        """
        return {
            "email_send_message": ToolMeta(arg_sensitivities={"to": True}),
            "email_reply_to_thread": ToolMeta(arg_sensitivities={"to": True}),
        }

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register email MCP tools.

        Send/reply tools are only registered when ``send_tools = true`` in
        the module config.  This ensures that only the Messenger butler
        (which has approval gates) can send outbound emails directly.
        All other butlers use ``notify()`` for outbound delivery.
        """
        self._config = config if isinstance(config, EmailConfig) else EmailConfig(**(config or {}))
        self._butler_name = butler_name or self._butler_name
        module = self  # capture for closures

        if self._config.send_tools:

            @mcp.tool()
            async def email_send_message(to: str, subject: str, body: str) -> dict:
                """Send an email via SMTP."""
                return await module._send_email(to, subject, body)

            @mcp.tool()
            async def email_reply_to_thread(
                to: str,
                thread_id: str,
                body: str,
                subject: str | None = None,
            ) -> dict:
                """Reply to an email thread."""
                return await module._reply_to_thread(to, thread_id, body, subject)

        @mcp.tool()
        async def email_search_inbox(query: str) -> list[dict]:
            """Search inbox via IMAP SEARCH."""
            return await module._search_inbox(query)

        @mcp.tool()
        async def email_read_message(message_id: str) -> dict:
            """Read a specific email by message ID."""
            return await module._read_email(message_id)

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Initialize email config and resolve credentials.

        User-scope credentials (USER_EMAIL_ADDRESS, USER_EMAIL_PASSWORD) are
        resolved exclusively from the owner entity's ``public.entity_info``
        entries (types ``email`` and ``email_password``).  Bot-scope credentials
        are resolved via :class:`~butlers.credential_store.CredentialStore`.

        All resolved values are cached in ``self._resolved_credentials`` so
        that sync IO helpers can use them without needing to be async.
        """
        from butlers.credential_store import resolve_owner_entity_info

        self._config = config if isinstance(config, EmailConfig) else EmailConfig(**(config or {}))
        self._resolved_credentials = {}

        # --- User-scope: resolve exclusively from owner entity_info ----------
        pool = getattr(db, "pool", None) if db is not None else None
        self._pool = pool  # Retained for OAuth status queries (public.google_accounts).
        if pool is not None:
            user_cfg = self._config.user
            # contact_info type → env var key used by _get_credentials
            _CI_MAP = {
                "email": user_cfg.address_env,
                "email_password": user_cfg.password_env,
            }
            for ci_type, env_key in _CI_MAP.items():
                value = await resolve_owner_entity_info(pool, ci_type)
                if value is not None:
                    self._resolved_credentials[env_key] = value

        # --- Bot-scope only: CredentialStore ----------------------------------
        if credential_store is not None:
            bot_cfg = self._config.bot
            for env_key in (bot_cfg.address_env, bot_cfg.password_env):
                if env_key not in self._resolved_credentials:
                    value = await credential_store.resolve(env_key)
                    if value is not None:
                        self._resolved_credentials[env_key] = value

    async def on_shutdown(self) -> None:
        """No persistent connections to clean up."""

    async def extra_status_fields(self) -> dict[str, Any]:
        """Return OAuth/credential health fields for the email module status entry.

        Queries ``public.google_accounts`` for the primary account's OAuth
        status and maps it to the ``oauth_status`` and ``credential_health``
        fields consumed by the dashboard Config tab.

        Returns ``{}`` gracefully when the DB pool is unavailable or the
        google_accounts table does not exist (e.g. during tests or early
        bootstrap).

        Mapping from ``public.google_accounts.status``:

        - ``'active'``         → ``oauth_status='granted'``, ``credential_health='ok'``
        - ``'revoked'``/``'expired'`` → ``oauth_status='reauth_needed'``,
          ``credential_health='error'``
        - no primary account  → ``oauth_status='not_configured'``,
          ``credential_health='warning'``
        """
        if self._pool is None:
            return {}
        try:
            row = await self._pool.fetchrow(
                "SELECT status FROM public.google_accounts WHERE is_primary = true LIMIT 1"
            )
        except Exception:
            logger.debug("extra_status_fields: google_accounts query failed", exc_info=True)
            return {}

        if row is None:
            return {
                "oauth_status": "not_configured",
                "credential_health": "warning",
            }

        account_status = row["status"]
        if account_status == "active":
            return {
                "oauth_status": "granted",
                "credential_health": "ok",
            }
        # 'revoked' or 'expired' → needs re-auth
        return {
            "oauth_status": "reauth_needed",
            "credential_health": "error",
        }

    def wire_audit_pool(self, audit_pool: Any) -> None:
        """Store the switchboard audit pool for gmail_send egress audit entries."""
        self._audit_pool = audit_pool

    # ------------------------------------------------------------------
    # Implementation helpers using stdlib imaplib/smtplib
    # ------------------------------------------------------------------

    def _get_credentials(self, *, scope: str = "bot") -> tuple[str, str]:
        """Resolve email credentials from startup-cached store.

        Credentials are pre-resolved at startup: user-scope from owner
        contact_info, bot-scope from CredentialStore.

        Raises ``RuntimeError`` if credentials are unavailable.
        """
        scope_cfg = self._config.bot if scope == "bot" else self._config.user
        if not scope_cfg.enabled:
            raise RuntimeError(f"Email credential scope modules.email.{scope} is disabled")

        address_env = scope_cfg.address_env
        password_env = scope_cfg.password_env
        address = self._resolved_credentials.get(address_env)
        password = self._resolved_credentials.get(password_env)
        if not address or not password:
            raise RuntimeError(
                f"Missing email credentials for modules.email.{scope}: "
                f"configure via owner entity_info (user-scope) "
                f"or butler_secrets (bot-scope)"
            )
        return address, password

    def _smtp_send(self, to: str, subject: str, body: str) -> dict:
        """Blocking SMTP send — intended to be run via ``asyncio.to_thread``."""
        address, password = self._get_credentials()

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = address
        msg["To"] = to

        if self._config.use_tls:
            server = smtplib.SMTP(self._config.smtp_host, self._config.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(self._config.smtp_host, self._config.smtp_port)

        try:
            server.login(address, password)
            server.sendmail(address, [to], msg.as_string())
        finally:
            server.quit()

        logger.info("Email sent to %s: %s", to, subject)
        return {"status": "sent", "to": to, "subject": subject}

    def _imap_search(self, query: str) -> list[dict]:
        """Blocking IMAP search — intended to be run via ``asyncio.to_thread``."""
        address, password = self._get_credentials()

        if self._config.use_tls:
            conn = imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)
        else:
            conn = imaplib.IMAP4(self._config.imap_host, self._config.imap_port)

        try:
            conn.login(address, password)
            conn.select("INBOX")

            # Use the query as an IMAP SEARCH criterion
            _status, data = conn.search(None, query)
            message_ids = data[0].split() if data[0] else []

            results: list[dict] = []
            for msg_id in message_ids[-50:]:  # Limit to 50 most recent
                _status, msg_data = conn.fetch(msg_id, "(RFC822.HEADER)")
                if msg_data[0] is None:
                    continue
                raw_header = msg_data[0][1]
                parsed = email_lib.message_from_bytes(raw_header)
                results.append(
                    {
                        "message_id": msg_id.decode(),
                        "from": parsed.get("From", ""),
                        "subject": parsed.get("Subject", ""),
                        "date": parsed.get("Date", ""),
                    }
                )
        finally:
            conn.logout()

        return results

    def _imap_read(self, message_id: str) -> dict:
        """Blocking IMAP fetch — intended to be run via ``asyncio.to_thread``."""
        address, password = self._get_credentials()

        if self._config.use_tls:
            conn = imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)
        else:
            conn = imaplib.IMAP4(self._config.imap_host, self._config.imap_port)

        try:
            conn.login(address, password)
            conn.select("INBOX")

            _status, msg_data = conn.fetch(message_id.encode(), "(RFC822)")
            if msg_data[0] is None:
                return {"error": f"Message {message_id} not found"}

            raw_msg = msg_data[0][1]
            parsed = email_lib.message_from_bytes(raw_msg)

            # Extract body
            body = ""
            if parsed.is_multipart():
                for part in parsed.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="replace")
                        break
            else:
                payload = parsed.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="replace")

            return {
                "message_id": message_id,
                "from": parsed.get("From", ""),
                "to": parsed.get("To", ""),
                "subject": parsed.get("Subject", ""),
                "date": parsed.get("Date", ""),
                "rfc_message_id": parsed.get("Message-ID", ""),
                "body": body,
            }
        finally:
            conn.logout()

    async def _send_email(self, to: str, subject: str, body: str) -> dict:
        """Send email via SMTP. Uses asyncio.to_thread for blocking SMTP calls.

        Permissions-matrix enforcement (public.permissions: email.send): the
        Settings → Permissions matrix governs whether this butler may send email
        on the owner's behalf. A cell flipped to granted=false blocks the send
        outright via :class:`PermissionDenied` (an authorization decision).
        Mirrors the spawn gate: consult the matrix at the decision point before
        any SMTP traffic. require_permission fails open, so a DB error never
        wedges delivery. Gating ``_send_email`` covers both the
        ``email_send_message`` MCP tool and the messenger route.execute path
        (which calls ``module._send_email`` directly).
        """
        _perm_pool = self._pool or self._audit_pool
        await require_permission(_perm_pool, self._butler_name, EMAIL_SEND_PERMISSION)
        try:
            result = await asyncio.to_thread(self._smtp_send, to, subject, body)
        except Exception as exc:
            await write_audit_entry(
                self._audit_pool,
                self._butler_name,
                "gmail_send",
                {"to": to, "subject": subject},
                result="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        await write_audit_entry(
            self._audit_pool,
            self._butler_name,
            "gmail_send",
            {"to": to, "subject": subject},
        )
        return result

    async def _reply_to_thread(
        self,
        to: str,
        thread_id: str,
        body: str,
        subject: str | None = None,
    ) -> dict:
        """Send a reply-like email payload tied to a thread identifier."""
        if not thread_id:
            raise ValueError("thread_id is required")

        resolved_subject = subject.strip() if subject else ""
        if not resolved_subject:
            resolved_subject = f"Re: {thread_id}"

        result = await self._send_email(to, resolved_subject, body)
        return {**result, "thread_id": thread_id}

    async def _search_inbox(self, query: str) -> list[dict]:
        """Search inbox via IMAP. Uses asyncio.to_thread for blocking IMAP calls."""
        return await asyncio.to_thread(self._imap_search, query)

    async def _read_email(self, message_id: str) -> dict:
        """Read specific email via IMAP. Uses asyncio.to_thread for blocking IMAP calls."""
        return await asyncio.to_thread(self._imap_read, message_id)
