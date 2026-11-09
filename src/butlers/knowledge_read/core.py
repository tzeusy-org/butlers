"""Shared contract for the pure public read functions (RFC 0039 D10, D12, D17).

Every reader takes an asyncpg connection to the projection database, a :class:`ReadContext`
the host builds once per request, and the caller's raw argument mapping. It returns plain
JSON-ready data or raises :class:`ReadError`. Readers never write, keep no state between calls
and never reach the network; the host owns the connection, authentication, budgets, kill-switch
layers, DLP and transport (D1).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any

import asyncpg

from butlers.knowledge_read.manifest import Manifest, Table, load_manifest

MAX_RESPONSE_BYTES = 256 * 1024
CURSOR_TTL = timedelta(minutes=15)
LAGGING_AFTER = timedelta(minutes=2)
STALE_AFTER = timedelta(minutes=15)
DOMAINS = ("knowledge", "travel", "relationship", "time", "finance", "health", "proposals")

# D17: fixed messages that never echo arguments. retry_after_s is always null in v1.
_ERRORS = {
    "domain_disabled": (False, "This data source is turned off by the owner."),
    "projection_stale": (True, "The data copy is out of date; try again later."),
    "projection_version_mismatch": (True, "The server and its data copy disagree; try later."),
    "projection_apply_pending": (True, "The data copy is being updated; try again later."),
    "budget_exhausted": (True, "The request budget is used up; try again later."),
    "invalid_argument": (False, "An argument is missing, malformed or out of range."),
    "not_found": (False, "No published record has that id."),
    "too_large": (False, "The result is too large; narrow the request."),
    "blocked": (False, "This result was blocked."),
    "unavailable": (True, "The data source is unavailable; try again later."),
}


class ReadError(Exception):
    """A tool-level refusal with a D17 code; ``argument`` names a field, never its value."""

    def __init__(self, code: str, *, domain: str | None = None, argument: str | None = None):
        if code not in _ERRORS:
            raise ValueError(f"unknown read error code: {code}")
        super().__init__(code)
        self.code = code
        self.domain = domain
        self.argument = argument

    def to_dict(self) -> dict[str, Any]:
        retryable, message = _ERRORS[self.code]
        return {
            "error": {
                "code": self.code,
                "domain": self.domain,
                "retryable": retryable,
                "retry_after_s": None,
                "message": message,
            }
        }


@dataclass(frozen=True)
class ReadContext:
    """Per-request facts the host supplies; readers derive nothing else from the environment.

    ``cursor_key`` is the host's process-lifetime random key, so a restart invalidates every
    cursor. ``disabled_domains`` is the union of every kill-switch layer the host can see.
    """

    sub: str
    as_of: datetime | None
    now: datetime
    cursor_key: bytes = field(repr=False)
    disabled_domains: frozenset[str] = frozenset()

    @property
    def freshness(self) -> str:
        if self.as_of is None or self.now - self.as_of > STALE_AFTER:
            return "stale"
        return "lagging" if self.now - self.as_of > LAGGING_AFTER else "fresh"

    def require(self, domain: str) -> None:
        """Refuse, in D17 precedence order, when ``domain`` cannot be served."""
        if self.freshness == "stale":
            raise ReadError("projection_stale")
        if domain in self.disabled_domains:
            raise ReadError("domain_disabled", domain=domain)

    def enabled(self, domain: str) -> bool:
        return domain not in self.disabled_domains

    def degraded(self, domains: tuple[str, ...]) -> list[dict[str, str]]:
        """``sources_degraded`` entries for the domains a multi-domain tool spans."""
        result = [{"domain": d, "reason": "disabled"} for d in domains if not self.enabled(d)]
        if self.freshness == "lagging":
            result += [{"domain": d, "reason": "lagging"} for d in domains if self.enabled(d)]
        return result

    def source(
        self, kind: str, uri: str, content_authority: str | None = None
    ) -> dict[str, str | None]:
        return {
            "kind": kind,
            "uri": uri,
            "as_of": _iso(self.as_of),
            "content_authority": content_authority,
        }

    def aggregate_source(self, domain: str, tool: str) -> dict[str, str | None]:
        return {"kind": tool, "uri": f"butlers://{domain}/{tool}", "as_of": _iso(self.as_of)}


async def read_context(
    conn: asyncpg.Connection,
    *,
    sub: str,
    cursor_key: bytes,
    disabled_domains: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> ReadContext:
    """Read the replication time once (D17) and bind it to this request."""
    as_of = await conn.fetchval("SELECT mcp_read.replication_as_of()")
    return ReadContext(
        sub=sub,
        as_of=as_of,
        now=now or datetime.now(UTC),
        cursor_key=cursor_key,
        disabled_domains=frozenset(disabled_domains),
    )


async def projection_disabled_domains(conn: asyncpg.Connection) -> frozenset[str]:
    """The replicated ``mcp_surface.controls`` switch layer; ``all`` disables every domain."""
    rows = await conn.fetch(
        "SELECT fields->>'domain' AS domain FROM mcp_read.mcp_surface__controls "
        "WHERE fields->>'state' = 'disabled'"
    )
    off = {r["domain"] for r in rows}
    return frozenset(DOMAINS) if "all" in off else frozenset(off & set(DOMAINS))


@cache
def _manifest() -> Manifest:
    return load_manifest()


def published(source: str, tool: str) -> tuple[Table, str]:
    """The manifest entry and ``mcp_read`` view a tool reads; refuses an unassigned source."""
    for table in _manifest().tables:
        if table.source == source:
            if tool not in table.tools:
                raise LookupError(f"{source} is not a source of {tool}")
            return table, "mcp_read." + source.replace(".", "__")
    raise LookupError(f"{source} is not published")


def record_uri(table: Table, row_id: str) -> tuple[str, str]:
    """(kind, uri) from the manifest's ``butlers://<domain>/<kind>/{key}`` template."""
    template = table.uri
    kind = template.split("/")[3]
    return kind, template[: template.index("{")] + row_id


