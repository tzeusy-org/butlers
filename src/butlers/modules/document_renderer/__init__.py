"""Document renderer module -- MCP tools for rendering documents and charts to blobs.

Provides two MCP tools:

- ``render_document``: Convert Markdown or HTML source to a PDF blob.
- ``render_chart``: Convert a JSON chart specification to a PNG or SVG image blob.

All output is stored in the configured blob store and returned as a ``storage_ref``
usable as a ``notify`` attachment.  Rendering is pure local computation -- no
network egress, no approval gating.

PDF rendering requires ``weasyprint`` and its system dependencies (Cairo, Pango,
GLib).  Chart rendering works without additional system dependencies: SVG is
generated as text, PNG uses Pillow (already a project dependency).

Configured via ``[modules.document_renderer]`` in ``butler.toml`` (no extra keys
required; just enabling the module suffices).
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class DocumentRendererConfig(BaseModel):
    """Configuration for the document renderer module.

    No settings are required today; the module activates as soon as it is
    listed under ``[modules.document_renderer]`` in ``butler.toml`` and the
    daemon has a blob store configured.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _markdown_to_html(text: str) -> str:
    """Convert Markdown source to an HTML fragment using the ``markdown`` library."""
    import markdown  # lightweight pure-Python dep added to pyproject.toml

    return markdown.markdown(text, extensions=["extra", "tables", "nl2br"])


def _html_to_pdf(html: str) -> bytes:
    """Render an HTML string to PDF bytes using WeasyPrint.

    Raises:
        RuntimeError: When ``weasyprint`` is not installed or its system
            dependencies (Cairo, Pango) are unavailable.
    """
    try:
        import weasyprint  # optional system-dep; not in pyproject.toml

        document = weasyprint.HTML(string=html)
        return document.write_pdf()  # type: ignore[return-value]
    except ImportError as exc:
        msg = (
            "PDF rendering requires weasyprint and its system libraries "
            "(Cairo, Pango, GLib). "
            "Install with: pip install weasyprint "
            "(and ensure system packages are present -- see https://doc.courtbouillon.org/weasyprint/)."
        )
        raise RuntimeError(msg) from exc


def _wrap_html_document(body_html: str, title: str = "") -> str:
    """Wrap an HTML fragment in a minimal HTML5 document shell."""
    title_tag = f"<title>{title}</title>" if title else ""
    return (
        "<!DOCTYPE html>"
        "<html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"{title_tag}"
        "<style>"
        "body{font-family:sans-serif;max-width:800px;margin:2em auto;padding:0 1em;line-height:1.5}"
        "h1,h2,h3{margin-top:1.5em}"
        "pre{background:#f5f5f5;padding:1em;overflow:auto}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:0.4em 0.6em}"
        "</style>"
        "</head>"
        f"<body>{body_html}</body></html>"
    )


# ---------------------------------------------------------------------------
# Chart rendering -- SVG
# ---------------------------------------------------------------------------

_SVG_BAR_COLOURS = [
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
]

_SVG_W = 640
_SVG_H = 400
_SVG_PAD_LEFT = 60
_SVG_PAD_RIGHT = 20
_SVG_PAD_TOP = 50
_SVG_PAD_BOTTOM = 80


