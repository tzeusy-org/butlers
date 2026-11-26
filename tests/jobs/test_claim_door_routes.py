"""Every producer's claim door lands on a route the dashboard declares (bu-5ii4gj.13 D1)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from butlers.jobs import calendar_overlay as job

_ROUTER = Path(__file__).resolve().parents[2] / "frontend" / "src" / "router-config.tsx"
_PATH = re.compile(r"""path:\s*['"]([^'"]+)['"]""")


def _declared_patterns() -> list[re.Pattern[str]]:
    paths = _PATH.findall(_ROUTER.read_text(encoding="utf-8"))
    assert "/calendar" in paths, "router-config.tsx no longer declares paths this test can read"
    return [
        re.compile("^" + re.sub(r":[^/]+", "[^/]+", path.rstrip("*").rstrip("/")) + "/?$")
        for path in paths
        if path != "*"
    ]


def _resolves(route: str) -> bool:
    return any(pattern.match(route) for pattern in _declared_patterns())


def test_every_producer_door_route_is_a_declared_dashboard_route():
    missing = {name: route for name, route in job.DOOR_ROUTES.items() if not _resolves(route)}
    assert missing == {}


def test_the_claims_the_producers_build_use_those_routes():
    zone = ZoneInfo("Asia/Singapore")
    day = date(2030, 3, 4)
    at = datetime(2030, 3, 4, 9, tzinfo=UTC)
    claims = [
        job._day_claim(
            key="education:reviews:x",
            version="1",
            kind="k",
            label="l",
            priority="low",
            presence="overlay",
            day=day,
            zone=zone,
            ref="x",
            route=job.DOOR_ROUTES["education"],
        ),
        job._timed_claim(
            key="travel:leg:x",
            version="1",
            kind="k",
            label="l",
            priority="low",
            presence="block",
            start=at,
            end=at + timedelta(hours=1),
            zone=zone,
            ref="x",
        ),
    ]
    for claim in claims:
        assert _resolves(claim["door"]["route"]), claim["door"]


def test_the_route_check_rejects_a_route_the_dashboard_does_not_declare():
    assert not _resolves("/finance")
    assert not _resolves("/travel")
    assert _resolves("/butlers/finance")
