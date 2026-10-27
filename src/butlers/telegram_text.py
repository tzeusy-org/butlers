"""Pure Telegram text rendering; transport and authority stay in their existing owners."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

MAX_VISIBLE_UNITS = 4000
TRUNCATED = "… (truncated)"


def text_units(text: str) -> int:
    """Count Telegram entity offsets without splitting an astral code point."""
    return len(text.encode("utf-16-le")) // 2


def _prefix_end(text: str, limit: int) -> int:
    units = 0
    for index, char in enumerate(text):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > limit:
            return index
    return len(text)


def clamp_edit_source(text: str) -> str:
    """One edit cannot add messages; retain an explicit bounded truncation marker."""
    if text_units(text) <= MAX_VISIBLE_UNITS:
        return text
    return text[: _prefix_end(text, MAX_VISIBLE_UNITS - text_units(TRUNCATED))] + TRUNCATED


def _inline(text: str) -> str:
    # A delimiter can close only the top frame. Crossing/unclosed markup is
    # literal, never an independently substituted tag that can cross another.
    frames: list[tuple[str, str, list[str]]] = [("", "", [])]
    index = 0
    while index < len(text):
        char = text[index]
        if char == "`":
            end = text.find("`", index + 1)
            if end != -1 and "\n" not in text[index + 1 : end]:
                frames[-1][2].append("<code>" + html.escape(text[index + 1 : end]) + "</code>")
                index = end + 1
                continue
        if char == "[":
            label_end = text.find("](", index + 1)
            url_end = text.find(")", label_end + 2) if label_end != -1 else -1
            if url_end != -1 and "\n" not in text[index:url_end]:
                url = text[label_end + 2 : url_end]
                try:
                    parsed = urlsplit(url)
                    allowed = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                except ValueError:
                    allowed = False
                if allowed and not any(ord(c) < 33 for c in url):
                    frames[-1][2].append(
                        '<a href="'
                        + html.escape(url, quote=True)
                        + '">'
                        + html.escape(text[index + 1 : label_end])
                        + "</a>"
                    )
                    index = url_end + 1
                    continue
        marker = next((m for m in ("**", "~~", "*") if text.startswith(m, index)), None)
        if marker is not None:
            if frames[-1][0] == marker:
                _, tag, content = frames.pop()
                frames[-1][2].append("<" + tag + ">" + "".join(content) + "</" + tag + ">")
            elif len(frames) < 32 and not any(f[0] == marker for f in frames):
                frames.append((marker, {"**": "b", "*": "i", "~~": "s"}[marker], []))
            else:
                frames[-1][2].append(html.escape(marker))
            index += len(marker)
            continue
        frames[-1][2].append(html.escape(char))
        index += 1
    while len(frames) > 1:
        marker, _, content = frames.pop()
        frames[-1][2].append(html.escape(marker) + "".join(content))
    return "".join(frames[0][2])


def render_markdown(text: str, *, in_fence: bool = False) -> str:
    """Emit only balanced b/i/s/code/pre/a tags; unknown HTML stays escaped."""
    output: list[str] = []
    index = 0
    while index < len(text):
        if in_fence:
            end = text.find("```", index)
            end = len(text) if end == -1 else end
            output.append("<pre>" + html.escape(text[index:end]) + "</pre>")
            index = end
            if index < len(text):
                index += 3
                in_fence = False
            continue
        fence = text.find("```", index)
        end = len(text) if fence == -1 else fence
        for line in text[index:end].splitlines(keepends=True):
            heading = re.match(r"^#{1,6} +([^\n]*)(\n?)$", line)
            if heading:
                output.append("<b>" + _inline(heading[1]) + "</b>" + heading[2])
            else:
                output.append(_inline(line))
        index = end
        if index < len(text):
            index += 3
            # Keep source untouched; the conventional opening newline is syntax.
            if text[index : index + 1] == "\n":
                index += 1
            in_fence = True
    return "".join(output)


@dataclass(frozen=True)
class TelegramTextChunk:
    source: str
    html: str


def _code_line_boundary(text: str, end: int, *, in_fence: bool) -> int | None:
    """Keep a fenced line whole, counting its content rather than fence syntax.

    A source span can include invisible opening/closing syntax beyond the visible
    budget. Plain fallback requests are split independently from this span.
    """
    initially_in_fence = in_fence
    code_start = 0
    position = 0
    while (fence := text.find("```", position)) != -1 and fence < end:
        if fence + 3 > end:
            return fence  # Never bisect a delimiter and change the renderer state.
        in_fence = not in_fence
        position = fence + 3
        if in_fence:
            code_start = position + (text[position : position + 1] == "\n")
    if not in_fence:
        return None
    line_start = max(code_start, text.rfind("\n", code_start, end) + 1)
    prefix = render_markdown(text[:line_start], in_fence=initially_in_fence)
    if re.sub(r"<[^>]+>", "", prefix):
        return line_start  # Flush earlier visible content before the complete line.
    closing = text.find("```", max(end, code_start))
    newline = text.find("\n", max(end, code_start))
    line_end = min(point for point in (closing, newline, len(text)) if point != -1)
    line = text[line_start:line_end]
    end = line_start + _prefix_end(line, MAX_VISIBLE_UNITS)
    if end == line_end and newline == line_end and text_units(line) < MAX_VISIBLE_UNITS:
        end += 1
    return end


def split_telegram_text(text: str, *, inert: bool = False) -> list[TelegramTextChunk]:
    """Split source losslessly before rendering, preferring natural boundaries.

    HTML chunks fit the visible UTF-16 budget. Fence syntax remains in the
    lossless source span, but does not prematurely split a fitting code line.
    Each fragment reopens as its own pre. Inert text and plain fallback requests
    use a separate raw-source bound and never enter the renderer.
    """
    chunks: list[TelegramTextChunk] = []
    in_fence = False
    while text:
        end = _prefix_end(text, MAX_VISIBLE_UNITS)
        code_boundary = (
            _code_line_boundary(text, end, in_fence=in_fence)
            if not inert and end < len(text)
            else None
        )
        if code_boundary is not None:
            end = code_boundary
        elif end < len(text):
            window = text[:end]
            # Paragraph, line, sentence, then word. Keep separators in source.
            for separator in ("\n\n", "\n", ". ", "! ", "? ", " "):
                boundary = window.rfind(separator)
                if boundary >= 0:
                    candidate = boundary + len(separator)
                    # Inside code prefer complete lines, not sentence/word cuts.
                    if separator not in {"\n\n", "\n"} and (
                        in_fence != (window.count("```") % 2 == 1)
                    ):
                        continue
                    if not inert and not re.sub(
                        r"<[^>]+>", "", render_markdown(text[:candidate], in_fence=in_fence)
                    ):
                        continue
                    end = candidate
                    break
        source, text = text[:end], text[end:]
        rendered = html.escape(source) if inert else render_markdown(source, in_fence=in_fence)
        if not inert and not re.sub(r"<[^>]+>", "", rendered):
            if chunks and in_fence and source.strip() == "```":
                # Closing syntax can fall just beyond a full code chunk. Keep
                # it in the lossless source receipt, without an empty API send.
                previous = chunks[-1]
                chunks[-1] = TelegramTextChunk(previous.source + source, previous.html)
                in_fence = False
                continue
            # A message made only of markup still has a literal representation.
            rendered = html.escape(source)
        chunks.append(TelegramTextChunk(source, rendered))
        in_fence = in_fence != (source.count("```") % 2 == 1)
    return chunks or [TelegramTextChunk("", "")]


class PartialDeliveryLatch:
    """Retain unresolved terminal bookkeeping, never permission to resend.

    Pool identity stays strongly bound only while its existing terminal row has
    not settled. This is not a durable outbox: process-crash atomicity with the
    external provider is unchanged. Normal retries do not enter this latch.
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[int, str], tuple[Any, dict[str, bool]]] = {}

    def mark(self, pool: Any, key: str, metadata: dict[str, bool]) -> None:
        self._pending[(id(pool), key)] = (pool, dict(metadata))

    def contains(self, pool: Any, key: str) -> bool:
        value = self._pending.get((id(pool), key))
        return value is not None and value[0] is pool

    def metadata(self, pool: Any, key: str) -> dict[str, bool]:
        return dict(self._pending[(id(pool), key)][1]) if self.contains(pool, key) else {}

    def keys(self, pool: Any) -> list[str]:
        return [key for (_, key), (owner, _) in self._pending.items() if owner is pool]

    def clear(self, pool: Any, key: str) -> None:
        self._pending.pop((id(pool), key), None)
