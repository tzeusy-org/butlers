"""Unit tests for Relationship's owner anchor-address geocoder (bu-r3fmja.3).

Openspec change ``chronicler-situational-inference``: provider selection (OneMap for
Singapore, Nominatim otherwise), response parsing, the not_found/failed backoff schedule, and the
HTTP contract of each provider. All HTTP is served by ``httpx.MockTransport``;
no test reaches a live geocoder.

Cases are grouped into few test functions (each assertion names its case) to keep
the unit lane inside its collected-test budget.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest

# The roster tools package is mounted as ``butlers.tools.relationship`` at runtime;
# the static checker cannot resolve that mount, so load it by name.
geo = importlib.import_module("butlers.tools.relationship.owner_address_geocoding")
NOMINATIM_USER_AGENT = geo.NOMINATIM_USER_AGENT
AddressGeocoder = geo.AddressGeocoder
ExistingGeocode = geo.ExistingGeocode
GeocodeResult = geo.GeocodeResult
address_sha256 = geo.address_sha256
is_due = geo.is_due
is_singapore_address = geo.is_singapore_address
parse_nominatim_response = geo.parse_nominatim_response
parse_onemap_response = geo.parse_onemap_response
retry_delay = geo.retry_delay

pytestmark = pytest.mark.unit

_SG = "1 Shenton Way, Singapore 068803"
_LONDON = "221B Baker Street, London NW1 6XE, United Kingdom"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_address_hash_and_provider_selection() -> None:
    expected = hashlib.sha256(_SG.encode("utf-8")).hexdigest()
    assert address_sha256(f"  {_SG}\n") == expected == address_sha256(_SG)

    singapore = [
        _SG,
        "10 Anson Road, S079903",
        "Blk 123 Ang Mo Kio Ave 3 #05-12, 560123",
        "8 Marina View, Asia Square Tower 1, SG",
        "1 Fusionopolis Way, singapore",
        "Changi Airport Terminal 3, Republic of Singapore",
    ]
    elsewhere = [
        _LONDON,
        "MG Road, Bengaluru 560001, India",
        "1 Jianguomenwai Avenue, Beijing 100004, China",
        "Tverskaya 7, Moscow 125009, Russia",
        "1600 Amphitheatre Pkwy, Mountain View, CA 94043",
        "Jalan Ampang, 50450 Kuala Lumpur, Malaysia",
        "Alexanderplatz 1, 10178 Berlin",
        "",
    ]
    assert [a for a in singapore if not is_singapore_address(a)] == []
    assert [a for a in elsewhere if is_singapore_address(a)] == []


def test_backoff_schedule_and_due_check() -> None:
    hours = {0: 1, 1: 1, 2: 6, 3: 24, 4: 48, 5: 96, 6: 168, 7: 168, 40: 168}
    assert {n: retry_delay(n) for n in hours} == {n: timedelta(hours=h) for n, h in hours.items()}

    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    sha = address_sha256(_SG)

    def row(status: str, *, other_text: bool = False, next_at: datetime | None = None):
        return ExistingGeocode(
            address_sha256="0" * 64 if other_text else sha,
            status=status,
            attempts=1,
            next_attempt_at=next_at,
        )

    cases = {
        "no row": (None, True),
        "resolved, same text": (row("resolved"), False),
        "resolved, text changed": (row("resolved", other_text=True), True),
        "not_found, retry later": (row("not_found", next_at=now + timedelta(minutes=1)), False),
        "not_found, retry now": (row("not_found", next_at=now), True),
        "failed, retry overdue": (row("failed", next_at=now - timedelta(hours=2)), True),
        "failed, retry later": (row("failed", next_at=now + timedelta(hours=2)), False),
        "failed, text changed": (
            row("failed", other_text=True, next_at=now + timedelta(days=3)),
            True,
        ),
        "failed, no retry time": (row("failed"), True),
    }
    assert {name: is_due(existing, sha, now) for name, (existing, _) in cases.items()} == {
        name: due for name, (_, due) in cases.items()
    }


def test_response_parsing() -> None:
    onemap = {"found": 1, "results": [{"LATITUDE": "1.27930123456", "LONGITUDE": "103.850612345"}]}
    assert parse_onemap_response(onemap) == (Decimal("1.27930"), Decimal("103.85061"))
    assert parse_onemap_response({"found": 0, "results": []}) is None
    assert parse_onemap_response({"found": 0}) is None
    nominatim = [{"lat": "51.5237629", "lon": "-0.1584743", "display_name": "x"}]
    assert parse_nominatim_response(nominatim) == (Decimal("51.52376"), Decimal("-0.15847"))
    assert parse_nominatim_response([]) is None

    malformed_onemap = [
        {"results": [{"LATITUDE": "NaN", "LONGITUDE": "103.8"}]},
        {"results": [{"LATITUDE": "abc", "LONGITUDE": "103.8"}]},
        {"results": [{"LONGITUDE": "103.8"}]},
        {"results": [{"LATITUDE": "91", "LONGITUDE": "103.8"}]},
        {"results": "nope"},
        ["not", "a", "dict"],
    ]
    malformed_nominatim = [[{"lat": "51.5"}], [{"lat": "1", "lon": "181"}], {"lat": "1"}]
    for parse, payloads in (
        (parse_onemap_response, malformed_onemap),
        (parse_nominatim_response, malformed_nominatim),
    ):
        for payload in payloads:
            with pytest.raises(ValueError):
                parse(payload)


# ---------------------------------------------------------------------------
# Provider HTTP contract
# ---------------------------------------------------------------------------


class _FakeCredentials:
    def __init__(self, values: dict[str, str] | None = None, *, error: bool = False) -> None:
        self.values = values or {}
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def resolve(self, key: str, *, env_fallback: bool = False) -> str | None:
        self.calls.append((key, env_fallback))
        if self.error:
            raise RuntimeError("store unavailable")
        return self.values.get(key)


class _Clock:
    """A monotonic clock that only moves when the geocoder sleeps (or a test says so)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


