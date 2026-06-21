"""Tests for the dashboard audit middleware and audit_emit helper.

Condensed: 26 → ~14 tests [bu-gg4y1].
Keeps: redact_body contract (parametrized), emit integration (insert/noop/swallow),
middleware fires on DELETE/POST, skips GET/health, trace-id header, audit endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.audit_emit import build_user_context, emit_dashboard_audit, redact_body
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Unit: redact_body (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected_redacted,expected_kept",
    [
        (
            {"type": "email", "value": "secret@example.com", "password": "x", "token": "t"},
            ["value", "password", "token"],
            ["type"],
        ),
        ({"name": "Alice", "is_primary": False}, [], ["name", "is_primary"]),
        ({}, [], []),
        ({"Password": "abc", "API_KEY": "key"}, ["Password", "API_KEY"], []),
    ],
)
def test_redact_body(body, expected_redacted, expected_kept):
    result = redact_body(body)
    for k in expected_redacted:
        assert result[k] == "[REDACTED]"
    for k in expected_kept:
        assert result[k] == body[k]


def test_redact_body_nested_sensitive_key_redacts_whole_value():
    body = {"credentials": {"password": "x", "username": "alice"}}
    assert redact_body(body)["credentials"] == "[REDACTED]"


def test_redact_body_non_sensitive_nesting_recurses():
    body = {"metadata": {"password": "x", "label": "prod"}}
    result = redact_body(body)
    assert result["metadata"]["password"] == "[REDACTED]"
    assert result["metadata"]["label"] == "prod"


def test_redact_body_does_not_mutate_original():
    body = {"metadata": {"password": "secret", "label": "prod"}}
    original_inner = body["metadata"].copy()
    redact_body(body)
    assert body["metadata"] == original_inner


# ---------------------------------------------------------------------------
# Unit: emit_dashboard_audit
# ---------------------------------------------------------------------------


class TestEmitDashboardAudit:
    """As of bu-h47nm, emit_dashboard_audit routes through audit.append() into
    the canonical ``public.audit_log`` table (no more dashboard_audit_log write).

    append() uses ``pool.fetchval`` (RETURNING id); the legacy column shape is
    mapped onto append params: butler->actor, operation->action, path->target,
    request_summary/user_context->metadata JSONB.
    """

    async def test_inserts_row_on_success(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        await emit_dashboard_audit(
            mock_db,
            butler="relationship",
            operation="contact_info_delete",
            method="DELETE",
            path="/api/relationship/contacts/abc/contact-info/xyz",
            path_params={"contact_id": "abc", "info_id": "xyz"},
            response_status=204,
        )

        mock_pool.fetchval.assert_awaited_once()
        call_args = mock_pool.fetchval.call_args[0]
        assert "INSERT INTO public.audit_log" in call_args[0]
        assert "dashboard_audit_log" not in call_args[0]
        # actor <- butler, action <- operation, target <- path
        assert call_args[1] == "relationship"
        assert call_args[2] == "contact_info_delete"
        assert call_args[3] == "/api/relationship/contacts/abc/contact-info/xyz"
        # metadata ($7) is a JSON string carrying request_summary + user_context.
        metadata_json = call_args[7]
        assert isinstance(metadata_json, str)
        assert '"request_summary"' in metadata_json
        # user_context defaults to owner principal even when no request/context
        # is supplied — never the legacy empty dict.
        assert '"principal": "owner"' in metadata_json
        assert '"source": "dashboard"' in metadata_json

    async def test_explicit_user_context_overrides_default(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        await emit_dashboard_audit(
            mock_db,
            butler="approvals",
            operation="suggestion.confirm",
            method="POST",
            path="/api/approvals/suggestions/abc/confirm",
            user_context={"principal": "owner", "actor": "dashboard:rest-api"},
        )

        metadata_json = mock_pool.fetchval.call_args[0][7]
        assert '"actor": "dashboard:rest-api"' in metadata_json

    async def test_noop_when_db_manager_is_none(self):
        # Should not raise
        await emit_dashboard_audit(
            None,
            butler="relationship",
            operation="test",
            method="DELETE",
            path="/api/test",
        )

    async def test_swallows_db_errors(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(side_effect=RuntimeError("db gone"))
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        # Should not raise
        await emit_dashboard_audit(
            mock_db,
            butler="relationship",
            operation="test",
            method="DELETE",
            path="/api/test",
        )

    async def test_body_redaction_applied(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        await emit_dashboard_audit(
            mock_db,
            butler="test",
            operation="op",
            method="POST",
            path="/api/test",
            body={"type": "email", "value": "secret@example.com"},
        )

        # Sensitive body fields are redacted before landing in metadata JSONB.
        metadata_json = mock_pool.fetchval.call_args[0][7]
        assert isinstance(metadata_json, str)
        assert '"type": "email"' in metadata_json
        assert "[REDACTED]" in metadata_json
        assert "secret@example.com" not in metadata_json


# ---------------------------------------------------------------------------
# Unit: build_user_context
# ---------------------------------------------------------------------------


class TestBuildUserContext:
    def test_default_principal_is_owner_without_request(self):
        ctx = build_user_context()
        assert ctx == {"principal": "owner", "source": "dashboard"}

    def test_extracts_client_ip_and_headers_from_request(self):
        from starlette.requests import Request as StarletteRequest

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/relationship/contacts",
            "headers": [
                (b"x-api-key", b"super-secret"),
                (b"user-agent", b"butlers-cli/0.1"),
                (b"x-forwarded-for", b"203.0.113.7, 10.0.0.1"),
            ],
            "client": ("10.0.0.1", 51234),
            "query_string": b"",
        }
        request = StarletteRequest(scope)
        ctx = build_user_context(request)

        assert ctx["principal"] == "owner"
        assert ctx["source"] == "dashboard"
        assert ctx["client_ip"] == "10.0.0.1"
        assert ctx["forwarded_for"] == "203.0.113.7"
        assert ctx["user_agent"] == "butlers-cli/0.1"
        assert ctx["api_key_authenticated"] is True
        # The raw API key value must never appear in user_context.
        assert "super-secret" not in str(ctx)

    def test_extra_overrides_and_augments_defaults(self):
        ctx = build_user_context(extra={"actor": "dashboard:rest-api"})
        assert ctx["principal"] == "owner"
        assert ctx["actor"] == "dashboard:rest-api"


# ---------------------------------------------------------------------------
# Integration: middleware fires on DELETE, skips GET
# ---------------------------------------------------------------------------


class TestDashboardAuditMiddleware:
    """Integration tests for the middleware using a real FastAPI test client."""

    def _make_app_with_mock_db(self):
        """Create an app where get_db_manager returns a mock that records execute calls."""
        app = create_app(api_key="")
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool
        return app, mock_db, mock_pool

    async def test_middleware_skips_get(self):
        """A GET to /api/ does NOT write an audit row."""
        app, mock_db, mock_pool = self._make_app_with_mock_db()

        with patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db):

            @app.get("/api/test-get-no-audit")
            async def _get_endpoint():
                return {"ok": True}

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/test-get-no-audit")

        assert resp.status_code == 200
        # No audit append should have fired for a GET.
        audit_calls = [
            call for call in mock_pool.fetchval.call_args_list if "public.audit_log" in str(call)
        ]
        assert audit_calls == [], f"Expected no audit rows for GET, got: {audit_calls}"

    async def test_middleware_skips_health(self):
        """GET /api/health is not audited."""
        app, mock_db, mock_pool = self._make_app_with_mock_db()
        app.state.ready = True  # simulate completed lifespan startup so health returns 200

        with patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/health")

        assert resp.status_code == 200
        audit_calls = [c for c in mock_pool.fetchval.call_args_list if "public.audit_log" in str(c)]
        assert audit_calls == []

    async def test_middleware_records_method_and_path(self):
        """Audit row metadata carries the request_summary (method + path); the
        path is also surfaced on the dedicated ``target`` column (bu-h47nm)."""
        import json as _json

        app, mock_db, mock_pool = self._make_app_with_mock_db()

        with patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db):

            @app.delete("/api/test-detail-check")
            async def _detail_endpoint():
                return {}

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.delete("/api/test-detail-check")

        # append() args: 0=sql 1=actor 2=action 3=target 4=note 5=ip
        #                6=request_id 7=metadata_json(str) 8=result 9=error
        audit_calls = [
            call for call in mock_pool.fetchval.call_args_list if "public.audit_log" in str(call)
        ]
        assert audit_calls, "No audit INSERT found"
        call_args = audit_calls[-1][0]
        # target column carries the request path.
        assert "/api/test-detail-check" in call_args[3]
        # metadata JSONB carries the full request_summary.
        metadata = _json.loads(call_args[7])
        summary = metadata["request_summary"]
        assert summary["method"] == "DELETE"
        assert "/api/test-detail-check" in summary["path"]

    async def test_middleware_populates_user_context(self):
        """Middleware emits a user_context with owner principal + request metadata.

        Regression for bu-sz7q3: prior to the fix the middleware always wrote
        ``user_context={}``, leaving every audit row unattributable.
        """
        app, mock_db, mock_pool = self._make_app_with_mock_db()

        with patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db):

            @app.post("/api/test-user-context")
            async def _user_context_endpoint():
                return {"ok": True}

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/api/test-user-context",
                    json={"k": "v"},
                    headers={"X-API-Key": "ignored", "User-Agent": "pytest-suite"},
                )

        import json as _json

        audit_calls = [c for c in mock_pool.fetchval.call_args_list if "public.audit_log" in str(c)]
        assert audit_calls, "Expected audit INSERT but found none"
        # user_context now lives inside the metadata JSONB column (index 7).
        metadata = _json.loads(audit_calls[-1][0][7])
        user_context = metadata["user_context"]
        assert isinstance(user_context, dict)
        assert user_context, "user_context must not be empty"
        assert user_context["principal"] == "owner"
        assert user_context["source"] == "dashboard"
        assert user_context["api_key_authenticated"] is True
        assert user_context["user_agent"] == "pytest-suite"

    async def test_x_trace_id_header_present_and_matches_audit_row(self):
        """X-Trace-Id response header is present and matches the trace_id in the audit row."""
        import uuid as _uuid

        app, mock_db, mock_pool = self._make_app_with_mock_db()

        with patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=mock_db):

            @app.patch("/api/test-trace-header")
            async def _patch_endpoint():
                return {"updated": True}

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.patch("/api/test-trace-header", json={"key": "val"})

        # Response must carry X-Trace-Id
        assert "x-trace-id" in resp.headers, "X-Trace-Id header missing from response"
        header_trace_id = resp.headers["x-trace-id"]

        # The value must be a valid UUID
        _uuid.UUID(header_trace_id)  # raises ValueError if not a UUID

        # The same trace_id must appear in the audit append() call.
        # append() args: 0=sql 1=actor 2=action 3=target 4=note 5=ip
        #                6=request_id 7=metadata_json(str) 8=result 9=error
        import json as _json

        audit_calls = [
            call for call in mock_pool.fetchval.call_args_list if "public.audit_log" in str(call)
        ]
        assert audit_calls, "No audit INSERT found"
        call_args = audit_calls[-1][0]
        # The trace_id is surfaced on the request_id column AND inside metadata.
        assert str(call_args[6]) == header_trace_id, (
            f"X-Trace-Id header ({header_trace_id!r}) does not match "
            f"audit row request_id ({call_args[6]!r})"
        )
        metadata = _json.loads(call_args[7])
        audit_trace_id = metadata["request_summary"].get("trace_id")
        assert audit_trace_id == header_trace_id


# ---------------------------------------------------------------------------
# Integration: audit READ endpoint not broken
# ---------------------------------------------------------------------------


class TestAuditReadEndpoint:
    async def test_get_audit_log_returns_paginated_structure(self):
        """GET /api/audit-log still works correctly after middleware changes."""
        from butlers.api.routers.audit import _get_db_manager as _audit_get_db

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        # The merged read path also queries the legacy dashboard_audit_log via
        # the switchboard pool (bu-isi4i); reuse the empty pool.
        mock_db.pool.return_value = mock_pool

        app = create_app(api_key="")
        app.dependency_overrides[_audit_get_db] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/audit-log")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert body["data"] == []


# ---------------------------------------------------------------------------
# Unit: _infer_butler path parsing
# ---------------------------------------------------------------------------


class TestInferButler:
    def test_relationship_path(self):
        from butlers.api.dashboard_audit_middleware import _infer_butler

        assert _infer_butler("/api/relationship/contacts/abc/contact-info/xyz") == "relationship"

    def test_butlers_path(self):
        from butlers.api.dashboard_audit_middleware import _infer_butler

        assert _infer_butler("/api/butlers/atlas/runtime-config") == "butlers"

    def test_audit_log_path_returns_dashboard(self):
        from butlers.api.dashboard_audit_middleware import _infer_butler

        assert _infer_butler("/api/audit-log") == "dashboard"
        assert _infer_butler("/api/health") == "dashboard"
