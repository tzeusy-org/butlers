"""Pure helpers for the owner-private native Google Docs and Sheets tools.

Nothing here touches the network. The module wires these into the HTTP calls; keeping the
rules (what is a private target, what is sanitised, what is too big) in one dependency-free
place lets the tests pin them without a fake Google.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from butlers.core.delivery_rendering import defang_delivery_text

DOC_MAX_BYTES = 1_000_000
MAX_CELLS = 50_000
SHEETS_API_BASE = "https://sheets.googleapis.com/v4"
CREATED_BY_PROPERTY = "butlers_created_by"
CONTENT_FORMATS = ("markdown", "text")

# Drive file fields the owner-private check reads. ``permissions`` carries inherited grants.
TARGET_FIELDS = (
    "id,name,mimeType,trashed,ownedByMe,driveId,version,webViewLink,appProperties,"
    "permissions(type,role,emailAddress,domain,deleted)"
)

_IMAGE_EMPTY_ALT = re.compile(r"!\[\s*\]")
_IMAGE_OPEN = re.compile(r"!\[")
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*<?([^)\s>]*)>?(?:\s+[^)]*)?\)")
_URL = re.compile(
    r"(?:\b[A-Za-z][A-Za-z0-9+.-]*://|\bwww\.|\bmailto:)[^\s<>\"')\]]+", re.IGNORECASE
)


def sanitize_markdown(text: str) -> str:
    """Make Markdown safe to hand to Google's importer.

    An image reference, inline or reference style, becomes an ordinary link, so the import
    never fetches a URL. Every ``<`` is escaped, so raw HTML stays literal text.
    """
    text = _IMAGE_EMPTY_ALT.sub("[image]", text)
    text = _IMAGE_OPEN.sub("[", text)
    return text.replace("<", "\\<")


def inert_urls(text: str) -> str:
    """Write every URL in ``text`` in the inert text form no client can link.

    A Markdown link keeps its label and shows its target defanged; a bare URL is defanged in
    place. Bare hosts without a scheme or ``www.`` are not recognised.
    """

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        shown = defang_delivery_text(target)
        return f"{label} ({shown})" if label.strip() else shown

    text = _MD_LINK.sub(link, text)
    return _URL.sub(lambda match: defang_delivery_text(match.group(0)), text)


def prepare_doc_content(content: str, content_format: str, *, inert: bool) -> str:
    """The exact text uploaded for a Doc: sanitised, and link-inert when ``inert``."""
    body = sanitize_markdown(content) if content_format == "markdown" else content
    return inert_urls(body) if inert else body


def doc_too_large(content: str) -> bool:
    return len(content.encode("utf-8")) > DOC_MAX_BYTES


def prepare_rows(rows: Any, *, inert: bool) -> list[list[Any]] | str:
    """Validate and normalise sheet rows, or return a short reason string.

    Cells are text, numbers or booleans; ``None`` becomes an empty cell. Values are always
    sent raw, so nothing here needs to guard a leading ``=``; ``inert`` defangs URLs in text.
    """
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        return "rows must be a non-empty list of rows"
    prepared: list[list[Any]] = []
    cells = 0
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            return "each row must be a list of cells"
        cells += len(row)
        if cells > MAX_CELLS:
            return "too_large"
        out: list[Any] = []
        for cell in row:
            if cell is None:
                out.append("")
            elif isinstance(cell, bool) or isinstance(cell, (int, float)):
                out.append(cell)
            elif isinstance(cell, str):
                out.append(inert_urls(cell) if inert else cell)
            else:
                return "cells must be text, numbers or booleans"
        prepared.append(out)
    return prepared


def a1_path(a1_range: str) -> str:
    """An A1 range as one URL path segment."""
    return quote(a1_range, safe="")


def sheet_title_range(title: str) -> str:
    """A whole-tab A1 range for ``title``."""
    return "'" + title.replace("'", "''") + "'"


def evaluate_reach(meta: Mapping[str, Any], owner_email: str) -> tuple[bool, dict[str, int]]:
    """Whether ``meta`` is an owner-private target, and a count of what makes it otherwise.

    Private means: owned by the connected account, not in a Shared Drive, and every live
    permission is that account's own user grant. Anything unreadable fails closed. The counts
    name permission types and roles only, never an address.
    """
    reach: Counter[str] = Counter()
    if meta.get("ownedByMe") is not True:
        reach["not_owned"] += 1
    if meta.get("driveId"):
        reach["shared_drive"] += 1
    permissions = meta.get("permissions")
    if not isinstance(permissions, list):
        reach["permissions_unreadable"] += 1
        permissions = []
    me = owner_email.strip().lower()
    for permission in permissions:
        if not isinstance(permission, Mapping) or permission.get("deleted"):
            continue
        kind, role = permission.get("type"), permission.get("role")
        email = permission.get("emailAddress")
        if kind == "user" and isinstance(email, str) and email.strip().lower() == me:
            continue
        reach[f"{kind}:{role}"] += 1
    return (not reach, dict(reach))
