"""Tests for scripts/extract-frontend-copy.py.

Regression guard for bu-5n509. The extractor originally collected only JSX text
nodes and a fixed attribute allowlist, so copy assembled in JS expressions --
`toast.error("...")`, template literals, ternary branches -- was invisible to it.
That was tolerable while the inventory was advisory, but CI job
`frontend-copy-inventory-guard` (bu-erfdj) now regenerates the file and fails the
build on any diff, which makes the inventory read as authoritative while being
structurally blind to a whole category of visible copy.

These tests pin the widened collection surface, the `{}` rendering of
interpolated expressions, the depth-0 rule that keeps nested non-copy strings
out, and the generated header's self-declared scope.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "extract-frontend-copy.py"


def _extractor() -> ModuleType:
    spec = importlib.util.spec_from_file_location("extract_frontend_copy", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract(tmp_path: Path, source: str) -> list[str]:
    module = _extractor()
    fixture = tmp_path / "FixturePage.tsx"
    fixture.write_text(source, encoding="utf-8")
    return module.extract_strings_from_file(fixture)


# ---------------------------------------------------------------------------
# The bug: copy built inside JS expressions
# ---------------------------------------------------------------------------


def test_collects_plain_string_argument_to_a_toast_call(tmp_path: Path) -> None:
    """The shape that made the inventory wrong: hundreds of toast strings, none seen."""
    strings = _extract(
        tmp_path,
        """
        const handleVerifyAll = () => {
          toast.warning("Verify all was called recently. Wait 60 seconds before retrying.");
        };
        """,
    )
    assert "Verify all was called recently. Wait 60 seconds before retrying." in strings


def test_collects_template_literal_and_its_conditional_suffix(tmp_path: Path) -> None:
    """The bead's shape: a template literal whose tail is a ternary of more templates."""
    strings = _extract(
        tmp_path,
        """
        toast.success(
          `Verified ${ok}/${total} models${failed > 0 ? ` · ${failed} failed` : ""}`,
        );
        """,
    )
    assert "Verified {}/{} models{}" in strings
    assert "· {} failed" in strings


def test_collects_both_branches_of_a_ternary_attribute(tmp_path: Path) -> None:
    strings = _extract(
        tmp_path,
        """
        <button title={paused ? "Resume this butler" : "Pause this butler"} />
        """,
    )
    assert "Resume this butler" in strings
    assert "Pause this butler" in strings


def test_collects_template_literal_in_an_attribute(tmp_path: Path) -> None:
    strings = _extract(
        tmp_path,
        """
        <Row aria-label={`Session ${session.id} started ${when}`} />
        """,
    )
    assert "Session {} started {}" in strings


# ---------------------------------------------------------------------------
# The noise the widening must not let in
# ---------------------------------------------------------------------------


def test_ignores_class_names_keys_and_routes(tmp_path: Path) -> None:
    strings = _extract(
        tmp_path,
        """
        <div
          className="flex items-center gap-2 rounded-md border"
          data-testid="butler-row"
          onClick={() => navigate("/butlers/overview")}
        />
        """,
    )
    assert strings == []


def test_ignores_strings_nested_inside_a_call_within_a_copy_site(tmp_path: Path) -> None:
    """Depth-0 rule: `t("errors.rate_limited")` is a lookup key, not copy."""
    strings = _extract(
        tmp_path,
        """
        toast.error(t("errors.rate_limited"), { id: "verify-all", className: "w-96" });
        """,
    )
    assert strings == []


def test_ignores_a_template_literal_that_is_only_interpolation(tmp_path: Path) -> None:
    strings = _extract(tmp_path, "toast.error(`${err.message}`);")
    assert strings == []


def test_ignores_class_name_template_literals(tmp_path: Path) -> None:
    """`className` is not a copy anchor, so its template literals never reach the filter."""
    strings = _extract(
        tmp_path,
        """
        <div className={`flex items-center ${active ? "bg-accent" : "bg-muted"}`} />
        """,
    )
    assert strings == []


# ---------------------------------------------------------------------------
# Stability
# ---------------------------------------------------------------------------


def test_multiline_template_literal_collapses_to_one_line(tmp_path: Path) -> None:
    """An inventory entry is one Markdown list item; a raw newline would break it."""
    strings = _extract(
        tmp_path,
        """
        toast.error(`Could not reach the butler.
          Check that the daemon is running.`);
        """,
    )
    assert "Could not reach the butler. Check that the daemon is running." in strings
    assert all("\n" not in s for s in strings)


def test_extraction_is_deterministic_and_deduplicated(tmp_path: Path) -> None:
    source = """
        toast.error("Save failed");
        toast.error("Save failed");
        <span>Save failed</span>
    """
    first = _extract(tmp_path, source)
    second = _extract(tmp_path, source)
    assert first == second
    assert first.count("Save failed") == 1


# ---------------------------------------------------------------------------
# The inventory declares its own limits (the other half of the acceptance criterion)
# ---------------------------------------------------------------------------


def test_generated_header_declares_what_the_inventory_does_not_cover(tmp_path: Path) -> None:
    module = _extractor()
    fixture = tmp_path / "FixturePage.tsx"
    fixture.write_text("<span>Hello there</span>", encoding="utf-8")
    report, _ = module.generate_report([fixture])

    header = report.split("## `", 1)[0]
    assert "## Scope" in header
    # Names the collection surface, so a reader knows what a hit means...
    assert "toast" in header
    # ...and names the blind spots, so a reader knows what a miss does not mean.
    assert "Not covered" in header
    assert "{}" in header


def test_report_is_a_pure_function_of_the_sources(tmp_path: Path) -> None:
    """The CI guard diffs the regenerated file; any nondeterminism would flap it."""
    module = _extractor()
    fixture = tmp_path / "FixturePage.tsx"
    fixture.write_text('toast.success("Butler restored");', encoding="utf-8")
    assert module.generate_report([fixture]) == module.generate_report([fixture])


# ---------------------------------------------------------------------------
# The same claim, against the real frontend rather than a fixture
# ---------------------------------------------------------------------------

# Deliberately independent of the extractor's own scanner: a dumb regex for the
# simplest possible display call, `toast.error("literal")`. If the extractor and
# this regex disagree about a real page, the extractor is the one that is wrong.
_SIMPLE_TOAST_RE = re.compile(
    r"""\btoast\.(?:success|error|warning|info|message|loading)\(\s*"([^"\\]+)"\s*[,)]"""
)


def test_every_simple_toast_literal_on_a_real_page_reaches_the_inventory() -> None:
    """bu-5n509: toast copy was the largest category the extractor could not see."""
    module = _extractor()
    checked = 0
    missing: list[tuple[str, str]] = []

    for path in module.collect_tsx_files(module.SCAN_DIRS):
        if path.stem.endswith(".test") or path.stem.endswith(".spec"):
            continue
        source = path.read_text(encoding="utf-8")
        literals = [
            m.group(1)
            for m in _SIMPLE_TOAST_RE.finditer(source)
            if module.is_user_facing(m.group(1))
        ]
        if not literals:
            continue
        extracted = set(module.extract_strings_from_file(path))
        for literal in literals:
            checked += 1
            if " ".join(literal.split()) not in extracted:
                missing.append((str(path), literal))

    assert checked > 50, f"expected the real frontend to exercise this; only found {checked}"
    assert missing == []
