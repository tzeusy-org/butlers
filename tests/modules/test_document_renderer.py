"""Unit tests for the document_renderer module.

Covers:
- Module interface (name, config_schema, dependencies, migration_revisions)
- Tool registration: render_document and render_chart are registered
- on_startup: stores blob_store; warns when None
- on_shutdown: clears blob_store
- render_document: markdown->pdf, html->pdf, missing blob_store error,
  unsupported format, unsupported content_type, PDF backend error,
  blob store write error
- render_chart: chart->png, chart->svg, missing blob_store error,
  unsupported format, invalid JSON, missing data field, render error,
  blob store write error
- _markdown_to_html: produces valid HTML fragment
- _render_chart_svg / _render_chart_png: produce non-empty output
- DocumentRendererModule is discoverable via default_registry

[bu-3x58k]
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.document_renderer import (
    DocumentRendererModule,
    _markdown_to_html,
    _render_chart_png,
    _render_chart_svg,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubMCP:
    """Minimal FastMCP stub that collects tool registrations."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _make_blob_store(*, storage_ref: str = "s3://bucket/path/chart.png") -> AsyncMock:
    store = AsyncMock()
    store.put = AsyncMock(return_value=storage_ref)
    return store


async def _registered_tools(
    module: DocumentRendererModule,
    blob_store: Any = None,
) -> tuple[_StubMCP, DocumentRendererModule]:
    """Run on_startup (with optional blob_store) and register_tools, return (mcp, module)."""
    mcp = _StubMCP()
    await module.on_startup(config={}, db=None, blob_store=blob_store)
    await module.register_tools(mcp=mcp, config={}, db=None, butler_name="test-butler")
    return mcp, module


# ---------------------------------------------------------------------------
# Module interface
# ---------------------------------------------------------------------------


class TestModuleInterface:
    def test_name(self) -> None:
        assert DocumentRendererModule().name == "document_renderer"

    def test_config_schema(self) -> None:
        from butlers.modules.document_renderer import DocumentRendererConfig

        assert DocumentRendererModule().config_schema is DocumentRendererConfig

    def test_dependencies_empty(self) -> None:
        assert DocumentRendererModule().dependencies == []

    def test_migration_revisions_none(self) -> None:
        assert DocumentRendererModule().migration_revisions() is None

    def test_tool_metadata_keys(self) -> None:
        meta = DocumentRendererModule().tool_metadata()
        assert "render_document" in meta
        assert "render_chart" in meta
        # Both tools are non-write in the network-egress sense
        assert meta["render_document"].arg_sensitivities["_write"] is False
        assert meta["render_chart"].arg_sensitivities["_write"] is False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_on_startup_stores_blob_store(self) -> None:
        mod = DocumentRendererModule()
        bs = _make_blob_store()
        await mod.on_startup(config={}, db=None, blob_store=bs)
        assert mod._blob_store is bs

    async def test_on_startup_none_blob_store_logs_warning(self, caplog) -> None:
        import logging

        mod = DocumentRendererModule()
        with caplog.at_level(logging.WARNING, logger="butlers.modules.document_renderer"):
            await mod.on_startup(config={}, db=None, blob_store=None)
        assert mod._blob_store is None
        assert any("no blob store" in r.message for r in caplog.records)

    async def test_on_shutdown_clears_blob_store(self) -> None:
        mod = DocumentRendererModule()
        mod._blob_store = _make_blob_store()
        await mod.on_shutdown()
        assert mod._blob_store is None

    async def test_on_shutdown_idempotent(self) -> None:
        mod = DocumentRendererModule()
        await mod.on_shutdown()  # should not raise when blob_store is None
        assert mod._blob_store is None


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_both_tools_registered(self) -> None:
        mcp, _ = await _registered_tools(DocumentRendererModule())
        assert "render_document" in mcp.tools
        assert "render_chart" in mcp.tools

    async def test_register_tools_twice_idempotent(self) -> None:
        """Calling register_tools a second time should not raise."""
        mod = DocumentRendererModule()
        mcp = _StubMCP()
        await mod.on_startup(config={}, db=None, blob_store=_make_blob_store())
        await mod.register_tools(mcp=mcp, config={}, db=None, butler_name="test-butler")
        await mod.register_tools(mcp=mcp, config={}, db=None, butler_name="test-butler")
        # Both tools still present
        assert "render_document" in mcp.tools
        assert "render_chart" in mcp.tools