_ONEMAP_HIT = {"found": 1, "results": [{"LATITUDE": "1.2793", "LONGITUDE": "103.8506"}]}
_ONEMAP_EMPTY = {"found": 0, "totalNumPages": 0, "pageNum": 1, "results": []}
_NOMINATIM_HIT = [{"lat": "51.5237629", "lon": "-0.1584743"}]
_CREDS = {"ONEMAP_EMAIL": "e@x.test", "ONEMAP_PASSWORD": "pw"}


def _geocoder(
    handler,
    *,
    credentials: _FakeCredentials | None = None,
) -> tuple[Any, list[httpx.Request], _Clock]:
    requests: list[httpx.Request] = []
    clock = _Clock()

    def _record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    geocoder = AddressGeocoder(
        client,
        credential_store=credentials or _FakeCredentials(),
        sleep=clock.sleep,
        clock=clock,
    )
    return geocoder, requests, clock


def _respond(spec: Any, request: httpx.Request) -> httpx.Response:
    if callable(spec):
        return cast(httpx.Response, spec(request))
    return httpx.Response(200, json=spec)


def _route(*, onemap=None, nominatim=None, token=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.onemap.gov.sg" and request.url.path.endswith("/getToken"):
            return _respond(token, request)
        if request.url.host == "www.onemap.gov.sg":
            return _respond(onemap, request)
        if request.url.host == "nominatim.openstreetmap.org":
            return _respond(nominatim, request)
        raise AssertionError(f"unexpected host {request.url.host}")

    return handler


async def test_onemap_request_contract_and_optional_credentials() -> None:
    # No credentials: unauthenticated search, credentials looked up in the store only.
    credentials = _FakeCredentials()
    geocoder, requests, _ = _geocoder(_route(onemap=_ONEMAP_HIT), credentials=credentials)
    assert await geocoder.geocode(_SG) == GeocodeResult(
        "resolved", "onemap", Decimal("1.27930"), Decimal("103.85060"), 0.9
    )
    (request,) = requests
    assert request.method == "GET"
    assert str(request.url).startswith(geo.ONEMAP_SEARCH_URL)
    assert dict(request.url.params) == {
        "searchVal": _SG,
        "returnGeom": "Y",
        "getAddrDetails": "Y",
        "pageNum": "1",
    }
    assert "authorization" not in request.headers
    assert sorted(credentials.calls) == [("ONEMAP_EMAIL", False), ("ONEMAP_PASSWORD", False)]

    # Credentials: one token request per run, then Bearer on every search.
    token_bodies: list[dict[str, Any]] = []

    def token(request: httpx.Request) -> httpx.Response:
        token_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"access_token": "tok-123", "expiry_timestamp": "1"})

    geocoder, requests, _ = _geocoder(
        _route(onemap=_ONEMAP_HIT, token=token), credentials=_FakeCredentials(_CREDS)
    )
    await geocoder.geocode(_SG)
    await geocoder.geocode("2 Shenton Way, Singapore 068804")
    assert token_bodies == [{"email": "e@x.test", "password": "pw"}]
    token_request, *searches = requests
    assert (token_request.method, str(token_request.url)) == ("POST", geo.ONEMAP_TOKEN_URL)
    assert [r.headers["authorization"] for r in searches] == ["Bearer tok-123"] * 2

    # A refused or tokenless token response, or a store error, degrades to anonymous search.
    for token_response, store in (
        (httpx.Response(401, json={"error": "bad"}), _FakeCredentials(_CREDS)),
        (httpx.Response(200, json={"nope": 1}), _FakeCredentials(_CREDS)),
        (None, _FakeCredentials(error=True)),
    ):
        geocoder, requests, _ = _geocoder(
            _route(onemap=_ONEMAP_HIT, token=lambda _r, resp=token_response: resp),
            credentials=store,
        )
        assert (await geocoder.geocode(_SG)).status == "resolved"
        assert "authorization" not in requests[-1].headers


