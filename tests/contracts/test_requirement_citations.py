"""Requirement citations in tests must resolve to a canonical or retained contract."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Qualified IDs are the only actionable references. The legacy change-local
# shorthand is checked separately so a citation cannot evade resolution.
CITATION = re.compile(r"\bREQ-(?:[a-z0-9]+-)+[0-9]+\b")
SHORTHAND = re.compile(r"\bREQ-[0-9]+\b")
REQUIREMENT = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$", re.IGNORECASE)
SECTION = re.compile(r"^##\s+(.+?)\s*$")
ID_LINE = re.compile(r"^ID:\s*(REQ-(?:[a-z0-9]+-)+[0-9]+)\s*$")
FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class Citation:
    path: str
    line: int
    req_id: str


@dataclass(frozen=True)
class Definition:
    path: str
    heading: str
    section: str


def _unfenced_lines(text: str) -> list[str | None]:
    """Mask code fences so examples and fenced IDs are not definitions."""
    result: list[str | None] = []
    active: tuple[str, int] | None = None
    for line in text.splitlines():
        if active is None:
            opener = FENCE_OPEN.match(line)
            if opener:
                active = (opener.group(1)[0], len(opener.group(1)))
                result.append(None)
                continue
            result.append(line)
            continue
        result.append(None)
        closer = FENCE_CLOSE.match(line)
        if closer and closer.group(1)[0] == active[0] and len(closer.group(1)) >= active[1]:
            active = None
    return result


def _requirement_blocks(text: str) -> list[tuple[str | None, str, list[str]]]:
    """Return section, requirement heading, and unfenced body for each block."""
    blocks: list[tuple[str | None, str, list[str]]] = []
    section: str | None = None
    current_heading: str | None = None
    current_body: list[str] = []

    def close() -> None:
        nonlocal current_heading, current_body
        if current_heading is not None:
            blocks.append((section, current_heading, current_body))
        current_heading = None
        current_body = []

    for line in _unfenced_lines(text):
        if line is None:
            if current_heading is not None:
                current_body.append("")
            continue
        heading = REQUIREMENT.match(line)
        if heading:
            close()
            current_heading = heading.group(1)
            continue
        top_section = SECTION.match(line)
        if top_section and not line.startswith("###"):
            close()
            section = top_section.group(1)
            continue
        if current_heading is not None:
            current_body.append(line)
    close()
    return blocks


def _definitions(spec_files: list[Path], *, retained: bool) -> dict[str, list[Definition]]:
    found: dict[str, list[Definition]] = {}
    accepted = {"added requirements", "modified requirements"}
    for spec_file in spec_files:
        for section, heading, body in _requirement_blocks(
            spec_file.read_text("utf-8", errors="strict")
        ):
            if retained and (section or "").strip().lower() not in accepted:
                continue
            ids = [match.group(1) for line in body if (match := ID_LINE.fullmatch(line))]
            if len(ids) != 1:
                continue
            req_id = ids[0]
            # A definition belongs to the capability whose spec contains it.
            capability = spec_file.parent.name
            declared_capability = req_id.rsplit("-", 1)[0].removeprefix("REQ-")
            if capability != declared_capability:
                continue
            found.setdefault(req_id, []).append(
                Definition(
                    path=spec_file.as_posix(),
                    heading=heading,
                    section=(section or "").strip(),
                )
            )
    return found


def canonical_definitions(root: Path) -> dict[str, list[Definition]]:
    specs = sorted((root / "openspec" / "specs").rglob("spec.md"))
    return _definitions(specs, retained=False)


def retained_definitions(root: Path) -> dict[str, list[Definition]]:
    """Definitions in complete, unarchived changes that can still be applied."""
    changes = root / "openspec" / "changes"
    spec_files: list[Path] = []
    if not changes.is_dir():
        return {}
    for change in sorted(changes.iterdir()):
        if (
            not change.is_dir()
            or change.name == "archive"
            or not (change / "proposal.md").is_file()
            or not (change / "tasks.md").is_file()
        ):
            continue
        spec_files.extend(sorted((change / "specs").rglob("spec.md")))
    return _definitions(spec_files, retained=True)


def resolve(
    req_id: str, canonical: dict[str, list[Definition]], retained: dict[str, list[Definition]]
) -> str | None:
    """Return the authoritative source kind, or None for absent/ambiguous IDs."""
    if canonical.get(req_id):
        return "canonical"
    candidates = retained.get(req_id, [])
    return "retained" if len(candidates) == 1 else None


def test_requirement_citations_in_repository_tests_resolve() -> None:
    canonical = canonical_definitions(ROOT)
    retained = retained_definitions(ROOT)
    citations = collect_citations(ROOT)
    unresolved = [
        citation for citation in citations if resolve(citation.req_id, canonical, retained) is None
    ]
    assert not unresolved, "\n".join(
        f"{item.path}:{item.line}: unresolved or ambiguous {item.req_id}" for item in unresolved
    )


def test_repository_test_citations_use_capability_qualified_ids() -> None:
    shorthand = [
        f"{path.relative_to(ROOT).as_posix()}:{number}: {match.group(0)}"
        for path in repository_test_files(ROOT)
        for number, line in enumerate(path.read_text("utf-8", errors="strict").splitlines(), 1)
        for match in SHORTHAND.finditer(line)
    ]
    assert not shorthand, "\n".join(shorthand)


def test_repository_assertion_checks_frontend_and_python_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    valid = _req("widgets", "010")
    unknown = _req("widgets", "999")
    _write_spec(tmp_path / "openspec/specs/widgets/spec.md", "Requirements", valid)
    frontend = tmp_path / "frontend/src/widgets.test.tsx"
    python = tmp_path / "tests/test_widgets.py"
    for path in (frontend, python):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"// {valid}\n", encoding="utf-8")

    # Non-test source, hidden/vendor copies and nested worktrees are not test roots.
    ignored = (
        "frontend/src/widgets.tsx",
        "frontend/src/.hidden/ignored.test.ts",
        "frontend/src/node_modules/ignored.test.ts",
        "frontend/src/vendor/ignored.test.ts",
        "frontend/src/worktrees/ignored.test.ts",
        "frontend/src/nested-checkout/ignored.test.ts",
    )
    for relative in ignored:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"// {unknown}\n", encoding="utf-8")
    (tmp_path / "frontend/src/nested-checkout/.git").write_text("gitdir: elsewhere\n")

    test_requirement_citations_in_repository_tests_resolve()
    frontend.write_text(f"// {unknown}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=unknown):
        test_requirement_citations_in_repository_tests_resolve()
    frontend.write_text(f"// {valid}\n", encoding="utf-8")
    test_requirement_citations_in_repository_tests_resolve()

    python.write_text(f"# {unknown}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=unknown):
        test_requirement_citations_in_repository_tests_resolve()
    python.write_text(f"# {valid}\n", encoding="utf-8")
    test_requirement_citations_in_repository_tests_resolve()


def test_canonical_definition_wins_over_retained_copy(tmp_path: Path) -> None:
    req_id = _req("widgets", "001")
    _write_spec(
        tmp_path / "openspec/specs/widgets/spec.md",
        "Requirements",
        req_id,
    )
    _write_change(tmp_path, "modify-widgets", "MODIFIED Requirements", req_id)
    canonical = canonical_definitions(tmp_path)
    retained = retained_definitions(tmp_path)

    assert resolve(req_id, canonical, retained) == "canonical"


def test_only_added_or_modified_blocks_in_complete_retained_changes_resolve(tmp_path: Path) -> None:
    added = _req("widgets", "002")
    modified = _req("widgets", "003")
    removed = _req("widgets", "004")
    renamed = _req("widgets", "005")
    root = tmp_path
    _write_change(root, "keep-widgets", "ADDED Requirements", added)
    _append_spec_block(
        root / "openspec/changes/keep-widgets/specs/widgets/spec.md",
        "MODIFIED Requirements",
        modified,
    )
    _append_spec_block(
        root / "openspec/changes/keep-widgets/specs/widgets/spec.md",
        "REMOVED Requirements",
        removed,
    )
    _append_spec_block(
        root / "openspec/changes/keep-widgets/specs/widgets/spec.md",
        "RENAMED Requirements",
        renamed,
    )
    canonical = canonical_definitions(root)
    retained = retained_definitions(root)

    assert resolve(added, canonical, retained) == "retained"
    assert resolve(modified, canonical, retained) == "retained"
    assert resolve(removed, canonical, retained) is None
    assert resolve(renamed, canonical, retained) is None


def test_archived_incomplete_and_mention_only_ids_do_not_resolve(tmp_path: Path) -> None:
    archived = _req("widgets", "006")
    incomplete = _req("widgets", "007")
    mention = _req("widgets", "008")
    archive_file = (
        tmp_path / "openspec/changes/archive/2026-01-01-old-widgets/specs/widgets/spec.md"
    )
    _write_spec(archive_file, "ADDED Requirements", archived)
    incomplete_change = tmp_path / "openspec/changes/incomplete-widgets"
    (incomplete_change / "proposal.md").parent.mkdir(parents=True)
    (incomplete_change / "proposal.md").write_text("proposal\n", encoding="utf-8")
    _write_spec(
        incomplete_change / "specs/widgets/spec.md",
        "ADDED Requirements",
        incomplete,
    )
    mention_change = tmp_path / "openspec/changes/mention-widgets"
    (mention_change / "proposal.md").parent.mkdir(parents=True)
    (mention_change / "proposal.md").write_text(
        f"This prose mentions {mention}.\n", encoding="utf-8"
    )
    (mention_change / "tasks.md").write_text("- [ ] inspect the prose\n", encoding="utf-8")

    assert (
        resolve(archived, canonical_definitions(tmp_path), retained_definitions(tmp_path)) is None
    )
    assert (
        resolve(incomplete, canonical_definitions(tmp_path), retained_definitions(tmp_path)) is None
    )
    assert resolve(mention, canonical_definitions(tmp_path), retained_definitions(tmp_path)) is None


def test_duplicate_retained_definition_is_ambiguous(tmp_path: Path) -> None:
    req_id = _req("widgets", "009")
    _write_change(tmp_path, "first-widgets", "ADDED Requirements", req_id)
    _write_change(tmp_path, "second-widgets", "ADDED Requirements", req_id)

    assert resolve(req_id, canonical_definitions(tmp_path), retained_definitions(tmp_path)) is None


def collect_citations(root: Path) -> list[Citation]:
    found: dict[tuple[str, int, str], Citation] = {}
    for path in repository_test_files(root):
        relative = path.relative_to(root).as_posix()
        for number, line in enumerate(path.read_text("utf-8", errors="strict").splitlines(), 1):
            for req_id in CITATION.findall(line):
                item = Citation(relative, number, req_id)
                found[(relative, number, req_id)] = item
    return list(found.values())


def repository_test_files(root: Path) -> list[Path]:
    """Scan Python test roots and frontend tests without hidden/vendor/worktree copies."""
    found: set[Path] = set()
    excluded = {"node_modules", "vendor", "vendored", "worktrees", "__pycache__"}
    for base in (root / "tests", root / "roster", root / "frontend/src"):
        if not base.is_dir():
            continue
        frontend = base == root / "frontend/src"
        for directory, subdirectories, filenames in os.walk(base):
            if ".git" in subdirectories or ".git" in filenames:
                subdirectories[:] = []
                continue
            subdirectories[:] = [
                name for name in subdirectories if not name.startswith(".") and name not in excluded
            ]
            if not frontend and "tests" not in Path(directory).relative_to(root).parts:
                continue
            found.update(
                Path(directory) / name
                for name in filenames
                if not name.startswith(".")
                and (".test." in name if frontend else name.endswith(".py"))
            )
    return sorted(found)


def _req(capability: str, number: str) -> str:
    """Assemble fixture IDs so the repository-wide scan does not cite them."""
    return f"REQ-{capability}-{number}"


def _requirement(req_id: str) -> str:
    return (
        f"### Requirement: {req_id}\n"
        f"A widget SHALL remain available.\n\n"
        f"ID: {req_id}\n"
        f"\n#### Scenario: the widget remains available\n"
        f"- **WHEN** a client asks for the widget\n"
        f"- **THEN** the widget is returned\n"
    )


def _write_spec(path: Path, section: str, req_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"## {section}\n\n{_requirement(req_id)}",
        encoding="utf-8",
    )


def _append_spec_block(path: Path, section: str, req_id: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n## {section}\n\n{_requirement(req_id)}")


def _write_change(root: Path, name: str, section: str, req_id: str) -> None:
    change = root / "openspec/changes" / name
    change.mkdir(parents=True, exist_ok=True)
    (change / "proposal.md").write_text("## Why\nA widget change.\n", encoding="utf-8")
    (change / "tasks.md").write_text("- [ ] implement the widget\n", encoding="utf-8")
    _write_spec(change / "specs/widgets/spec.md", section, req_id)