# ---------------------------------------------------------------------------
# render_document
# ---------------------------------------------------------------------------

_MARKDOWN_CONTENT = "# Hello\n\nThis is **bold** text.\n\n- item 1\n- item 2"
_HTML_CONTENT = "<h1>Hello</h1><p>This is <strong>bold</strong> text.</p>"
_FAKE_PDF = b"%PDF-1.4 fake-pdf-bytes"


class TestRenderDocument:
    async def test_markdown_to_pdf_happy_path(self) -> None:
        bs = _make_blob_store(storage_ref="s3://bucket/doc.pdf")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_document"]

        with patch(
            "butlers.modules.document_renderer._html_to_pdf",
            return_value=_FAKE_PDF,
        ):
            result = await tool(content=_MARKDOWN_CONTENT, format="pdf", content_type="markdown")

        assert result["storage_ref"] == "s3://bucket/doc.pdf"
        assert result["content_type"] == "application/pdf"
        bs.put.assert_awaited_once()
        call_kwargs = bs.put.call_args
        assert call_kwargs.kwargs["content_type"] == "application/pdf"

    async def test_html_to_pdf_happy_path(self) -> None:
        bs = _make_blob_store(storage_ref="s3://bucket/doc.pdf")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_document"]

        with patch(
            "butlers.modules.document_renderer._html_to_pdf",
            return_value=_FAKE_PDF,
        ):
            result = await tool(content=_HTML_CONTENT, format="pdf", content_type="html")

        assert result["storage_ref"] == "s3://bucket/doc.pdf"
        assert result["content_type"] == "application/pdf"

    async def test_no_blob_store_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=None)
        tool = mcp.tools["render_document"]
        result = await tool(content=_MARKDOWN_CONTENT)
        assert "error" in result
        assert "blob store" in result["error"]

    async def test_unsupported_format_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_document"]
        result = await tool(content=_MARKDOWN_CONTENT, format="docx")
        assert "error" in result
        assert "docx" in result["error"]

    async def test_unsupported_content_type_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_document"]
        result = await tool(content=_MARKDOWN_CONTENT, content_type="rst")
        assert "error" in result
        assert "rst" in result["error"]

    async def test_pdf_backend_unavailable_returns_error(self) -> None:
        """If weasyprint is not installed, tool returns an actionable error."""
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_document"]

        with patch(
            "butlers.modules.document_renderer._html_to_pdf",
            side_effect=RuntimeError("PDF rendering requires weasyprint."),
        ):
            result = await tool(content=_MARKDOWN_CONTENT)

        assert "error" in result
        assert "weasyprint" in result["error"].lower() or "render" in result["error"].lower()

    async def test_blob_store_write_error_returns_error(self) -> None:
        bs = _make_blob_store()
        bs.put = AsyncMock(side_effect=OSError("S3 unavailable"))
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_document"]

        with patch(
            "butlers.modules.document_renderer._html_to_pdf",
            return_value=_FAKE_PDF,
        ):
            result = await tool(content=_MARKDOWN_CONTENT)

        assert "error" in result
        assert "store" in result["error"].lower() or "S3" in result["error"]

    async def test_default_args_invoke_markdown_pdf(self) -> None:
        """Calling with only content uses markdown + pdf defaults."""
        bs = _make_blob_store(storage_ref="s3://bucket/default.pdf")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_document"]

        with (
            patch(
                "butlers.modules.document_renderer._html_to_pdf",
                return_value=_FAKE_PDF,
            ) as mock_pdf,
            patch(
                "butlers.modules.document_renderer._markdown_to_html",
                return_value="<h1>Hello</h1>",
            ) as mock_md,
        ):
            result = await tool(content=_MARKDOWN_CONTENT)

        mock_md.assert_called_once_with(_MARKDOWN_CONTENT)
        mock_pdf.assert_called_once()
        assert "storage_ref" in result