async def test_onemap_empty_falls_back_to_nominatim_but_errors_do_not() -> None:
    geocoder, requests, _ = _geocoder(_route(onemap=_ONEMAP_EMPTY, nominatim=_NOMINATIM_HIT))
    assert await geocoder.geocode(_SG) == GeocodeResult(
        "resolved", "nominatim", Decimal("51.52376"), Decimal("-0.15847"), 0.7
    )
    assert [r.url.host for r in requests] == ["www.onemap.gov.sg", "nominatim.openstreetmap.org"]

    geocoder, requests, _ = _geocoder(_route(onemap=lambda _r: httpx.Response(503)))
    assert await geocoder.geocode(_SG) == GeocodeResult("failed", "onemap")
    assert [r.url.host for r in requests] == ["www.onemap.gov.sg"]


async def test_nominatim_request_contract_and_statuses() -> None:
    geocoder, requests, _ = _geocoder(_route(nominatim=_NOMINATIM_HIT))
    result = await geocoder.geocode(_LONDON)
    assert (result.status, result.provider, result.confidence) == ("resolved", "nominatim", 0.7)
    (request,) = requests
    assert str(request.url).startswith(geo.NOMINATIM_SEARCH_URL)
    assert dict(request.url.params) == {"q": _LONDON, "format": "json", "limit": "1"}
    assert request.headers["user-agent"] == NOMINATIM_USER_AGENT
    assert NOMINATIM_USER_AGENT == "butlers-relationship-geocoder/1.0"

    geocoder, _, _ = _geocoder(_route(nominatim=[]))
    assert await geocoder.geocode(_LONDON) == GeocodeResult("not_found", "nominatim")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    failures = {
        "429": lambda _r: httpx.Response(429),
        "500": lambda _r: httpx.Response(500),
        "502": lambda _r: httpx.Response(502),
        "not json": lambda _r: httpx.Response(200, content=b"<html>not json</html>"),
        "junk coordinates": lambda _r: httpx.Response(200, json=[{"lat": "x", "lon": "y"}]),
        "timeout": timeout,
    }
    outcomes = {}
    for name, response in failures.items():
        geocoder, _, _ = _geocoder(_route(nominatim=response))
        outcomes[name] = await geocoder.geocode(_LONDON)
    assert outcomes == dict.fromkeys(failures, GeocodeResult("failed", "nominatim"))


async def test_nominatim_requests_are_spaced_at_least_one_second_apart() -> None:
    geocoder, requests, clock = _geocoder(_route(nominatim=_NOMINATIM_HIT))
    stamps: list[float] = []
    for _ in range(3):
        await geocoder.geocode(_LONDON)
        stamps.append(clock.now)
    assert len(requests) == 3
    assert clock.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]
    assert all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:], strict=False))

    # No wait when a second has already passed.
    clock.sleeps.clear()
    clock.now += 5.0
    await geocoder.geocode(_LONDON)
    assert clock.sleeps == []


async def test_geocoder_never_logs_the_address(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    geocoder, _, _ = _geocoder(
        _route(onemap=lambda _r: httpx.Response(500), nominatim=lambda _r: httpx.Response(429))
    )

    await geocoder.geocode(_SG)
    await geocoder.geocode(_LONDON)

    assert "OneMap geocode failed: http_500" in caplog.text
    for text in ("Shenton", "068803", "Baker", "NW1"):
        assert text not in caplog.text
