"""Deterministic geocoding of the owner's anchor addresses (bu-r3fmja.3).

Openspec change ``chronicler-situational-inference``. Relationship owns the address
facts, so it owns their conversion to coordinates; Chronicler reads the result from
``relationship.address_geocodes`` to know where the owner's home and work are.

Only owner *anchor* addresses are ever sent to a geocoder -- active literal
``has-address`` facts on:

1. the owner entity itself;
2. an entity the owner has an active ``works-at`` edge to;
3. a place entity the owner has an active ``rental-location`` or ``lives-at`` edge to.

No contact's address and no location trail is ever sent.

Providers:

- **OneMap** (Singapore Land Authority) when the address is in Singapore: it names
  Singapore or ends in country code ``SG``, or carries a 6-digit postal code and names
  no other country. Credentials are optional ``CredentialStore`` keys
  ``ONEMAP_EMAIL``/``ONEMAP_PASSWORD``; without them search runs unauthenticated. An
  empty OneMap result falls back to Nominatim.
- **Nominatim** (OpenStreetMap) otherwise, with a fixed identifying User-Agent and at
  least one second between requests (Nominatim usage policy).

HTTP 429/5xx, other non-2xx responses, transport errors, timeouts and malformed bodies
are ``failed``; an empty result is ``not_found``. Both back off 1 h, 6 h, 24 h, then
doubling, capped at 7 days. A ``resolved`` row is not touched again until the fact's
text changes (its SHA-256 differs).

Privacy: no address text or coordinate is ever logged or returned. Log lines carry
fact ids, statuses and providers only; HTTP exceptions are reduced to their class name
or status code because httpx embeds the request URL (which contains the address).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

import asyncpg
import httpx

from butlers.credential_store import CredentialStore

_Conn = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy

logger = logging.getLogger(__name__)

# httpx's ordinary INFO request record carries the full URL, and a geocoder URL carries
# the address. Drop only the records of this module's own requests (same pattern as
# ``butlers.jobs.external_deadman``), without changing global levels.
_geocoder_http: ContextVar[bool] = ContextVar("owner_address_geocoder_http", default=False)


class _GeocoderRequestLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _geocoder_http.get()


logging.getLogger("httpx").addFilter(_GeocoderRequestLogFilter())


@contextmanager
def _quiet_http() -> Iterator[None]:
    token = _geocoder_http.set(True)
    try:
        yield
    finally:
        _geocoder_http.reset(token)


JOB_NAME = "relationship_geocode_owner_addresses"
LOCK_NAME = JOB_NAME
DEFAULT_BATCH_LIMIT = 20

ONEMAP_SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"
ONEMAP_TOKEN_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
#: Identifies the application per the Nominatim usage policy. Never put an email here.
NOMINATIM_USER_AGENT = "butlers-relationship-geocoder/1.0"
NOMINATIM_MIN_INTERVAL_SECONDS = 1.0

ONEMAP_EMAIL_KEY = "ONEMAP_EMAIL"
ONEMAP_PASSWORD_KEY = "ONEMAP_PASSWORD"

ONEMAP_CONFIDENCE = 0.9
NOMINATIM_CONFIDENCE = 0.7

_REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_COORD_QUANTUM = Decimal("0.00001")
_BACKOFF_STEPS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
_BACKOFF_CAP = timedelta(days=7)

Status = Literal["resolved", "not_found", "failed"]
Provider = Literal["onemap", "nominatim"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def address_sha256(address: str) -> str:
    """Return the contract hash of a ``has-address`` literal: SHA-256 of the stripped text."""
    return hashlib.sha256(address.strip().encode("utf-8")).hexdigest()


_SINGAPORE_NAME = re.compile(r"\bsingapore\b", re.IGNORECASE)
_SINGAPORE_CODE = re.compile(r"(?:^|[\s,])SG\.?$")
_SIX_DIGIT_POSTCODE = re.compile(r"(?<!\d)\d{6}(?!\d)")

# Countries an address carrying a 6-digit number could plausibly name instead of
# Singapore: every country that uses 6-digit postal codes, the region's neighbours, and
# the commonest foreign destinations. A miss costs one OneMap query whose empty result
# falls through to Nominatim.
_OTHER_COUNTRIES = (
    "australia",
    "belarus",
    "brunei",
    "cambodia",
    "canada",
    "china",
    "colombia",
    "ecuador",
    "france",
    "germany",
    "hong kong",
    "india",
    "indonesia",
    "ireland",
    "italy",
    "japan",
    "kazakhstan",
    "korea",
    "kyrgyzstan",
    "laos",
    "macau",
    "malaysia",
    "mongolia",
    "myanmar",
    "netherlands",
    "new zealand",
    "nigeria",
    "pakistan",
    "panama",
    "philippines",
    "romania",
    "russia",
    "russian federation",
    "spain",
    "switzerland",
    "taiwan",
    "tajikistan",
    "thailand",
    "trinidad",
    "tunisia",
    "turkmenistan",
    "uae",
    "united arab emirates",
    "united kingdom",
    "united states",
    "usa",
    "uk",
    "uzbekistan",
    "vietnam",
    "viet nam",
)
_OTHER_COUNTRY = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in _OTHER_COUNTRIES) + r")\b", re.IGNORECASE
)


def is_singapore_address(address: str) -> bool:
    """Return whether *address* should be geocoded by OneMap.

    Singapore when the text names Singapore, ends in country code ``SG``, or carries a
    6-digit postal code and names no other country.
    """
    text = address.strip()
    if not text:
        return False
    if _SINGAPORE_NAME.search(text) or _SINGAPORE_CODE.search(text):
        return True
    return bool(_SIX_DIGIT_POSTCODE.search(text)) and not _OTHER_COUNTRY.search(text)


def retry_delay(attempts: int) -> timedelta:
    """Backoff after the *attempts*-th consecutive not_found/failed attempt.

    1 h, 6 h, 24 h, then doubling (48 h, 96 h, ...), capped at 7 days.
    """
    if attempts <= len(_BACKOFF_STEPS):
        return _BACKOFF_STEPS[max(attempts, 1) - 1]
    doublings = attempts - len(_BACKOFF_STEPS)
    if doublings >= 8:  # 24 h * 2**8 is far past the cap; avoid huge timedeltas
        return _BACKOFF_CAP
    return min(_BACKOFF_STEPS[-1] * (2**doublings), _BACKOFF_CAP)


@dataclass(frozen=True)
class ExistingGeocode:
    """The stored geocode state the due check needs."""

    address_sha256: str
    status: str
    attempts: int
    next_attempt_at: datetime | None


def is_due(existing: ExistingGeocode | None, sha: str, now: datetime) -> bool:
    """Return whether a fact needs geocoding now.

    Due when it has no row, its text hash changed, or it is a not_found/failed row whose
    retry time has come.
    """
    if existing is None or existing.address_sha256 != sha:
        return True
    if existing.status == "resolved":
        return False
    return existing.next_attempt_at is None or existing.next_attempt_at <= now


def _coordinate(value: Any, *, limit: float) -> Decimal:
    number = float(value)  # ValueError/TypeError on junk
    if not math.isfinite(number) or abs(number) > limit:
        raise ValueError("coordinate out of range")
    return Decimal(repr(number)).quantize(_COORD_QUANTUM, rounding=ROUND_HALF_EVEN)


def _pair(lat: Any, lon: Any) -> tuple[Decimal, Decimal]:
    try:
        return _coordinate(lat, limit=90.0), _coordinate(lon, limit=180.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("malformed coordinates") from exc


def parse_onemap_response(payload: Any) -> tuple[Decimal, Decimal] | None:
    """Return the first OneMap result's coordinates, ``None`` when empty.

    Raises ``ValueError`` for a malformed body.
    """
    if not isinstance(payload, dict):
        raise ValueError("OneMap body is not an object")
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise ValueError("OneMap results is not a list")
    if not results:
        return None
    top = results[0]
    if not isinstance(top, dict):
        raise ValueError("OneMap result is not an object")
    return _pair(top.get("LATITUDE"), top.get("LONGITUDE"))


def parse_nominatim_response(payload: Any) -> tuple[Decimal, Decimal] | None:
    """Return the first Nominatim result's coordinates, ``None`` when empty.

    Raises ``ValueError`` for a malformed body.
    """
    if not isinstance(payload, list):
        raise ValueError("Nominatim body is not a list")
    if not payload:
        return None
    top = payload[0]
    if not isinstance(top, dict):
        raise ValueError("Nominatim result is not an object")
    return _pair(top.get("lat"), top.get("lon"))


# ---------------------------------------------------------------------------
# Provider client
# ---------------------------------------------------------------------------


class _SecretResolver(Protocol):
    async def resolve(self, key: str, *, env_fallback: bool = False) -> str | None: ...


@dataclass(frozen=True)
class GeocodeResult:
    status: Status
    provider: Provider | None
    lat: Decimal | None = None
    lon: Decimal | None = None
    confidence: float | None = None


class _ProviderFailure(Exception):
    """A provider request failed; ``reason`` is safe to log (no URL, no address)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _failure_reason(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return type(exc).__name__


class AddressGeocoder:
    """Geocode one address at a time through OneMap or Nominatim.

    One instance serves one job run: it fetches a OneMap token at most once and spaces
    Nominatim requests by at least :data:`NOMINATIM_MIN_INTERVAL_SECONDS`.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        credential_store: _SecretResolver,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._credentials = credential_store
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_checked = False
        self._last_nominatim: float | None = None

    async def geocode(self, address: str) -> GeocodeResult:
        if is_singapore_address(address):
            try:
                coords = await self._onemap(address)
            except _ProviderFailure as failure:
                logger.warning("OneMap geocode failed: %s", failure.reason)
                return GeocodeResult(status="failed", provider="onemap")
            if coords is not None:
                return GeocodeResult("resolved", "onemap", *coords, ONEMAP_CONFIDENCE)
        try:
            coords = await self._nominatim(address)
        except _ProviderFailure as failure:
            logger.warning("Nominatim geocode failed: %s", failure.reason)
            return GeocodeResult(status="failed", provider="nominatim")
        if coords is None:
            return GeocodeResult(status="not_found", provider="nominatim")
        return GeocodeResult("resolved", "nominatim", *coords, NOMINATIM_CONFIDENCE)

    async def _get_json(self, url: str, **kwargs: Any) -> Any:
        try:
            with _quiet_http():
                response = await self._client.get(url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise _ProviderFailure(_failure_reason(exc)) from None
        except ValueError:
            raise _ProviderFailure("invalid_json") from None

    async def _onemap(self, address: str) -> tuple[Decimal, Decimal] | None:
        token = await self._onemap_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        payload = await self._get_json(
            ONEMAP_SEARCH_URL,
            params={
                "searchVal": address,
                "returnGeom": "Y",
                "getAddrDetails": "Y",
                "pageNum": "1",
            },
            headers=headers,
        )
        try:
            return parse_onemap_response(payload)
        except ValueError:
            raise _ProviderFailure("malformed_response") from None

    async def _onemap_token(self) -> str | None:
        """Return a OneMap bearer token, or ``None`` for unauthenticated search."""
        if self._token_checked:
            return self._token
        self._token_checked = True
        try:
            email = await self._credentials.resolve(ONEMAP_EMAIL_KEY, env_fallback=False)
            password = await self._credentials.resolve(ONEMAP_PASSWORD_KEY, env_fallback=False)
        except Exception as exc:  # noqa: BLE001 - optional credentials; degrade to anonymous
            logger.warning(
                "OneMap credentials unavailable (%s); searching unauthenticated", type(exc).__name__
            )
            return None
        if not email or not password:
            return None
        try:
            with _quiet_http():
                response = await self._client.post(
                    ONEMAP_TOKEN_URL, json={"email": email, "password": password}
                )
            response.raise_for_status()
            token = response.json().get("access_token")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            reason = _failure_reason(exc) if isinstance(exc, httpx.HTTPError) else "invalid_json"
            logger.warning("OneMap token request failed (%s); searching unauthenticated", reason)
            return None
        if not isinstance(token, str) or not token:
            logger.warning("OneMap token response had no token; searching unauthenticated")
            return None
        self._token = token
        return token

    async def _nominatim(self, address: str) -> tuple[Decimal, Decimal] | None:
        if self._last_nominatim is not None:
            wait = NOMINATIM_MIN_INTERVAL_SECONDS - (self._clock() - self._last_nominatim)
            if wait > 0:
                await self._sleep(wait)
        try:
            payload = await self._get_json(
                NOMINATIM_SEARCH_URL,
                params={"q": address, "format": "json", "limit": "1"},
                headers={"User-Agent": NOMINATIM_USER_AGENT},
            )
        finally:
            self._last_nominatim = self._clock()
        try:
            return parse_nominatim_response(payload)
        except ValueError:
            raise _ProviderFailure("malformed_response") from None


# ---------------------------------------------------------------------------
# Anchor selection and persistence
# ---------------------------------------------------------------------------

#: Active literal ``has-address`` facts on the owner and on the owner's anchor entities.
#: Merged or deleted entities are excluded on both ends of an edge.
ANCHOR_ADDRESS_FACTS_SQL = """
WITH owner AS (
    SELECT e.id
    FROM public.entities e
    WHERE 'owner' = ANY(e.roles)
      AND (e.metadata->>'merged_into') IS NULL
      AND (e.metadata->>'deleted_at') IS NULL
),
anchor_entity AS (
    SELECT o.id AS entity_id FROM owner o
    UNION
    SELECT target.id
    FROM relationship.entity_facts edge
    JOIN owner o ON o.id = edge.subject
    JOIN public.entities target ON target.id = edge.object_entity_id
    WHERE edge.validity = 'active'
      AND edge.object_kind = 'entity'
      AND (
          edge.predicate = 'works-at'
          OR (edge.predicate IN ('rental-location', 'lives-at')
              AND target.entity_type = 'place')
      )
      AND (target.metadata->>'merged_into') IS NULL
      AND (target.metadata->>'deleted_at') IS NULL
)
SELECT f.id AS fact_id,
       f.object AS address,
       g.address_sha256,
       g.status,
       g.attempts,
       g.next_attempt_at
FROM relationship.entity_facts f
JOIN anchor_entity a ON a.entity_id = f.subject
LEFT JOIN relationship.address_geocodes g ON g.fact_id = f.id
WHERE f.validity = 'active'
  AND f.predicate = 'has-address'
  AND f.object_kind = 'literal'
ORDER BY f.id
"""

_UPSERT_SQL = """
INSERT INTO relationship.address_geocodes (
    fact_id, address_sha256, status, lat, lon, provider, confidence,
    attempts, geocoded_at, next_attempt_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (fact_id) DO UPDATE SET
    address_sha256 = EXCLUDED.address_sha256,
    status = EXCLUDED.status,
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    provider = EXCLUDED.provider,
    confidence = EXCLUDED.confidence,
    attempts = EXCLUDED.attempts,
    geocoded_at = EXCLUDED.geocoded_at,
    next_attempt_at = EXCLUDED.next_attempt_at
"""

_PRUNE_SQL = """
DELETE FROM relationship.address_geocodes
WHERE NOT (fact_id = ANY($1::uuid[]))
"""


@dataclass(frozen=True)
class AnchorAddressFact:
    fact_id: UUID
    address: str
    existing: ExistingGeocode | None


async def select_anchor_address_facts(conn: _Conn) -> list[AnchorAddressFact]:
    """Return every owner anchor ``has-address`` fact with its stored geocode state."""
    rows = await conn.fetch(ANCHOR_ADDRESS_FACTS_SQL)
    return [
        AnchorAddressFact(
            fact_id=row["fact_id"],
            address=row["address"],
            existing=(
                None
                if row["status"] is None
                else ExistingGeocode(
                    address_sha256=row["address_sha256"],
                    status=row["status"],
                    attempts=row["attempts"],
                    next_attempt_at=row["next_attempt_at"],
                )
            ),
        )
        for row in rows
    ]


def _due_order(fact: AnchorAddressFact, sha: str) -> tuple[int, datetime, str]:
    """New or changed text first, then the longest-overdue retries."""
    existing = fact.existing
    if existing is None or existing.address_sha256 != sha:
        return (0, datetime.min.replace(tzinfo=UTC), str(fact.fact_id))
    return (
        1,
        existing.next_attempt_at or datetime.min.replace(tzinfo=UTC),
        str(fact.fact_id),
    )


async def run_owner_address_geocoding(
    pool: asyncpg.Pool,
    *,
    credential_store: _SecretResolver | None = None,
    http_client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> dict[str, Any]:
    """Geocode due owner anchor addresses, at most *limit* per run.

    Returns counts only -- never an address or coordinate. A run that finds another in
    progress returns ``{"status": "skipped", "reason": "already_running"}``.
    """
    effective_now = now or datetime.now(UTC)
    store = credential_store or CredentialStore(pool)
    counts = {"resolved": 0, "not_found": 0, "failed": 0}

    async with pool.acquire() as conn:
        locked = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_NAME)
        if not locked:
            return {"status": "skipped", "reason": "already_running"}
        try:
            facts = await select_anchor_address_facts(conn)
            pruned_tag = await conn.execute(_PRUNE_SQL, [f.fact_id for f in facts])
            pruned = int(pruned_tag.split()[-1]) if pruned_tag else 0

            hashed = [(fact, address_sha256(fact.address)) for fact in facts]
            due = sorted(
                (
                    (fact, sha)
                    for fact, sha in hashed
                    if fact.address.strip() and is_due(fact.existing, sha, effective_now)
                ),
                key=lambda item: _due_order(*item),
            )
            batch = due[: max(limit, 0)]

            owns_client = http_client is None
            client = http_client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)
            try:
                geocoder = AddressGeocoder(client, credential_store=store, sleep=sleep, clock=clock)
                by_sha: dict[str, GeocodeResult] = {}
                for fact, sha in batch:
                    result = by_sha.get(sha)
                    if result is None:
                        result = await geocoder.geocode(fact.address.strip())
                        by_sha[sha] = result
                    await _store(conn, fact, sha, result, effective_now)
                    counts[result.status] += 1
            finally:
                if owns_client:
                    await client.aclose()
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", LOCK_NAME)

    summary = {
        "status": "ok",
        "anchor_facts": len(facts),
        "due": len(due),
        "attempted": len(batch),
        **counts,
        "deferred": len(due) - len(batch),
        "pruned": pruned,
    }
    logger.info(
        "%s: anchors=%d due=%d attempted=%d resolved=%d not_found=%d failed=%d pruned=%d",
        JOB_NAME,
        summary["anchor_facts"],
        summary["due"],
        summary["attempted"],
        summary["resolved"],
        summary["not_found"],
        summary["failed"],
        summary["pruned"],
    )
    return summary


async def _store(
    conn: _Conn,
    fact: AnchorAddressFact,
    sha: str,
    result: GeocodeResult,
    now: datetime,
) -> None:
    existing = fact.existing
    attempts = (
        existing.attempts + 1 if existing is not None and existing.address_sha256 == sha else 1
    )
    next_attempt_at = None if result.status == "resolved" else now + retry_delay(attempts)
    try:
        await conn.execute(
            _UPSERT_SQL,
            fact.fact_id,
            sha,
            result.status,
            result.lat,
            result.lon,
            result.provider,
            result.confidence,
            attempts,
            now,
            next_attempt_at,
        )
    except asyncpg.ForeignKeyViolationError:
        # The fact was hard-deleted mid-run; its geocode has nothing to attach to.
        logger.info("%s: fact=%s vanished before its geocode was stored", JOB_NAME, fact.fact_id)
        return
    logger.info(
        "%s: fact=%s status=%s provider=%s attempts=%d",
        JOB_NAME,
        fact.fact_id,
        result.status,
        result.provider,
        attempts,
    )
