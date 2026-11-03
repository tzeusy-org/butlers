"""Bounded PDF work, run as a child process by ``attachment_read`` and ``attachment_page_view``.

An untrusted PDF can carry a decompression bomb: a few KB of FlateDecode that
inflates to millions of characters, or a scan whose image decodes to gigabytes
of PDFium allocations while a page is parsed or rendered. The parent runs this
module in a fresh interpreter under an address-space limit and a wall-clock
timeout, so a hostile page can kill only this child, never the daemon.

Protocol: the PDF bytes arrive on stdin. argv is ``mode``, the address-space
limit in bytes and the CPU limit in seconds, then the mode's arguments. The
limits are applied before PDFium is imported.

- ``text page_start page_count char_budget``: one JSON object goes to stdout,
  either ``{"total": int, "pages": [{"page": int, "text": str, "truncated":
  bool}]}`` or ``{"refusal": reason}``. The character budget is applied per
  page before any text is copied out, and no further page is opened once it is
  spent.
- ``render page max_edge_px max_pixels``: one JSON header line, then the PNG
  bytes. The header is ``{"total": int, "page": int, "width": int, "height":
  int}`` (PNG follows) or ``{"refusal": reason}`` (nothing follows). The scale
  is chosen from the page size before anything is drawn, so the bitmap never
  exceeds ``max_edge_px`` on its long edge or ``max_pixels`` in area.
"""

from __future__ import annotations

import io
import json
import math
import re
import resource
import sys
from typing import Any

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Upper bound on rendering resolution: small pages are not blown up past this.
_MAX_RENDER_DPI = 200


def _normalize(raw: str) -> str:
    return _CONTROL_CHARS.sub("", raw.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _open(pdfium: Any, data: bytes) -> Any:
    """Open ``data`` as a PDF, or return a refusal dict."""
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium.raw.FPDF_ERR_PASSWORD:
            return {"refusal": "encrypted_pdf"}
        return {"refusal": "corrupt_pdf"}


def extract(data: bytes, page_start: int, page_count: int, char_budget: int) -> dict[str, Any]:
    import pypdfium2 as pdfium

    document = _open(pdfium, data)
    if isinstance(document, dict):
        return document
    try:
        total = len(document)
        if page_start > total:
            return {"refusal": "invalid_page_range", "total": total}
        pages: list[dict[str, Any]] = []
        budget = char_budget
        for index in range(page_start - 1, min(total, page_start - 1 + page_count)):
            if budget <= 0:
                break
            page = document[index]
            textpage = page.get_textpage()
            try:
                available = textpage.count_chars()
                take = min(available, budget)
                text = _normalize(textpage.get_text_range(count=take)) if take else ""
            finally:
                textpage.close()
                page.close()
            truncated = available > take
            if truncated and pages:
                break  # leave the oversized page for the next call
            pages.append({"page": index + 1, "text": text, "truncated": truncated})
            budget -= len(text)
            if truncated:
                break
        return {"total": total, "pages": pages}
    except pdfium.PdfiumError:
        return {"refusal": "corrupt_pdf"}
    finally:
        document.close()


def render_scale(width_pt: float, height_pt: float, max_edge: int, max_pixels: int) -> float:
    """The largest scale (pixels per point) that keeps the bitmap inside both bounds."""
    long_edge = max(width_pt, height_pt)
    return min(
        _MAX_RENDER_DPI / 72,
        max_edge / long_edge,
        math.sqrt(max_pixels / (width_pt * height_pt)),
    )


def render(
    data: bytes, page_number: int, max_edge: int, max_pixels: int
) -> tuple[dict[str, Any], bytes]:
    import pypdfium2 as pdfium

    document = _open(pdfium, data)
    if isinstance(document, dict):
        return document, b""
    try:
        total = len(document)
        if page_number > total:
            return {"refusal": "invalid_page_range", "total": total}, b""
        page = document[page_number - 1]
        try:
            width_pt, height_pt = page.get_size()
            if not (width_pt >= 1 and height_pt >= 1):
                return {"refusal": "corrupt_pdf"}, b""
            # Shrink slightly so pixel rounding cannot cross either bound.
            scale = render_scale(width_pt, height_pt, max_edge, max_pixels) * 0.995
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    except pdfium.PdfiumError:
        return {"refusal": "corrupt_pdf"}, b""
    finally:
        document.close()
    width, height = image.size
    if max(width, height) > max_edge or width * height > max_pixels:
        return {"refusal": "extraction_limit_exceeded"}, b""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    header = {"total": total, "page": page_number, "width": width, "height": height}
    return header, buffer.getvalue()


def apply_limits(memory_bytes: int, cpu_seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    mode = sys.argv[1]
    memory_bytes, cpu_seconds, *args = (int(value) for value in sys.argv[2:])
    apply_limits(memory_bytes, cpu_seconds)  # before PDFium is imported
    data = sys.stdin.buffer.read()
    if mode == "text":
        page_start, page_count, char_budget = args
        sys.stdout.write(json.dumps(extract(data, page_start, page_count, char_budget)))
        return
    if mode == "render":
        page_number, max_edge, max_pixels = args
        header, png = render(data, page_number, max_edge, max_pixels)
        sys.stdout.buffer.write(json.dumps(header).encode() + b"\n" + png)
        return
    raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    main()
