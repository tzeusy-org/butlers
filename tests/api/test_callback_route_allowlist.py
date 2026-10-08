"""The connector callback credential reaches only its exact routes (RFC 0021, bu-ckkpz.3)."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from butlers.api.middleware import _is_approval_callback_route

pytestmark = pytest.mark.unit

_ID = "12345678-1234-5678-1234-567812345678"


def _request(method: str, path: str) -> Request:
    return Request(
        {"type": "http", "method": method, "path": path, "headers": [], "query_string": b""}
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/approvals/{_ID}"),
        ("POST", f"/api/approvals/{_ID}/approve"),
        ("POST", f"/api/approvals/{_ID}/deny"),
        ("GET", f"/api/decisions/prompts/{_ID}"),
        ("POST", f"/api/decisions/prompts/{_ID}/choose"),
    ],
)
def test_callback_routes_are_admitted(method, path) -> None:
    assert _is_approval_callback_route(_request(method, path)) is True


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/decisions"),
        ("POST", "/api/decisions/bu-a/intent"),
        ("POST", f"/api/decisions/prompts/{_ID}"),
        ("GET", f"/api/decisions/prompts/{_ID}/choose"),
        ("POST", f"/api/decisions/prompts/{_ID}/apply"),
        ("POST", "/api/decisions/prompts/not-a-uuid/choose"),
        ("POST", f"/api/decisions/prompts/{_ID}/choose/extra"),
        ("GET", "/api/approvals"),
        ("POST", f"/api/approvals/{_ID}/execute"),
    ],
)
def test_everything_else_is_refused(method, path) -> None:
    assert _is_approval_callback_route(_request(method, path)) is False
