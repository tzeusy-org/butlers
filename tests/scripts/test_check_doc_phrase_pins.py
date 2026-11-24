"""Exercise the published Markdown pin contract against temporary files."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.check_doc_phrase_pins import PIN_TABLE, check_pins

_TODAY = date(2032, 4, 17)
_DOC = Path("docs/example.md")
_PHRASE = "Exact scope"
_GOVERNS = "REQ-testing-053"


def _write_pin(root: Path, mode: str = "require", expires: date | None = None) -> Path:
    table = root / PIN_TABLE
    table.parent.mkdir()
    fields = [
        "[[pin]]",
        f'file = "{_DOC}"',
        f'mode = "{mode}"',
        f'phrase = "{_PHRASE}"',
        f'governs = "{_GOVERNS}"',
    ]
    if expires is not None:
        fields.append(f"expires = {expires.isoformat()}")
    table.write_text("\n".join(fields) + "\n", encoding="utf-8")

    document = root / _DOC
    document.parent.mkdir()
    document.write_text(f"Here is the {_PHRASE}.\n", encoding="utf-8")
    return document


def _failure(tmp_path: Path, detail: str, *, today: date = _TODAY) -> None:
    [error] = check_pins(tmp_path, today)
    assert str(_DOC) in error and _PHRASE in error and _GOVERNS in error
    assert detail in error


def test_required_phrase_removed(tmp_path: Path) -> None:
    document = _write_pin(tmp_path)
    assert check_pins(tmp_path, _TODAY) == []

    document.write_text(_PHRASE.casefold(), encoding="utf-8")
    _failure(tmp_path, "required phrase is missing")


def test_forbidden_phrase_added(tmp_path: Path) -> None:
    document = _write_pin(tmp_path, mode="forbid")
    document.write_text(_PHRASE.casefold(), encoding="utf-8")
    assert check_pins(tmp_path, _TODAY) == []

    document.write_text(_PHRASE, encoding="utf-8")
    _failure(tmp_path, "forbidden phrase is present")


def test_pinned_document_missing(tmp_path: Path) -> None:
    document = _write_pin(tmp_path)
    assert check_pins(tmp_path, _TODAY) == []

    document.rename(document.with_name("moved.md"))
    _failure(tmp_path, "cannot read document")


def test_expired_pin(tmp_path: Path) -> None:
    _write_pin(tmp_path, expires=_TODAY)
    assert check_pins(tmp_path, _TODAY) == []

    _failure(tmp_path, "pin expired on 2032-04-17", today=date(2032, 4, 18))


def test_empty_table(tmp_path: Path) -> None:
    _write_pin(tmp_path)
    assert check_pins(tmp_path, _TODAY) == []

    (tmp_path / PIN_TABLE).write_text("# No pins left.\n", encoding="utf-8")
    assert check_pins(tmp_path, _TODAY) == [f"{PIN_TABLE}: pin table is empty or invalid"]
