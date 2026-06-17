"""Route-table contract: no legacy reveal route is mounted (bu-dl98i.1.2).

This test enumerates the ACTUAL mounted route table produced by ``create_app()``
and asserts that no route matching the legacy raw-secret reveal pattern
(``GET /api/butlers/{name}/secrets/{key}/reveal``) is present.

Motivation
----------
A string grep on source files can be fooled by comments, dead code, or a new
file that revives the endpoint.  This test asserts the invariant at the routing
layer — if the endpoint ever gets re-added, it will appear in FastAPI's route
table and this test will fail immediately.

Spec
----
openspec/specs/dashboard-admin-gateway/spec.md — "Write-only value masking: no
actual value retrieval" under Secrets and Credentials Management.

Removal history
---------------
The ``reveal_secret`` handler and its ``GET /{name}/secrets/{key}/reveal`` route
were removed in commit a60f2d814 (PR #2417, bu-dl98i.1.1).
"""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi.routing import APIRoute

from butlers.api.app import create_app

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Legacy pattern constants
# ---------------------------------------------------------------------------

# The exact FastAPI route-path template that was removed.
_LEGACY_REVEAL_ROUTE_PATH = "/api/butlers/{name}/secrets/{key}/reveal"

# Belt-and-suspenders: also catch any concrete-ish path that looks like the
# reveal endpoint (covers accidental partial renames such as {butler}/{secret}).
_LEGACY_REVEAL_PATH_RE = re.compile(r"^/api/butlers/[^/]+/secrets/[^/]+/reveal$")

# Minimum expected route count — proves that the traversal actually descended
# into the included sub-routers and did not only pick up the two health routes.
_MIN_EXPECTED_ROUTES = 50

# Representative request paths to probe with HTTP GET (404 expected for all).
_PROBE_PATHS = [
    "/api/butlers/atlas/secrets/ANTHROPIC_API_KEY/reveal",
    "/api/butlers/shared/secrets/BUTLER_TELEGRAM_TOKEN/reveal",
    "/api/butlers/deacon/secrets/any-key/reveal",
]


# ---------------------------------------------------------------------------
# Route-collection helper
# ---------------------------------------------------------------------------


def _collect_all_api_routes(app) -> list[APIRoute]:
    """Walk the app router tree and return every mounted ``APIRoute``.

    FastAPI stores ``include_router``-added sub-routers as ``_IncludedRouter``
    dataclass instances (not flat ``APIRoute`` objects) until routing is first
    used.  We must recurse into ``original_router.routes`` to discover all
    mounted endpoints.

    The traversal is deliberately duck-typed (``hasattr`` checks) to avoid
    coupling to private FastAPI internals by class name.
    """

    def _walk(routes: list) -> list[APIRoute]:
        found: list[APIRoute] = []
        for r in routes:
            if isinstance(r, APIRoute):
                found.append(r)
            elif hasattr(r, "original_router"):
                # _IncludedRouter: the real sub-router lives in original_router.
                found.extend(_walk(r.original_router.routes))
            elif hasattr(r, "routes"):
                # Mount or nested Router without the _IncludedRouter wrapper.
                found.extend(_walk(r.routes))
        return found

    return _walk(app.router.routes)


def _matches_legacy_reveal_pattern(route: APIRoute) -> bool:
    """Return True if *route* is (or looks like) the removed reveal endpoint."""
    methods = route.methods or set()
    if "GET" not in methods:
        return False
    # Exact template match.
    if route.path == _LEGACY_REVEAL_ROUTE_PATH:
        return True
    # Regex match against any normalised path variation.
    return bool(_LEGACY_REVEAL_PATH_RE.match(route.path))


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestNoRevealRouteContract:
    """Route-table contract: the legacy raw-secret GET reveal endpoint is absent.

    These tests operate at the FastAPI routing layer, not at the HTTP layer.
    They catch reintroduction of the endpoint even before a request is made.
    """

    def test_route_enumeration_traverses_included_routers(self):
        """_collect_all_api_routes descends into included sub-routers.

        This guard test ensures that the enumeration function is not silently
        returning only the two top-level health routes.  If fewer than
        ``_MIN_EXPECTED_ROUTES`` routes are found, the traversal is broken and
        all route-contract assertions in this class would be vacuously passing.
        """
        app = create_app()
        all_routes = _collect_all_api_routes(app)
        assert len(all_routes) >= _MIN_EXPECTED_ROUTES, (
            f"Route enumeration returned only {len(all_routes)} routes "
            f"(expected >= {_MIN_EXPECTED_ROUTES}).  "
            "The _collect_all_api_routes traversal may be broken — "
            "check for FastAPI internals changes affecting _IncludedRouter."
        )

    def test_no_get_route_matches_legacy_reveal_pattern(self):
        """No mounted GET route matches the legacy raw-secret reveal path.

        This is the primary structural invariant.  It catches reintroduction
        of the endpoint regardless of whether the handler would actually run,
        and regardless of which module re-adds it.
        """
        app = create_app()
        all_routes = _collect_all_api_routes(app)

        offending = [r for r in all_routes if _matches_legacy_reveal_pattern(r)]
        assert offending == [], (
            "Legacy raw-secret GET reveal route is mounted — security regression detected.  "
            "The following routes must be removed:\n"
            + "\n".join(f"  GET {r.path}" for r in offending)
            + "\n\nSee openspec/specs/dashboard-admin-gateway/spec.md "
            "and git commit a60f2d814 (PR #2417)."
        )

    def test_no_route_at_legacy_reveal_path_any_method(self):
        """No route exists at the legacy reveal path for ANY HTTP method.

        Belt-and-suspenders: even a POST or PATCH handler at the same path
        would indicate that reveal functionality is creeping back in.
        """
        app = create_app()
        all_routes = _collect_all_api_routes(app)

        at_reveal_path = [r for r in all_routes if r.path == _LEGACY_REVEAL_ROUTE_PATH]
        assert at_reveal_path == [], (
            "A route is mounted at the legacy reveal path (any method) — "
            "this is a security regression:\n"
            + "\n".join(f"  {r.methods} {r.path}" for r in at_reveal_path)
        )

    async def test_reveal_requests_return_404_for_representative_inputs(self):
        """Representative GET reveal requests all return HTTP 404.

        Exercises the live ASGI app to confirm that FastAPI's routing does not
        accidentally match the path under any concrete parameter value.
        """
        app = create_app()
        assert len(_PROBE_PATHS) == 3, "Probe paths list is empty or modified"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in _PROBE_PATHS:
                resp = await client.get(path)
                assert resp.status_code == 404, (
                    f"Expected HTTP 404 for {path!r} but got {resp.status_code}.  "
                    "The legacy plaintext-reveal endpoint must not be reachable."
                )