def source_domains() -> dict[str, str]:
    """``<schema>.<table>`` of every fact source an edge or withheld count can name."""
    return {
        t.source: t.domain
        for t in _manifest().tables
        if t.source.endswith((".facts", ".entity_facts"))
    }


async def in_snapshot(conn: asyncpg.Connection, read: Callable[[], Any]) -> Any:
    """Run every query of one call in one read-only repeatable-read snapshot."""
    if conn.is_in_transaction():
        return await read()
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        return await read()


def fields(value: str | dict | None) -> dict[str, Any]:
    """A view's ``fields`` jsonb, whether or not the host installed a jsonb codec."""
    if value is None:
        return {}
    return json.loads(value) if isinstance(value, str) else dict(value)


def _iso(value: datetime | date | None) -> str | None:
    return None if value is None else value.isoformat()


class Arguments:
    """Closed argument parsing: unknown names refuse; text is NFC-normalized and capped."""

    def __init__(self, raw: object, allowed: frozenset[str] | set[str]):
        if not isinstance(raw, Mapping) or any(not isinstance(k, str) for k in raw):
            raise ReadError("invalid_argument")
        unknown = set(raw) - set(allowed)
        if unknown:
            raise ReadError("invalid_argument", argument=sorted(unknown)[0])
        self._raw = raw
        self.values: dict[str, Any] = {}

    def _get(self, name: str, required: bool) -> object:
        value = self._raw.get(name)
        if value is None and required:
            raise ReadError("invalid_argument", argument=name)
        return value

    def _keep(self, name: str, value: Any) -> Any:
        self.values[name] = value
        return value

    def text(self, name: str, *, max_length: int, required: bool = False) -> str | None:
        value = self._get(name, required)
        if value is None:
            return self._keep(name, None)
        if not isinstance(value, str):
            raise ReadError("invalid_argument", argument=name)
        value = unicodedata.normalize("NFC", value).strip()
        if len(value) > max_length or (required and not value):
            raise ReadError("invalid_argument", argument=name)
        return self._keep(name, value or None)

    def choice(self, name: str, options: tuple[str, ...], *, default: str | None = None) -> str:
        value = self._get(name, default is None)
        value = default if value is None else value
        if value not in options:
            raise ReadError("invalid_argument", argument=name)
        return self._keep(name, value)

    def subset(
        self, name: str, *, max_items: int, max_length: int, options: tuple[str, ...] = ()
    ) -> tuple[str, ...] | None:
        value = self._get(name, False)
        if value is None:
            return self._keep(name, None)
        if not isinstance(value, list) or not value or len(value) > max_items:
            raise ReadError("invalid_argument", argument=name)
        items = []
        for item in value:
            if not isinstance(item, str):
                raise ReadError("invalid_argument", argument=name)
            item = unicodedata.normalize("NFC", item).strip()
            if not item or len(item) > max_length or (options and item not in options):
                raise ReadError("invalid_argument", argument=name)
            items.append(item)
        return self._keep(name, tuple(sorted(set(items))))

    def integer(self, name: str, *, low: int, high: int, default: int | None = None) -> int:
        value = self._get(name, default is None)
        value = default if value is None else value
        if type(value) is not int or not low <= value <= high:
            raise ReadError("invalid_argument", argument=name)
        return self._keep(name, value)

    def uuid(self, name: str) -> str:
        value = self._get(name, True)
        try:
            parsed = uuid.UUID(value) if isinstance(value, str) and len(value) <= 64 else None
        except ValueError:
            parsed = None
        if parsed is None:
            raise ReadError("invalid_argument", argument=name)
        return self._keep(name, str(parsed))

    def day(self, name: str) -> date:
        value = self._get(name, True)
        try:
            parsed = (
                date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
            )
        except ValueError:
            parsed = None
        if parsed is None:
            raise ReadError("invalid_argument", argument=name)
        self._keep(name, parsed.isoformat())
        return parsed

    def instant(self, name: str) -> datetime:
        """An ISO 8601 date or offset-qualified date-time; a bare date is UTC midnight."""
        value = self._get(name, True)
        parsed = None
        if isinstance(value, str) and len(value) <= 40:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC) if len(value) == 10 else None
        if parsed is None:
            raise ReadError("invalid_argument", argument=name)
        self._keep(name, parsed.astimezone(UTC).isoformat())
        return parsed

    def cursor(self) -> str | None:
        value = self._get("cursor", False)
        if value is not None and (not isinstance(value, str) or len(value) > 2048):
            raise ReadError("invalid_argument", argument="cursor")
        return value


