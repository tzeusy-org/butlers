"""Bounded PDF text extraction, run as a child process by ``attachment_read``.

An untrusted PDF can carry a decompression bomb: a few KB of FlateDecode that
inflates to millions of characters and gigabytes of PDFium allocations while a
page is parsed. The parent runs this module in a fresh interpreter under an
address-space limit and a wall-clock timeout, so a hostile page can kill only
this child, never the daemon.

Protocol: the PDF bytes arrive on stdin; argv carries ``page_start``,
``page_count``, ``char_budget``, the address-space limit in bytes and the CPU
limit in seconds. The limits are applied before PDFium is imported. One JSON
object goes to stdout, either
``{"total": int, "pages": [{"page": int, "text": str, "truncated": bool}]}``
or ``{"refusal": reason}``. The character budget is applied per page before
any text is copied out, and no further page is opened once it is spent.
"""

from __future__ import annotations

import json
import re
import resource
import sys
from typing import Any

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _normalize(raw: str) -> str:
    return _CONTROL_CHARS.sub("", raw.replace("\r\n", "\n").replace("\r", "\n")).strip()


def extract(data: bytes, page_start: int, page_count: int, char_budget: int) -> dict[str, Any]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium.raw.FPDF_ERR_PASSWORD:
            return {"refusal": "encrypted_pdf"}
        return {"refusal": "corrupt_pdf"}
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


def apply_limits(memory_bytes: int, cpu_seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    page_start, page_count, char_budget, memory_bytes, cpu_seconds = (
        int(value) for value in sys.argv[1:6]
    )
    apply_limits(memory_bytes, cpu_seconds)  # before extract() imports pypdfium2
    result = extract(sys.stdin.buffer.read(), page_start, page_count, char_budget)
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
