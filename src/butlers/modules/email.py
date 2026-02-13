"""Email module — send_email, search_inbox, read_email MCP tools.

Uses IMAP for inbox access and SMTP for sending.
Configured via [modules.email] with optional
[modules.email.user] and [modules.email.bot] credential scopes in butler.toml.

When a ``MessagePipeline`` is attached, incoming emails can be classified
and routed to the appropriate butler via ``check_and_route_inbox``.
"""

from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import logging
import os
import re
import smtplib
from email.mime.text import MIMEText
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.modules.base import Module
from butlers.modules.pipeline import MessagePipeline, RoutingResult

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
    """Configuration for the Email module."""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    use_tls: bool = True
    user: EmailUserCredentialsConfig = Field(default_factory=EmailUserCredentialsConfig)
    bot: EmailBotCredentialsConfig = Field(default_factory=EmailBotCredentialsConfig)
    model_config = ConfigDict(extra="forbid")


class EmailModule(Module):
    """Email module providing send_email, search_inbox, and read_email tools.

    When a ``MessagePipeline`` is set via ``set_pipeline()``, the
    ``check_and_route_inbox`` tool becomes functional: it fetches unseen
    emails, classifies each via ``classify_message()``, and routes them
    to the appropriate butler.
    """

    def __init__(self) -> None:
        self._config: EmailConfig = EmailConfig()
        self._pipeline: MessagePipeline | None = None
        self._routed_messages: list[RoutingResult] = []

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
        if self._config.user.enabled:
            envs.extend([self._config.user.address_env, self._config.user.password_env])
        return envs

    def migration_revisions(self) -> str | None:
        return None  # No custom tables needed

    def set_pipeline(self, pipeline: MessagePipeline) -> None:
        """Attach a classification/routing pipeline for incoming messages.

        When set, ``check_and_route_inbox`` will classify and route each
        unseen email to the appropriate butler.
        """
        self._pipeline = pipeline

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register send_email, search_inbox, read_email, check_and_route_inbox MCP tools."""
        self._config = config if isinstance(config, EmailConfig) else EmailConfig(**(config or {}))
        module = self  # capture for closures

        @mcp.tool()
        async def send_email(to: str, subject: str, body: str) -> dict:
            """Send an email via SMTP."""
            return await module._send_email(to, subject, body)

        @mcp.tool()
        async def search_inbox(query: str) -> list[dict]:
            """Search inbox via IMAP SEARCH."""
            return await module._search_inbox(query)

        @mcp.tool()
        async def read_email(message_id: str) -> dict:
            """Read a specific email by message ID."""
            return await module._read_email(message_id)

        @mcp.tool()
        async def check_and_route_inbox() -> dict:
            """Check for unseen emails and route each through the classification pipeline."""
            return await module._check_and_route_inbox()

    async def on_startup(self, config: Any, db: Any) -> None:
        """Initialize email config. Connections are created per-operation."""
        self._config = config if isinstance(config, EmailConfig) else EmailConfig(**(config or {}))

    async def on_shutdown(self) -> None:
        """No persistent connections to clean up."""
        pass

    # ------------------------------------------------------------------
    # Classification pipeline integration
    # ------------------------------------------------------------------

    async def process_incoming(
        self,
        email_data: dict[str, str],
    ) -> RoutingResult | None:
        """Process a single email through the classification pipeline.

        Builds a message string from the email subject and body, then
        classifies and routes it via the pipeline.

        Returns ``None`` if no pipeline is configured or the email has
        no usable content.
        """
        if self._pipeline is None:
            return None

        subject = email_data.get("subject", "")
        body = email_data.get("body", "")
        sender = email_data.get("from", "")
        message_id = email_data.get("message_id", "")

        # Build a text representation for classification
        text = _build_classification_text(subject, body)
        if not text:
            return None

        result = await self._pipeline.process(
            message_text=text,
            tool_name="handle_message",
            tool_args={
                "source": "email",
                "from": sender,
                "subject": subject,
                "message_id": message_id,
            },
        )

        self._routed_messages.append(result)
        logger.info(
            "Email routed to %s (from=%s, subject=%s)",
            result.target_butler,
            sender,
            subject,
        )
        return result

    async def _check_and_route_inbox(self) -> dict:
        """Check for unseen emails and route each through the pipeline.

        Returns a summary dict with counts and per-email routing results.
        """
        if self._pipeline is None:
            return {"status": "no_pipeline", "message": "No classification pipeline configured"}

        try:
            unseen = await self._search_inbox("UNSEEN")
        except Exception as exc:
            return {"status": "error", "message": f"Failed to search inbox: {exc}"}

        results: list[dict[str, Any]] = []
        for email_header in unseen:
            # Read the full email
            try:
                full_email = await self._read_email(email_header["message_id"])
            except Exception as exc:
                results.append(
                    {
                        "message_id": email_header.get("message_id"),
                        "status": "error",
                        "error": f"Failed to read email: {exc}",
                    }
                )
                continue

            if "error" in full_email:
                results.append(
                    {
                        "message_id": email_header.get("message_id"),
                        "status": "error",
                        "error": full_email["error"],
                    }
                )
                continue

            # Route through the pipeline
            routing_result = await self.process_incoming(full_email)
            if routing_result:
                results.append(
                    {
                        "message_id": email_header.get("message_id"),
                        "subject": full_email.get("subject"),
                        "target_butler": routing_result.target_butler,
                        "status": "routed",
                    }
                )
            else:
                results.append(
                    {
                        "message_id": email_header.get("message_id"),
                        "status": "skipped",
                        "reason": "no content or no pipeline",
                    }
                )

        return {
            "status": "ok",
            "total": len(unseen),
            "routed": sum(1 for r in results if r.get("status") == "routed"),
            "results": results,
        }

    # ------------------------------------------------------------------
    # Implementation helpers using stdlib imaplib/smtplib
    # ------------------------------------------------------------------

    def _get_credentials(self, *, scope: str = "bot") -> tuple[str, str]:
        """Read email credentials from environment variables.

        Raises ``RuntimeError`` if configured credential env vars for the
        selected identity scope are not set.
        """
        scope_cfg = self._config.bot if scope == "bot" else self._config.user
        if not scope_cfg.enabled:
            raise RuntimeError(f"Email credential scope modules.email.{scope} is disabled")

        address_env = scope_cfg.address_env
        password_env = scope_cfg.password_env
        address = os.environ.get(address_env)
        password = os.environ.get(password_env)
        if not address or not password:
            raise RuntimeError(
                f"Missing email credentials for modules.email.{scope}: set "
                f"{address_env} and {password_env}"
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
                "body": body,
            }
        finally:
            conn.logout()

    async def _send_email(self, to: str, subject: str, body: str) -> dict:
        """Send email via SMTP. Uses asyncio.to_thread for blocking SMTP calls."""
        return await asyncio.to_thread(self._smtp_send, to, subject, body)

    async def _search_inbox(self, query: str) -> list[dict]:
        """Search inbox via IMAP. Uses asyncio.to_thread for blocking IMAP calls."""
        return await asyncio.to_thread(self._imap_search, query)

    async def _read_email(self, message_id: str) -> dict:
        """Read specific email via IMAP. Uses asyncio.to_thread for blocking IMAP calls."""
        return await asyncio.to_thread(self._imap_read, message_id)


def _build_classification_text(subject: str, body: str) -> str | None:
    """Build a classification-ready text from email subject and body.

    Returns None if both are empty.
    """
    parts = []
    if subject:
        parts.append(f"Subject: {subject}")
    if body:
        # Limit body to first 500 chars to avoid overwhelming the classifier
        parts.append(body[:500])
    return "\n".join(parts) if parts else None
