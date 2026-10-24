"""Operator-selected central runtime; no request supplies authority or factories.

This assembles delivered readers/ledger/signing and fixed configured owning
transports. The runner must still supply a genuine committed invocation mapping;
no startup data or transport success manufactures domain effect receipts.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from butlers.core.catalog_reader import DOMAINS
from butlers.core.goal_ledger import GoalLedger

from .assertions import AssertionSigner
from .catalog import PublishedAuthorities
from .contracts import BrokerRefused, Reason
from .forwarding import SignedMCPDomains
from .owning import PrivateApprovals
from .service import Broker
from .session import BrokerSessionListener


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Strict literal startup data, with no ENV expansion or credential resolver."""

    enabled: bool = False
    key_file: str | None = None
    kid: str | None = None
    verification_keys: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    domain_endpoints: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    approval_transports: Mapping[str, dict] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def parse(cls, value: dict) -> RuntimeConfig:
        fields = {
            "enabled",
            "key_file",
            "kid",
            "verification_keys",
            "domain_endpoints",
            "approval_transports",
        }
        try:
            if type(value) is not dict or set(value) - fields:
                raise ValueError
            if type(value.get("enabled", False)) is not bool:
                raise ValueError
            for name in ("key_file", "kid"):
                if name in value and (type(value[name]) is not str or not value[name]):
                    raise ValueError
            mappings = {}
            for name in ("verification_keys", "domain_endpoints"):
                mapping = value.get(name, {})
                if type(mapping) is not dict or any(
                    type(k) is not str or not k or type(v) is not str or not v
                    for k, v in mapping.items()
                ):
                    raise ValueError
                mappings[name] = MappingProxyType(dict(mapping))
            from butlers.config import CapabilityBrokerTransportConfig

            transports = value.get("approval_transports", {})
            if type(transports) is not dict:
                raise ValueError
            normalized = {}
            for domain, raw in transports.items():
                settings = CapabilityBrokerTransportConfig.model_validate(raw)
                if (
                    domain not in DOMAINS
                    or not settings.enabled
                    or settings.role != "broker-control"
                    or settings.domain != domain
                    or settings.peer.domain != domain
                ):
                    raise ValueError
                normalized[domain] = settings.model_dump()
            mappings["approval_transports"] = MappingProxyType(normalized)
            config = cls(**{k: v for k, v in value.items() if k not in mappings}, **mappings)
            if config.enabled and (
                config.key_file is None
                or not Path(config.key_file).is_absolute()
                or config.kid is None
                or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", config.kid) is None
                or not 1 <= len(config.verification_keys) <= 2
                or config.kid not in config.verification_keys
                or set(config.domain_endpoints) != set(DOMAINS)
            ):
                raise ValueError
            return config
        except (TypeError, ValueError):
            raise BrokerRefused(Reason.UNAVAILABLE) from None


def load_runtime_config(path: Path | None) -> RuntimeConfig:
    """Missing selection is OFF. Read at most 64 KiB of literal operator TOML."""
    if path is None:
        return RuntimeConfig.parse({})
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
                raise ValueError
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
        finally:
            os.close(fd)
        return RuntimeConfig.parse(tomllib.loads(raw.decode("utf-8")))
    except (OSError, UnicodeError, ValueError):
        raise BrokerRefused(Reason.UNAVAILABLE) from None