# ---------------------------------------------------------------------------
# render_chart
# ---------------------------------------------------------------------------

_BAR_SPEC = json.dumps(
    {
        "type": "bar",
        "title": "Monthly Revenue",
        "x_label": "Month",
        "y_label": "USD",
        "data": [
            {"label": "Jan", "value": 1200},
            {"label": "Feb", "value": 980},
            {"label": "Mar", "value": 1500},
        ],
    }
)

_LINE_SPEC = json.dumps(
    {
        "type": "line",
        "title": "Trend",
        "data": [
            {"label": "Q1", "value": 100},
            {"label": "Q2", "value": 200},
            {"label": "Q3", "value": 150},
        ],
    }
)

_MINIMAL_SPEC = json.dumps({"data": [{"label": "A", "value": 42}]})


class TestRenderChart:
    async def test_chart_to_png_happy_path(self) -> None:
        bs = _make_blob_store(storage_ref="s3://bucket/chart.png")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_chart"]

        result = await tool(chart_spec=_BAR_SPEC, format="png")

        assert result["storage_ref"] == "s3://bucket/chart.png"
        assert result["content_type"] == "image/png"
        bs.put.assert_awaited_once()
        assert bs.put.call_args.kwargs["content_type"] == "image/png"

    async def test_chart_to_svg_happy_path(self) -> None:
        bs = _make_blob_store(storage_ref="s3://bucket/chart.svg")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_chart"]

        result = await tool(chart_spec=_BAR_SPEC, format="svg")

        assert result["storage_ref"] == "s3://bucket/chart.svg"
        assert result["content_type"] == "image/svg+xml"
        assert bs.put.call_args.kwargs["content_type"] == "image/svg+xml"

    async def test_line_chart_png(self) -> None:
        bs = _make_blob_store(storage_ref="s3://bucket/line.png")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=_LINE_SPEC, format="png")
        assert result["content_type"] == "image/png"

    async def test_line_chart_svg(self) -> None:
        bs = _make_blob_store(storage_ref="s3://bucket/line.svg")
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=_LINE_SPEC, format="svg")
        assert result["content_type"] == "image/svg+xml"

    async def test_minimal_spec_png(self) -> None:
        """A spec with only 'data' (no type/title/labels) should succeed."""
        bs = _make_blob_store()
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=_MINIMAL_SPEC, format="png")
        assert "error" not in result
        assert "storage_ref" in result

    async def test_no_blob_store_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=None)
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=_BAR_SPEC)
        assert "error" in result
        assert "blob store" in result["error"]

    async def test_unsupported_format_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=_BAR_SPEC, format="gif")
        assert "error" in result
        assert "gif" in result["error"]

    async def test_invalid_json_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec="not-json{{{")
        assert "error" in result
        assert "JSON" in result["error"]

    async def test_missing_data_field_returns_error(self) -> None:
        spec = json.dumps({"type": "bar", "title": "No data"})
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=spec)
        assert "error" in result
        assert "data" in result["error"]

    async def test_blob_store_write_error_returns_error(self) -> None:
        bs = _make_blob_store()
        bs.put = AsyncMock(side_effect=OSError("bucket full"))
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=bs)
        tool = mcp.tools["render_chart"]
        result = await tool(chart_spec=_BAR_SPEC, format="svg")
        assert "error" in result

    async def test_render_error_returns_error(self) -> None:
        mod = DocumentRendererModule()
        mcp, mod = await _registered_tools(mod, blob_store=_make_blob_store())
        tool = mcp.tools["render_chart"]

        with patch(
            "butlers.modules.document_renderer._render_chart_png",
            side_effect=RuntimeError("Pillow exploded"),
        ):
            result = await tool(chart_spec=_BAR_SPEC, format="png")

        assert "error" in result
        assert "render" in result["error"].lower() or "Pillow" in result["error"]


