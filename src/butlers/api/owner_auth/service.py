"""Owner authentication service and private PostgreSQL capability boundary."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.api.owner_auth.verifier import InvalidProof, WebAuthnVerifier, decode
from butlers.db import database_name_from_env, db_params_from_env, register_jsonb_codec

_ERRORS = {
    "UNAUTHORIZED": (401, "Owner authentication required"),
    "FORBIDDEN": (403, "Owner authentication request forbidden"),
    "AUTH_RESTART_REQUIRED": (409, "Restart owner authentication"),
    "RATE_LIMITED": (429, "Owner authentication rate limit reached"),
    "AUTH_UNAVAILABLE": (503, "Owner authentication unavailable"),
    "BAD_REQUEST": (400, "Invalid owner authentication request"),
    "INVALID_REQUEST": (400, "Invalid owner authentication request"),
    "REQUEST_TOO_LARGE": (413, "Owner authentication request too large"),
}


class AuthError(Exception):
    def __init__(self, code: str = "AUTH_UNAVAILABLE") -> None:
        self.code = code if code in _ERRORS else "AUTH_UNAVAILABLE"
        self.status_code, self.message = _ERRORS[self.code]
        super().__init__(self.message)


@dataclass(frozen=True, repr=False)
class IssuedContext:
    data: dict[str, Any]
    token: str


@dataclass(frozen=True, repr=False)
class IssuedSession:
    data: dict[str, Any]
    token: str


@dataclass(frozen=True)
class OptionsResult:
    data: dict[str, Any]
    status_code: int


@dataclass(frozen=True)
class Authority:
    method: str
    expires_at: str | None


def _token() -> str:
    return secrets.token_urlsafe(32)


def _digest(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return hashlib.sha256(value.encode()).hexdigest()
    except (AttributeError, UnicodeError):
        raise AuthError("UNAUTHORIZED") from None


def _locator(value: str) -> str:
    try:
        decode(value, maximum=32, exact=32)
    except InvalidProof:
        raise AuthError("BAD_REQUEST") from None
    return value


class OwnerAuthService:
    def __init__(self, pool: asyncpg.Pool | None, config: Any) -> None:
        self.pool = pool
        self.config = config
        self._cleanup_task: asyncio.Task | None = None
        self.verifier = (
            WebAuthnVerifier(config.origin, config.rp_id)
            if config.origin and config.rp_id
            else None
        )

    def _config(self) -> dict:
        return {
            "origin": self.config.origin,
            "rp_id": self.config.rp_id,
            "key_generation": _digest(self.config.api_key) if self.config.api_key else None,
        }

    async def _call(self, action: str, **values: Any) -> dict:
        if self.pool is None:
            raise AuthError()
        try:
            # SET LOCAL ROLE happens on every checkout; asyncpg resets session role.
            with suppress_instrumentation():
                async with self.pool.acquire() as connection, connection.transaction():
                    await connection.execute("SET LOCAL ROLE dashboard_auth_api")
                    await register_jsonb_codec(connection)
                    raw = await connection.fetchval(
                        "SELECT dashboard_auth.api($1, $2::jsonb)",
                        action,
                        self._config() | values,
                    )
            result = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(result, dict):
                raise AuthError()
        except AuthError:
            raise
        except Exception:
            raise AuthError() from None
        if "error" in result:
            raise AuthError(result["error"])
        return result

    @staticmethod
    def _proof(preauth_token: str, csrf_token: str) -> dict:
        if not preauth_token:
            raise AuthError("UNAUTHORIZED")
        if not csrf_token:
            raise AuthError("FORBIDDEN")
        return {"context_digest": _digest(preauth_token), "csrf_digest": _digest(csrf_token)}

    async def status(self, session_token: str | None = None) -> dict:
        try:
            data = await self._call("status", session_digest=_digest(session_token))
            return {
                "state": data["state"],
                "authenticated": data["authenticated"],
                "session_expires_at": data["session_expires_at"],
            }
        except AuthError:
            return {"state": "unavailable", "authenticated": False, "session_expires_at": None}

    async def context(self) -> IssuedContext:
        token, csrf = _token(), _token()
        data = await self._call("context", context_digest=_digest(token), csrf_digest=_digest(csrf))
        return IssuedContext(
            {"csrf_token": csrf, "csrf_expires_at": data["csrf_expires_at"]}, token
        )

    async def intent(self, preauth_token: str, csrf_token: str, operation: str) -> dict:
        if operation not in ("enroll", "recover"):
            raise AuthError("BAD_REQUEST")
        data = await self._call(
            "intent",
            **self._proof(preauth_token, csrf_token),
            operation=operation,
            request_id=_token(),
        )
        return {
            "request_id": data["request_id"],
            "expires_at": data["expires_at"],
            "operation": data["operation"],
            "canonical_origin": data["canonical_origin"],
        }

    def _options(self, ceremony: dict) -> dict:
        remaining = max(
            1,
            min(
                300000,
                int(
                    (
                        datetime.fromisoformat(ceremony["expires_at"]) - datetime.now().astimezone()
                    ).total_seconds()
                    * 1000
                ),
            ),
        )
        public: dict[str, Any] = {"challenge": ceremony["challenge"], "timeout": remaining}
        if ceremony["operation"] == "login":
            public.update(rpId=self.config.rp_id, userVerification="required")
        else:
            public.update(
                rp={"id": self.config.rp_id, "name": "Butlers"},
                user={
                    "id": ceremony["user_handle"],
                    "name": "owner",
                    "displayName": "Butlers owner",
                },
                pubKeyCredParams=[
                    {"type": "public-key", "alg": -7},
                    {"type": "public-key", "alg": -257},
                ],
                authenticatorSelection={
                    "residentKey": "required",
                    "requireResidentKey": True,
                    "userVerification": "required",
                },
                attestation="none",
            )
        return {"ceremony_id": ceremony["id"], "publicKey": public}

    async def registration_options(
        self,
        preauth_token: str,
        csrf_token: str,
        request_id: str,
    ) -> OptionsResult:
        data = await self._call(
            "registration_options",
            **self._proof(preauth_token, csrf_token),
            request_id=_locator(request_id),
            ceremony_id=_token(),
            challenge=_token(),
            user_handle=_token(),
        )
        if data.get("state") == "pending":
            return OptionsResult({"state": "pending", "expires_at": data["expires_at"]}, 202)
        return OptionsResult(self._options(data), 200)

    async def login_options(self, preauth_token: str, csrf_token: str) -> dict:
        data = await self._call(
            "login_options",
            **self._proof(preauth_token, csrf_token),
            ceremony_id=_token(),
            challenge=_token(),
        )
        return self._options(data)

    async def _finish(
        self,
        operation: str,
        preauth_token: str,
        csrf_token: str,
        ceremony_id: str,
        credential: dict,
    ) -> IssuedSession:
        proof = self._proof(preauth_token, csrf_token) | {"ceremony_id": _locator(ceremony_id)}
        snapshot = await self._call("snapshot_" + operation, **proof)
        if self.verifier is None:
            raise AuthError()
        try:
            verified = await asyncio.to_thread(
                self.verifier.verify,
                snapshot["ceremony"],
                credential,
                snapshot["credential"],
            )
        except InvalidProof:
            raise AuthError("UNAUTHORIZED") from None
        token, csrf = _token(), _token()
        data = await self._call(
            "finish_" + operation,
            **proof,
            challenge=snapshot["ceremony"]["challenge"],
            credential_epoch=snapshot["ceremony"]["credential_epoch"],
            credential_id=verified.credential_id,
            credential_data=verified.credential_data,
            backup_eligible=verified.backup_eligible,
            backup_state=verified.backup_state,
            counter=verified.counter,
            new_session_digest=_digest(token),
            new_csrf_digest=_digest(csrf),
        )
        return IssuedSession(
            {
                "csrf_token": csrf,
                "csrf_expires_at": data["csrf_expires_at"],
                "session_expires_at": data["session_expires_at"],
            },
            token,
        )

    async def finish_registration(
        self,
        preauth_token: str,
        csrf_token: str,
        ceremony_id: str,
        credential: dict,
    ) -> IssuedSession:
        return await self._finish(
            "registration", preauth_token, csrf_token, ceremony_id, credential
        )

    async def finish_login(
        self,
        preauth_token: str,
        csrf_token: str,
        ceremony_id: str,
        credential: dict,
    ) -> IssuedSession:
        return await self._finish("login", preauth_token, csrf_token, ceremony_id, credential)

    async def cancel(
        self,
        preauth_token: str,
        csrf_token: str,
        request_id: str | None = None,
        ceremony_id: str | None = None,
    ) -> None:
        if (request_id is None) == (ceremony_id is None):
            raise AuthError("BAD_REQUEST")
        await self._call(
            "cancel",
            **self._proof(preauth_token, csrf_token),
            request_id=_locator(request_id) if request_id else None,
            ceremony_id=_locator(ceremony_id) if ceremony_id else None,
        )

    def _check_key(self, api_key: str | None) -> None:
        configured = self.config.api_key
        if not isinstance(api_key, str) or not configured:
            raise AuthError("UNAUTHORIZED")
        try:
            matches = hmac.compare_digest(api_key.encode(), configured.encode())
        except UnicodeError:
            raise AuthError("UNAUTHORIZED") from None
        if not matches:
            raise AuthError("UNAUTHORIZED")

    async def key_session(self, api_key: str) -> IssuedSession:
        # Charge all bounded attempts across workers before the key comparison.
        # The attempt operation cannot create a session or grant authority.
        await self._call("key_session_attempt")
        self._check_key(api_key)
        token, csrf = _token(), _token()
        data = await self._call(
            "key_session", new_session_digest=_digest(token), new_csrf_digest=_digest(csrf)
        )
        return IssuedSession(
            {
                "csrf_token": csrf,
                "csrf_expires_at": data["csrf_expires_at"],
                "session_expires_at": data["session_expires_at"],
            },
            token,
        )

    async def authorize(
        self,
        session_token: str | None = None,
        api_key: str | None = None,
        csrf_token: str | None = None,
        unsafe: bool = False,
    ) -> Authority:
        if api_key is not None:
            self._check_key(api_key)
            data = await self._call("header")
        else:
            data = await self._call(
                "authorize",
                session_digest=_digest(session_token),
                method="cookie",
                csrf_digest=_digest(csrf_token),
                unsafe=unsafe,
            )
        return Authority(data["method"], data["expires_at"])

    async def csrf(self, session_token: str) -> dict:
        csrf = _token()
        data = await self._call(
            "csrf", session_digest=_digest(session_token), new_csrf_digest=_digest(csrf)
        )
        return {"csrf_token": csrf, "csrf_expires_at": data["csrf_expires_at"]}

    async def revoke(
        self,
        session_token: str | None = None,
        api_key: str | None = None,
        all_sessions: bool = False,
        csrf_token: str | None = None,
    ) -> None:
        if api_key is not None:
            self._check_key(api_key)
        await self._call(
            "revoke_all" if all_sessions else "revoke",
            session_digest=_digest(session_token),
            method="header" if api_key is not None else "cookie",
            unsafe=True,
            csrf_digest=_digest(csrf_token),
        )

    async def cleanup(self) -> None:
        if self.pool is None:
            return
        with suppress_instrumentation():
            async with self.pool.acquire() as connection, connection.transaction():
                await connection.execute("SET LOCAL ROLE dashboard_auth_api")
                await connection.execute("SELECT dashboard_auth.cleanup()")


async def _cleanup_loop(service: OwnerAuthService) -> None:
    while True:
        try:
            await service.cleanup()
        except Exception:
            # No exception tails or auth material in retained background evidence.
            logging.getLogger(__name__).warning("Owner authentication cleanup unavailable")
        await asyncio.sleep(300)


async def _initialize_auth_connection(connection: asyncpg.Connection) -> None:
    """RESET ROLE must never recover host authority on a dashboard connection."""
    with suppress_instrumentation():
        safe = await connection.fetchval(
            """
            WITH RECURSIVE reachable(oid) AS (
                SELECT oid FROM pg_roles WHERE rolname=session_user
                UNION
                SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member=r.oid
            ), host_function AS (
                SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='dashboard_auth' AND p.proname='host'
                  AND p.proargtypes='25 3802'::oidvector
            )
            SELECT EXISTS(SELECT FROM host_function)
              AND pg_has_role(session_user, 'dashboard_auth_api', 'MEMBER')
              AND NOT EXISTS (
                SELECT FROM reachable JOIN pg_roles r USING(oid)
                WHERE r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
                   OR r.rolbypassrls OR r.rolreplication
                   OR has_function_privilege(r.oid,
                        (SELECT oid FROM host_function), 'EXECUTE')
              )
            """
        )
        if safe is not True:
            raise AuthError()
        await register_jsonb_codec(connection)


async def create_owner_auth_service(config: Any) -> OwnerAuthService:
    """Use a dedicated Tier 0 login; never borrow the host's administrative login."""
    pool = None
    user = os.environ.get("DASHBOARD_AUTH_DB_USER")
    password = os.environ.get("DASHBOARD_AUTH_DB_PASSWORD")
    if user and password:
        try:
            params = db_params_from_env() | {"user": user, "password": password}
            with suppress_instrumentation():
                pool = await asyncpg.create_pool(
                    **params,
                    database=database_name_from_env("butlers"),
                    min_size=1,
                    max_size=3,
                    command_timeout=5,
                    timeout=5,
                    init=_initialize_auth_connection,
                )
        except Exception:
            logging.getLogger(__name__).warning("Owner authentication storage unavailable")
    service = OwnerAuthService(pool, config)
    service._cleanup_task = asyncio.create_task(_cleanup_loop(service))
    return service


async def close_owner_auth_service(service: OwnerAuthService) -> None:
    if service._cleanup_task is not None:
        service._cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await service._cleanup_task
    if service.pool is not None:
        await service.pool.close()