@dataclass(slots=True)
class CentralRuntime:
    """Own the real session lifetime; the daemon remains the sole pool owner."""

    broker: Broker
    listener: BrokerSessionListener

    _replay_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _close_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _cleanup_children: tuple = field(default=(), init=False, repr=False)

    async def start(self) -> None:
        await self.listener.start()
        if self.broker.approvals.clients:
            self._replay_task = asyncio.create_task(self._replay())

    async def _replay(self):
        while True:
            async with asyncio.timeout(5):
                async with self.broker.pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT a.goal_id,a.effect_id FROM switchboard.goal_park_associations a "
                        "JOIN switchboard.effects e ON (e.goal_id,e.id)=(a.goal_id,a.effect_id) "
                        "JOIN switchboard.goals g ON g.id=a.goal_id "
                        "WHERE a.dispatch_intent_id IS NULL AND a.park_call_id IS NOT NULL "
                        "AND g.status IN ('running','settling') "
                        "AND e.status IN ('requested','parked','unknown') "
                        "ORDER BY e.created_at,e.id LIMIT 16"
                    )
            for row in rows:
                try:
                    await self.broker.replay_approved(row["goal_id"], row["effect_id"])
                except Exception:
                    # No payload diagnostics or transport retry. A later scan
                    # can only read; an existing intent is never executable again.
                    pass
            await asyncio.sleep(1)

    async def _release(self):
        if self._replay_task is not None:
            self._replay_task.cancel()
        children = [asyncio.create_task(self.listener.close())]
        children.extend(
            asyncio.create_task(c.aclose()) for c in self.broker.approvals.clients.values()
        )
        if self._replay_task is not None:
            children.append(self._replay_task)
        self._cleanup_children = tuple(children)
        done, pending = await asyncio.wait(children, timeout=6)
        failure = None
        for child in done:
            try:
                child.result()
            except asyncio.CancelledError:
                if child is not self._replay_task:
                    failure = failure or RuntimeError("central_resource_cleanup_cancelled")
            except BaseException as exc:
                failure = failure or exc
        if pending:
            raise TimeoutError("central_runtime_cleanup_incomplete") from failure
        if failure is not None:
            raise failure

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._release())
        cancellation = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except BaseException:
                break
        try:
            self._close_task.result()
        except BaseException as exc:
            if cancellation is not None:
                raise cancellation from exc
            raise
        if cancellation is not None:
            raise cancellation


async def build_runtime(pool, config: RuntimeConfig, *, host: str, port: int = 41199):
    """Assemble fixed production adapters, after actual SQL readiness, before bind.

    No constructor starts a domain connection. A disabled config returns before
    touching the pool, key path, public key bytes, adapters or session socket.
    The factory never issues a token: the private runner hook still requires a
    committed authenticated invocation, current lease, ceiling and hash CAS.
    """
    if type(config) is not RuntimeConfig:
        raise BrokerRefused(Reason.UNAVAILABLE)
    try:
        # Revalidate even directly constructed dataclasses; no alternate bypass API.
        config = RuntimeConfig.parse(
            {
                "enabled": config.enabled,
                **({"key_file": config.key_file} if config.key_file is not None else {}),
                **({"kid": config.kid} if config.kid is not None else {}),
                "verification_keys": dict(config.verification_keys),
                "domain_endpoints": dict(config.domain_endpoints),
                "approval_transports": dict(config.approval_transports),
            }
        )
        if not config.enabled:
            return None
        domains = SignedMCPDomains(config.domain_endpoints)
        keys = {}
        for kid, encoded in config.verification_keys.items():
            if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kid) is None:
                raise ValueError
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
                raise ValueError
            keys[kid] = Ed25519PublicKey.from_public_bytes(raw)
        authorities = PublishedAuthorities()
        async with asyncio.timeout(5):
            async with pool.acquire() as connection, connection.transaction():
                await GoalLedger(connection).verify_role()
                await authorities.catalog(connection, None)
        signer = AssertionSigner.from_file(
            enabled=True, path=Path(config.key_file), kid=config.kid, verification_keys=keys
        )
        from butlers.config import CapabilityBrokerTransportConfig

        from .production import material_context
        from .transport.client import build_client
        from .transport.config import PeerPolicy, TransportConfig

        clients = {}
        try:
            for domain, raw in config.approval_transports.items():
                settings = CapabilityBrokerTransportConfig.model_validate(raw)
                peer = PeerPolicy(
                    settings.peer.role,
                    settings.peer.domain,
                    frozenset(settings.peer.sans),
                    settings.peer.client_certificate,
                )
                clients[domain] = build_client(
                    TransportConfig(settings.endpoint, peer),
                    lambda material=settings.material: material_context(material),
                )
        except BaseException:
            # Construction opens no network connections. Close every created
            # client before allowing a failed assembly to escape.
            await asyncio.gather(*(client.aclose() for client in clients.values()))
            raise
        broker = Broker(pool, authorities, domains, signer, approvals=PrivateApprovals(clients))
        return CentralRuntime(broker, BrokerSessionListener(broker, host=host, port=port))
    except BrokerRefused:
        raise
    except Exception:
        raise BrokerRefused(Reason.UNAVAILABLE) from None


def decode_verification_keys(values):
    keys = {}
    try:
        if type(values) is not dict or not 1 <= len(values) <= 2:
            raise ValueError
        for kid, encoded in values.items():
            if type(kid) is not str or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kid) is None:
                raise ValueError
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
                raise ValueError
            keys[kid] = Ed25519PublicKey.from_public_bytes(raw)
        return keys
    except (ValueError, TypeError):
        raise BrokerRefused(Reason.UNAVAILABLE) from None