# ---------------------------------------------------------------------------
# Rendering helpers (unit tests, no blob store needed)
# ---------------------------------------------------------------------------


class TestMarkdownToHtml:
    def test_heading_converted(self) -> None:
        html = _markdown_to_html("# Hello World")
        assert "<h1>" in html and "Hello World" in html

    def test_bold_converted(self) -> None:
        html = _markdown_to_html("**bold text**")
        assert "<strong>bold text</strong>" in html

    def test_list_converted(self) -> None:
        html = _markdown_to_html("- item 1\n- item 2")
        assert "<li>" in html

    def test_paragraph_wrapped(self) -> None:
        html = _markdown_to_html("simple paragraph")
        assert "<p>" in html

    def test_code_block_converted(self) -> None:
        html = _markdown_to_html("```\ncode here\n```")
        assert "<code>" in html or "<pre>" in html


class TestRenderChartSvg:
    def test_bar_chart_produces_svg(self) -> None:
        spec = {"type": "bar", "data": [{"label": "A", "value": 10}]}
        result = _render_chart_svg(spec)
        assert result.startswith(b"<svg")
        assert b"</svg>" in result

    def test_line_chart_produces_svg(self) -> None:
        spec = {
            "type": "line",
            "title": "T",
            "data": [{"label": "x", "value": 5}, {"label": "y", "value": 10}],
        }
        result = _render_chart_svg(spec)
        assert b"<svg" in result
        assert b"path" in result  # line chart uses <path>

    def test_empty_data_does_not_raise(self) -> None:
        spec = {"type": "bar", "data": []}
        result = _render_chart_svg(spec)
        assert b"<svg" in result

    def test_default_type_is_bar(self) -> None:
        spec = {"data": [{"label": "A", "value": 1}]}  # no 'type'
        result = _render_chart_svg(spec)
        assert b"rect" in result  # bars are rects

    def test_negative_value_produces_valid_svg(self) -> None:
        """Negative bar values must not emit a negative SVG height attribute."""
        spec = {
            "type": "bar",
            "data": [{"label": "A", "value": 10}, {"label": "B", "value": -5}],
        }
        result = _render_chart_svg(spec)
        assert b'height="-' not in result  # no negative height attrs

    def test_svg_includes_title(self) -> None:
        spec = {"type": "bar", "title": "My Title", "data": [{"label": "x", "value": 3}]}
        result = _render_chart_svg(spec)
        assert b"My Title" in result


class TestRenderChartPng:
    def test_bar_chart_produces_png_bytes(self) -> None:
        spec = {
            "type": "bar",
            "data": [{"label": "A", "value": 50}, {"label": "B", "value": 30}],
        }
        result = _render_chart_png(spec)
        # PNG signature
        assert result[:4] == b"\x89PNG"

    def test_line_chart_produces_png_bytes(self) -> None:
        spec = {
            "type": "line",
            "data": [{"label": "1", "value": 10}, {"label": "2", "value": 20}],
        }
        result = _render_chart_png(spec)
        assert result[:4] == b"\x89PNG"

    def test_empty_data_does_not_raise(self) -> None:
        spec = {"type": "bar", "data": []}
        result = _render_chart_png(spec)
        assert result[:4] == b"\x89PNG"

    def test_single_data_point(self) -> None:
        spec = {"type": "line", "data": [{"label": "only", "value": 42}]}
        result = _render_chart_png(spec)
        assert result[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# Registry discovery
# ---------------------------------------------------------------------------


class TestRegistryDiscovery:
    def test_module_discoverable_via_default_registry(self) -> None:
        from butlers.modules.registry import default_registry

        registry = default_registry()
        assert "document_renderer" in registry.available_modules
