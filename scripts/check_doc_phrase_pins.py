"""Check exact wording requirements in repository Markdown files."""

from __future__ import annotations

import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

PIN_TABLE = Path("scripts/doc_phrase_pins.toml")


def check_pins(root: Path, today: date) -> list[str]:
    """Return all failed or invalid pins, with their document and authority."""
    root = root.resolve()
    try:
        with (root / PIN_TABLE).open("rb") as contents:
            table = tomllib.load(contents)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return [f"{PIN_TABLE}: cannot read pin table ({exc})"]

    pins = table.get("pin")
    if not isinstance(pins, list) or not pins:
        return [f"{PIN_TABLE}: pin table is empty or invalid"]

    failures: list[str] = []
    for index, pin in enumerate(pins, start=1):
        if not isinstance(pin, dict):
            failures.append(f"{PIN_TABLE}: pin {index} must be a table")
            continue
        file = pin.get("file")
        mode = pin.get("mode")
        phrase = pin.get("phrase")
        governs = pin.get("governs")
        expires = pin.get("expires")
        if (
            not isinstance(file, str)
            or not file.strip()
            or mode not in ("require", "forbid")
            or not isinstance(phrase, str)
            or not phrase.strip()
            or not isinstance(governs, str)
            or not governs.strip()
        ):
            failures.append(f"{PIN_TABLE}: pin {index} must define file, mode, phrase and governs")
            continue

        context = f"{file} ({governs}, phrase {phrase!r})"
        if expires is not None and type(expires) is not date:
            failures.append(f"{context}: expires must be a TOML date")
            continue
        if isinstance(expires, date) and expires < today:
            failures.append(f"{context}: pin expired on {expires.isoformat()}")
            continue

        relative = Path(file)
        if relative.is_absolute() or relative.suffix.lower() not in {".md", ".markdown"}:
            failures.append(f"{context}: expected a repository-relative Markdown file")
            continue
        try:
            document = (root / relative).resolve()
            if not document.is_relative_to(root):
                failures.append(f"{context}: document is outside the repository")
                continue
            text = document.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeError) as exc:
            failures.append(f"{context}: cannot read document ({exc})")
            continue

        present = phrase in text
        if mode == "require" and not present:
            failures.append(f"{context}: required phrase is missing")
        elif mode == "forbid" and present:
            failures.append(f"{context}: forbidden phrase is present")

    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = check_pins(root, today=datetime.now(UTC).date())
    for failure in failures:
        print(failure, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
