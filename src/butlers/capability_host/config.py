"""Runtime configuration of the public MCP host (RFC 0039 D6, D7, D8, D15, D16).

Settings that are not secret come from the environment; secrets and the files the owner flips
at runtime come from mounted files, which :class:`FileLayers` re-reads every 10 seconds. Every
unreadable or malformed file fails closed: no bearer digest admits nobody, no keyring verifies
nothing, a broken break-glass file disables every domain, and missing canaries stop every tool.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from butlers.core.runtime_probe_control.keys import (
    RuntimeProbeControlKeyError,
    VerifierKeyring,
    parse_verifier_keyring_document,
)
from butlers.knowledge_read.core import DOMAINS

logger = logging.getLogger(__name__)

MCP_PORT = 8080
MCP_PATH = "/mcp"
METRICS_PORT = 9090
ROOT = Path("/etc/butlers-public-mcp")
RELOAD_SECONDS = 10.0
ISSUER = "mcp-gateway"
DEFAULT_CLIENT_DOMAINS = frozenset({"claude.ai", "chatgpt.com"})
HEAVY_TOOLS = frozenset({"finance_ledger", "finance_records", "health_series", "health_records"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FILE_BYTES = 64 * 1024
_MIN_CANARY_CHARS = 8


class ConfigError(ValueError):
    """A required setting is missing or malformed; the message names the setting only."""


@dataclass(frozen=True)
class BudgetLimits:
    """D15 budgets. Defaults are configuration, to be measured after launch."""

    per_client_per_minute: int = 60
    heavy_per_minute: int = 20
    heavy_rows_per_day: int = 20_000
    proposals_per_day: int = 20
    server_per_minute: int = 600
    heavy_tools: frozenset[str] = HEAVY_TOOLS


@dataclass(frozen=True)
class HostSettings:
    audience: str
    allowed_subs: frozenset[str]
    client_domains: frozenset[str] = DEFAULT_CLIENT_DOMAINS
    registered_clients: frozenset[str] = frozenset()
    dashboard_base: str = ""
    db_host: str = ""
    db_port: int = 5432
    db_name: str = "butlers_public"
    db_user: str = "mcp_public_ro"
    root: Path = ROOT
    reload_seconds: float = RELOAD_SECONDS
    budgets: BudgetLimits = field(default_factory=BudgetLimits)
    issuer: str = ISSUER

    @property
    def bearer_paths(self) -> tuple[Path, Path]:
        return self.root / "bearer" / "digest", self.root / "bearer" / "digest_next"

    @property
    def keyring_path(self) -> Path:
        return self.root / "keyring" / "keyring.json"

    @property
    def switches_path(self) -> Path:
        return self.root / "switches" / "disabled_domains"

    @property
    def canaries_path(self) -> Path:
        return self.root / "canaries" / "canaries.json"

    @property
    def db_password_path(self) -> Path:
        return self.root / "db" / "password"

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, root: Path = ROOT) -> HostSettings:
        def items(name: str, default: frozenset[str] = frozenset()) -> frozenset[str]:
            raw = env.get(name)
            if raw is None:
                return default
            return frozenset(x.strip() for x in raw.split(",") if x.strip())

        audience = env.get("MCP_PUBLIC_AUDIENCE", "").strip()
        subs = items("MCP_PUBLIC_ALLOWED_SUBS")
        host = env.get("MCP_PUBLIC_DB_HOST", "").strip()
        if not audience.startswith("https://"):
            raise ConfigError("MCP_PUBLIC_AUDIENCE must be an https URL")
        if not subs:
            raise ConfigError("MCP_PUBLIC_ALLOWED_SUBS must name at least one user")
        if not host:
            raise ConfigError("MCP_PUBLIC_DB_HOST is required")
        try:
            port = int(env.get("MCP_PUBLIC_DB_PORT", "5432"))
        except ValueError as exc:
            raise ConfigError("MCP_PUBLIC_DB_PORT must be an integer") from exc
        return cls(
            audience=audience,
            allowed_subs=subs,
            client_domains=frozenset(
                d.lower() for d in items("MCP_PUBLIC_CLIENT_DOMAINS", DEFAULT_CLIENT_DOMAINS)
            ),
            registered_clients=items("MCP_PUBLIC_REGISTERED_CLIENTS"),
            dashboard_base=env.get("MCP_PUBLIC_DASHBOARD_BASE", ""),
            db_host=host,
            db_port=port,
            db_name=env.get("MCP_PUBLIC_DB_NAME", "butlers_public"),
            db_user=env.get("MCP_PUBLIC_DB_USER", "mcp_public_ro"),
            root=root,
        )


def read_file(path: Path) -> bytes | None:
    """A bounded read that follows the ConfigMap/Secret symlinks; ``None`` when absent."""
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(data) > _MAX_FILE_BYTES:
        raise OSError("file is larger than the accepted bound")
    return data


def parse_digest(raw: bytes | None) -> bytes | None:
    """One lowercase hex SHA-256, surrounding whitespace allowed; anything else is refused."""
    if raw is None:
        return None
    text = raw.decode("ascii", "replace").strip()
    return bytes.fromhex(text) if _DIGEST.match(text) else None


def parse_disabled_domains(raw: bytes | None) -> frozenset[str]:
    """One domain or ``all`` per line, ``#`` comments; an unknown entry disables everything."""
    if raw is None:
        return frozenset()
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return frozenset(DOMAINS)
    names = {line.split("#", 1)[0].strip() for line in lines} - {""}
    if "all" in names or not names <= set(DOMAINS):
        return frozenset(DOMAINS)
    return frozenset(names)


def parse_canaries(raw: bytes | None) -> tuple[str, ...] | None:
    """A JSON array of distinct canary strings of at least 8 characters; else ``None``."""
    if raw is None:
        return None
    try:
        values = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(values, list) or not all(
        isinstance(v, str) and len(v) >= _MIN_CANARY_CHARS for v in values
    ):
        return None
    return tuple(dict.fromkeys(values))


class FileLayers:
    """The mounted files, re-read at most every ``reload_seconds`` and on :meth:`refresh`."""

    def __init__(self, settings: HostSettings, *, clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._clock = clock
        self._loaded_at: float | None = None
        self.bearer_digests: tuple[bytes, ...] = ()
        self.keyring: VerifierKeyring | None = None
        self.break_glass: frozenset[str] = frozenset(DOMAINS)
        self.canaries: tuple[str, ...] | None = None

    def current(self) -> FileLayers:
        now = self._clock()
        if self._loaded_at is None or now - self._loaded_at >= self._settings.reload_seconds:
            self.refresh()
            self._loaded_at = now
        return self

    def refresh(self) -> None:
        s = self._settings
        digests = []
        for path in s.bearer_paths:
            try:
                digest = parse_digest(read_file(path))
            except OSError:
                digest = None
            if digest is not None:
                digests.append(digest)
        self.bearer_digests = tuple(digests[:2])
        try:
            raw = read_file(s.keyring_path)
            self.keyring = None if raw is None else parse_verifier_keyring_document(raw)
        except (OSError, RuntimeProbeControlKeyError) as exc:
            logger.warning("public MCP keyring unusable: %s", type(exc).__name__)
            self.keyring = None
        try:
            self.break_glass = parse_disabled_domains(read_file(s.switches_path))
        except OSError:
            self.break_glass = frozenset(DOMAINS)
        try:
            self.canaries = parse_canaries(read_file(s.canaries_path))
        except OSError:
            self.canaries = None