def _render_bar_svg(data: list[dict], title: str, x_label: str, y_label: str) -> str:
    """Render a vertical bar chart to an SVG string."""
    values = [float(d.get("value", 0)) for d in data]
    labels = [str(d.get("label", "")) for d in data]
    n = len(values)

    plot_w = _SVG_W - _SVG_PAD_LEFT - _SVG_PAD_RIGHT
    plot_h = _SVG_H - _SVG_PAD_TOP - _SVG_PAD_BOTTOM
    max_val = max(values) if values else 1.0
    bar_w = max(4, plot_w // max(n, 1) - 6)

    elements: list[str] = []

    # Background
    elements.append(f'<rect width="{_SVG_W}" height="{_SVG_H}" fill="#fff"/>')

    # Title
    if title:
        elements.append(
            f'<text x="{_SVG_W // 2}" y="28" text-anchor="middle" '
            f'font-family="sans-serif" font-size="16" font-weight="bold">{title}</text>'
        )

    # Y-axis label
    if y_label:
        elements.append(
            f'<text x="12" y="{_SVG_PAD_TOP + plot_h // 2}" '
            f'transform="rotate(-90,12,{_SVG_PAD_TOP + plot_h // 2})" '
            f'text-anchor="middle" font-family="sans-serif" font-size="12">{y_label}</text>'
        )

    # X-axis label
    if x_label:
        elements.append(
            f'<text x="{_SVG_PAD_LEFT + plot_w // 2}" y="{_SVG_H - 6}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="12">{x_label}</text>'
        )

    # Axes
    x0, y0 = _SVG_PAD_LEFT, _SVG_PAD_TOP
    x1, y1 = _SVG_PAD_LEFT + plot_w, _SVG_PAD_TOP + plot_h
    elements.append(
        f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="#555" stroke-width="1.5"/>'
    )
    elements.append(
        f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#555" stroke-width="1.5"/>'
    )

    # Y-axis ticks (5 intervals)
    n_ticks = 5
    for i in range(n_ticks + 1):
        frac = i / n_ticks
        tick_val = max_val * frac
        ty = y1 - int(plot_h * frac)
        elements.append(
            f'<line x1="{x0 - 4}" y1="{ty}" x2="{x0}" y2="{ty}" stroke="#555" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{x0 - 8}" y="{ty + 4}" text-anchor="end" '
            f'font-family="sans-serif" font-size="10">{tick_val:.0f}</text>'
        )

    # Bars
    slot_w = plot_w // max(n, 1)
    for i, (val, lbl) in enumerate(zip(values, labels)):
        bh = max(0, int(plot_h * (val / max_val))) if max_val > 0 else 0
        bx = x0 + i * slot_w + (slot_w - bar_w) // 2
        by = y1 - bh
        colour = _SVG_BAR_COLOURS[i % len(_SVG_BAR_COLOURS)]
        elements.append(
            f'<rect x="{bx}" y="{by}" width="{bar_w}" height="{bh}" fill="{colour}" rx="2"/>'
        )
        # X-axis label below each bar
        elements.append(
            f'<text x="{bx + bar_w // 2}" y="{y1 + 16}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="11">{lbl}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_SVG_W} {_SVG_H}" '
        f'width="{_SVG_W}" height="{_SVG_H}">' + "".join(elements) + "</svg>"
    )


def _render_line_svg(data: list[dict], title: str, x_label: str, y_label: str) -> str:
    """Render a line chart to an SVG string."""
    values = [float(d.get("value", 0)) for d in data]
    labels = [str(d.get("label", "")) for d in data]
    n = len(values)

    plot_w = _SVG_W - _SVG_PAD_LEFT - _SVG_PAD_RIGHT
    plot_h = _SVG_H - _SVG_PAD_TOP - _SVG_PAD_BOTTOM
    max_val = max(values) if values else 1.0
    min_val = min(values) if values else 0.0
    val_range = max_val - min_val or 1.0

    elements: list[str] = []
    elements.append(f'<rect width="{_SVG_W}" height="{_SVG_H}" fill="#fff"/>')

    if title:
        elements.append(
            f'<text x="{_SVG_W // 2}" y="28" text-anchor="middle" '
            f'font-family="sans-serif" font-size="16" font-weight="bold">{title}</text>'
        )
    if y_label:
        elements.append(
            f'<text x="12" y="{_SVG_PAD_TOP + plot_h // 2}" '
            f'transform="rotate(-90,12,{_SVG_PAD_TOP + plot_h // 2})" '
            f'text-anchor="middle" font-family="sans-serif" font-size="12">{y_label}</text>'
        )
    if x_label:
        elements.append(
            f'<text x="{_SVG_PAD_LEFT + plot_w // 2}" y="{_SVG_H - 6}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="12">{x_label}</text>'
        )

    x0, y0 = _SVG_PAD_LEFT, _SVG_PAD_TOP
    x1, y1 = _SVG_PAD_LEFT + plot_w, _SVG_PAD_TOP + plot_h
    elements.append(
        f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="#555" stroke-width="1.5"/>'
    )
    elements.append(
        f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#555" stroke-width="1.5"/>'
    )

    n_ticks = 5
    for i in range(n_ticks + 1):
        frac = i / n_ticks
        tick_val = min_val + val_range * frac
        ty = y1 - int(plot_h * frac)
        elements.append(
            f'<line x1="{x0 - 4}" y1="{ty}" x2="{x0}" y2="{ty}" stroke="#555" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{x0 - 8}" y="{ty + 4}" text-anchor="end" '
            f'font-family="sans-serif" font-size="10">{tick_val:.1f}</text>'
        )

    if n > 0:

        def _pt(i: int) -> tuple[int, int]:
            px = x0 + int(plot_w * i / max(n - 1, 1))
            py = y1 - int(plot_h * (values[i] - min_val) / val_range)
            return px, py

        # Line path
        points = [_pt(i) for i in range(n)]
        d = " ".join(f"{'M' if i == 0 else 'L'}{px},{py}" for i, (px, py) in enumerate(points))
        elements.append(
            f'<path d="{d}" fill="none" stroke="{_SVG_BAR_COLOURS[0]}" stroke-width="2.5"/>'
        )

        # Data points and x labels
        for i, ((px, py), lbl) in enumerate(zip(points, labels)):
            elements.append(
                f'<circle cx="{px}" cy="{py}" r="4" '
                f'fill="{_SVG_BAR_COLOURS[0]}" stroke="#fff" stroke-width="1.5"/>'
            )
            elements.append(
                f'<text x="{px}" y="{y1 + 16}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="11">{lbl}</text>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_SVG_W} {_SVG_H}" '
        f'width="{_SVG_W}" height="{_SVG_H}">' + "".join(elements) + "</svg>"
    )


def _render_chart_svg(spec: dict) -> bytes:
    """Dispatch to bar or line SVG renderer based on spec ``type`` field."""
    chart_type = spec.get("type", "bar").lower()
    data = spec.get("data", [])
    title = spec.get("title", "")
    x_label = spec.get("x_label", "")
    y_label = spec.get("y_label", "")

    if chart_type == "line":
        svg_str = _render_line_svg(data, title, x_label, y_label)
    else:
        # Default to bar chart
        svg_str = _render_bar_svg(data, title, x_label, y_label)

    return svg_str.encode("utf-8")


# ---------------------------------------------------------------------------
# Chart rendering -- PNG via Pillow
# ---------------------------------------------------------------------------

_PNG_W = 800
_PNG_H = 500
_PNG_PAD_LEFT = 70
_PNG_PAD_RIGHT = 30
_PNG_PAD_TOP = 60
_PNG_PAD_BOTTOM = 100

_PILLOW_COLOURS = [
    (78, 121, 167),
    (242, 142, 43),
    (225, 87, 89),
    (118, 183, 178),
    (89, 161, 79),
    (237, 201, 72),
    (176, 122, 161),
    (255, 157, 167),
]


def _render_chart_png(spec: dict) -> bytes:
    """Render a chart spec to PNG bytes using Pillow."""
    from PIL import Image, ImageDraw

    chart_type = spec.get("type", "bar").lower()
    data = spec.get("data", [])
    title = spec.get("title", "")
    x_label = spec.get("x_label", "")
    y_label = spec.get("y_label", "")
    values = [float(d.get("value", 0)) for d in data]
    labels = [str(d.get("label", "")) for d in data]
    n = len(values)

    img = Image.new("RGB", (_PNG_W, _PNG_H), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    plot_x0 = _PNG_PAD_LEFT
    plot_y0 = _PNG_PAD_TOP
    plot_x1 = _PNG_W - _PNG_PAD_RIGHT
    plot_y1 = _PNG_H - _PNG_PAD_BOTTOM
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    max_val = max(values) if values else 1.0
    min_val = min(values) if values else 0.0
    val_range = max_val - min_val or 1.0

    # Title
    if title:
        draw.text((_PNG_W // 2, 20), title, fill=(30, 30, 30), anchor="mt")

    # Axes
    draw.line([(plot_x0, plot_y0), (plot_x0, plot_y1)], fill=(80, 80, 80), width=2)
    draw.line([(plot_x0, plot_y1), (plot_x1, plot_y1)], fill=(80, 80, 80), width=2)

    # Y-axis ticks
    n_ticks = 5
    for i in range(n_ticks + 1):
        frac = i / n_ticks
        if chart_type == "line":
            tick_val = min_val + val_range * frac
        else:
            tick_val = max_val * frac
        ty = plot_y1 - int(plot_h * frac)
        draw.line([(plot_x0 - 5, ty), (plot_x0, ty)], fill=(80, 80, 80), width=1)
        draw.text((plot_x0 - 8, ty), f"{tick_val:.0f}", fill=(60, 60, 60), anchor="rm")

    # Y-axis label -- Pillow doesn't natively rotate text; draw horizontally above the Y-axis
    # to avoid left-edge clipping that occurs when anchoring near x=0.
    if y_label:
        draw.text((plot_x0, plot_y0 - 15), y_label, fill=(60, 60, 60), anchor="lb")

    # X-axis label
    if x_label:
        draw.text((plot_x0 + plot_w // 2, _PNG_H - 15), x_label, fill=(60, 60, 60), anchor="mm")

    if n > 0:
        if chart_type == "line":
            slot_w = plot_w // max(n - 1, 1) if n > 1 else plot_w

            def _px(i: int) -> int:
                return plot_x0 + int(plot_w * i / max(n - 1, 1)) if n > 1 else plot_x0 + plot_w // 2

            def _py(v: float) -> int:
                return plot_y1 - int(plot_h * (v - min_val) / val_range)

            pts = [(_px(i), _py(v)) for i, v in enumerate(values)]
            if len(pts) > 1:
                draw.line(pts, fill=_PILLOW_COLOURS[0], width=3)
            for i, ((px, py), lbl) in enumerate(zip(pts, labels)):
                r = 5
                draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=_PILLOW_COLOURS[0])
                draw.text((px, plot_y1 + 12), lbl, fill=(60, 60, 60), anchor="mt")
        else:
            # Bar chart
            bar_w = max(6, plot_w // max(n, 1) - 8)
            slot_w = plot_w // max(n, 1)
            for i, (val, lbl) in enumerate(zip(values, labels)):
                bh = max(0, int(plot_h * (val / max_val))) if max_val > 0 else 0
                bx = plot_x0 + i * slot_w + (slot_w - bar_w) // 2
                by = plot_y1 - bh
                colour = _PILLOW_COLOURS[i % len(_PILLOW_COLOURS)]
                draw.rectangle([(bx, by), (bx + bar_w, plot_y1)], fill=colour)
                draw.text((bx + bar_w // 2, plot_y1 + 12), lbl, fill=(60, 60, 60), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


class DocumentRendererModule(Module):
    """Document renderer module providing MCP tools for rendering to the blob store.

    Render Markdown or HTML to PDF and chart specifications to PNG or SVG.
    All outputs are stored in the blob store and returned as ``storage_ref``
    values.

    Rendering is pure local computation -- no network egress, no approval gating.

    PDF rendering requires ``weasyprint`` (and Cairo/Pango system libs).
    Chart PNG rendering uses Pillow.  SVG generation requires no extra packages.
    """

    def __init__(self) -> None:
        self._blob_store: Any = None

    # ------------------------------------------------------------------
    # Module interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "document_renderer"

    @property
    def config_schema(self) -> type[BaseModel]:
        return DocumentRendererConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Store the blob store reference for use by rendering tools.

        Parameters
        ----------
        config:
            Module configuration (``DocumentRendererConfig`` or raw dict).
            No settings are read today.
        db:
            Butler database instance.  Unused by this module.
        credential_store:
            Unused by this module.
        blob_store:
            :class:`~butlers.storage.blobs.BlobStore` used for persisting
            rendered blobs.  When ``None``, tools will return an error.
        """
        self._blob_store = blob_store
        if blob_store is None:
            logger.warning(
                "document_renderer module: no blob store configured; "
                "render_document and render_chart will return errors."
            )

    async def on_shutdown(self) -> None:
        """Release the blob store reference."""
        self._blob_store = None

    # ------------------------------------------------------------------
    # Tool metadata
    # ------------------------------------------------------------------

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Rendering tools are pure computation -- not safety-critical write ops."""
        return {
            "render_document": ToolMeta(arg_sensitivities={"_write": False}),
            "render_chart": ToolMeta(arg_sensitivities={"_write": False}),
        }

    # ------------------------------------------------------------------
    # register_tools
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register ``render_document`` and ``render_chart`` MCP tools."""
        module = self  # capture for closures

        # ----------------------------------------------------------------
        # Tool 1: render_document
        # ----------------------------------------------------------------

        async def render_document(
            content: str,
            format: str = "pdf",  # noqa: A002
            content_type: str = "markdown",
        ) -> dict[str, Any]:
            """Render Markdown or HTML content to a PDF blob.

            Converts the provided source text to a PDF document, stores it in
            the blob store, and returns a ``storage_ref`` suitable for use as a
            ``notify`` attachment.

            PDF rendering requires ``weasyprint`` (and Cairo/Pango system
            libraries).  If ``weasyprint`` is not installed, the tool returns an
            actionable error describing how to install it.

            Args:
                content: Markdown or HTML source text to render.
                format: Output format.  Only ``"pdf"`` is supported.
                content_type: Input type -- ``"markdown"`` (default) or
                              ``"html"``.  Markdown is converted to HTML before
                              PDF generation.

            Returns:
                On success: ``{"storage_ref": "s3://...", "content_type": "application/pdf"}``.
                On failure: ``{"error": "<message>"}``.
            """
            if module._blob_store is None:
                return {
                    "error": (
                        "render_document: no blob store is configured for this butler. "
                        "Enable blob storage in the butler configuration."
                    )
                }

            if format.lower() != "pdf":
                return {
                    "error": (
                        f"render_document: unsupported output format '{format}'. "
                        "Only 'pdf' is supported."
                    )
                }

            try:
                if content_type.lower() == "markdown":
                    html_body = _markdown_to_html(content)
                elif content_type.lower() == "html":
                    html_body = content
                else:
                    return {
                        "error": (
                            f"render_document: unsupported content_type '{content_type}'. "
                            "Use 'markdown' or 'html'."
                        )
                    }

                full_html = _wrap_html_document(html_body)
                pdf_bytes = _html_to_pdf(full_html)
            except RuntimeError as exc:
                return {"error": f"render_document: {exc}"}
            except Exception as exc:  # noqa: BLE001
                logger.exception("render_document: unexpected error during rendering")
                return {"error": f"render_document: rendering failed -- {exc}"}

            try:
                storage_ref = await module._blob_store.put(
                    pdf_bytes,
                    content_type="application/pdf",
                    filename="document.pdf",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("render_document: blob store write failed")
                return {"error": f"render_document: failed to store rendered PDF -- {exc}"}

            return {"storage_ref": storage_ref, "content_type": "application/pdf"}

        mcp.tool()(render_document)

        # ----------------------------------------------------------------
        # Tool 2: render_chart
        # ----------------------------------------------------------------

        async def render_chart(
            chart_spec: str,
            format: str = "png",  # noqa: A002
        ) -> dict[str, Any]:
            """Render a chart specification to a PNG or SVG image blob.

            Accepts a JSON chart specification and renders it to a static image,
            stored in the blob store and returned as a ``storage_ref`` suitable
            for use as a ``notify`` attachment.

            Chart spec JSON format::

                {
                    "type": "bar" | "line",
                    "title": "Optional chart title",
                    "x_label": "Optional x-axis label",
                    "y_label": "Optional y-axis label",
                    "data": [
                        {"label": "Jan", "value": 1200},
                        {"label": "Feb", "value": 980}
                    ]
                }

            PNG rendering uses Pillow (a project dependency).  SVG is generated
            as pure text with no additional packages required.

            Args:
                chart_spec: JSON string describing the chart to render.
                format: Output image format -- ``"png"`` (default) or ``"svg"``.

            Returns:
                On success: ``{"storage_ref": "s3://...", "content_type": "image/png"}``
                (or ``"image/svg+xml"`` for SVG).
                On failure: ``{"error": "<message>"}``.
            """
            if module._blob_store is None:
                return {
                    "error": (
                        "render_chart: no blob store is configured for this butler. "
                        "Enable blob storage in the butler configuration."
                    )
                }

            fmt = format.lower()
            if fmt not in ("png", "svg"):
                return {
                    "error": (
                        f"render_chart: unsupported format '{format}'. "
                        "Supported values: 'png', 'svg'."
                    )
                }

            try:
                spec = json.loads(chart_spec)
            except json.JSONDecodeError as exc:
                return {"error": f"render_chart: invalid JSON in chart_spec -- {exc}"}

            if not isinstance(spec, dict):
                return {"error": "render_chart: chart_spec must be a JSON object."}

            data = spec.get("data")
            if not isinstance(data, list):
                return {
                    "error": (
                        "render_chart: chart_spec must contain a 'data' list "
                        "of {label, value} objects."
                    )
                }

            try:
                if fmt == "svg":
                    image_bytes = _render_chart_svg(spec)
                    mime = "image/svg+xml"
                    filename = "chart.svg"
                else:
                    image_bytes = _render_chart_png(spec)
                    mime = "image/png"
                    filename = "chart.png"
            except Exception as exc:  # noqa: BLE001
                logger.exception("render_chart: rendering failed")
                return {"error": f"render_chart: rendering failed -- {exc}"}

            try:
                storage_ref = await module._blob_store.put(
                    image_bytes,
                    content_type=mime,
                    filename=filename,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("render_chart: blob store write failed")
                return {"error": f"render_chart: failed to store rendered chart -- {exc}"}

            return {"storage_ref": storage_ref, "content_type": mime}

        mcp.tool()(render_chart)