def span(start: date, end: date, *, max_days: int) -> None:
    """Refuse an inverted window or one longer than the tool's declared span."""
    if end < start or end - start > timedelta(days=max_days):
        raise ReadError("invalid_argument", argument="end")


def _digest(tool: str, values: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(
        json.dumps([tool, values], sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def _mac(context: ReadContext, payload: bytes) -> bytes:
    return hmac.new(context.cursor_key, context.sub.encode() + b"\0" + payload, "sha256").digest()


def encode_cursor(
    context: ReadContext, tool: str, values: Mapping[str, Any], position: list
) -> str:
    """An opaque token bound to (sub, tool, argument digest, position, expiry) (D10)."""
    body = json.dumps(
        {
            "t": tool,
            "a": _digest(tool, values).hex(),
            "p": position,
            "e": int((context.now + CURSOR_TTL).timestamp()),
        },
        separators=(",", ":"),
    ).encode()
    token = body + b"." + base64.urlsafe_b64encode(_mac(context, body))
    return base64.urlsafe_b64encode(token).decode().rstrip("=")


def decode_cursor(
    context: ReadContext, tool: str, values: Mapping[str, Any], token: str | None
) -> list | None:
    """The position of a valid cursor; any tampering, expiry or rebinding refuses."""
    if token is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        body, mac = raw.rsplit(b".", 1)
        valid = hmac.compare_digest(base64.urlsafe_b64decode(mac), _mac(context, body))
        payload = json.loads(body) if valid else None
    except (ValueError, TypeError):
        payload = None
    if (
        not isinstance(payload, dict)
        or payload.get("t") != tool
        or payload.get("a") != _digest(tool, values).hex()
        or not isinstance(payload.get("e"), int)
        or payload["e"] < context.now.timestamp()
        or not isinstance(payload.get("p"), list)
    ):
        raise ReadError("invalid_argument", argument="cursor")
    return payload["p"]


def fit(items: list, build: Callable[[list, bool], dict[str, Any]]) -> dict[str, Any]:
    """Drop trailing items until the response fits 256 KiB; a cut response says so (D10)."""
    result = build(items, False)
    while len(json.dumps(result, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
        if not items:
            raise ReadError("too_large")
        items = items[: max(0, min(len(items) - 1, len(items) * 9 // 10))]
        result = build(items, True)
        result["truncated"] = True
    return result
