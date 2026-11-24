"""Exercise the retirement guard CLI against tracked, owned Git fixtures."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_retired_surfaces.py"
REGISTRY = ROOT / "scripts/retired_surfaces.toml"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _track(repo: Path, path: str, text: str = "live\n") -> Path:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    subprocess.run(["git", "add", "--", path], cwd=repo, check=True)
    return target


def _run(repo: Path, registry: Path = REGISTRY, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--registry", str(registry), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _deprecated(repo: Path, ceiling: int = 2, expires: date | None = None) -> Path:
    registry = repo / "registry.toml"
    registry.write_text(
        '[[surface]]\nid = "legacy"\nkind = "pattern"\nstate = "deprecated"\n'
        'pattern = "OLD"\nscope = ["src"]\n'
        f"ceiling = {ceiling}\nexpires = {expires or date.today() + timedelta(days=1)}\n"
        'bead = "bu-example"\nsurvivor = "new_api"\n'
    )
    return registry


def _assert_failure(result: subprocess.CompletedProcess[str], location: str) -> None:
    assert result.returncode == 1, result.stdout + result.stderr
    assert location in result.stderr
    assert "bead=" in result.stderr and "survivor=" in result.stderr


def test_contacts_registry_preserves_scope_allowance_and_multiline_sql(repo: Path) -> None:
    actual = _run(ROOT)
    assert actual.returncode == 0, actual.stdout + actual.stderr
    summary = next(line for line in actual.stdout.splitlines() if "contacts-tables:" in line)
    assert int(re.search(r"scanned=(\d+)", summary)[1]) > 100
    assert "matched=1 allowed=1" in summary

    briefing = _track(repo, "src/butlers/jobs/briefing.py", "# no JOIN contacts\n")
    source = _track(repo, "src/butlers/example.py")
    _track(repo, "roster/messenger/butler.toml")
    for excluded in (
        "src/butlers/migrations/old.py",
        "roster/relationship/tests/old.py",
        "src/butlers/__pycache__/old.py",
        "src/butlers/example.md",
    ):
        _track(repo, excluded, "FROM contacts\n")
    benign = [
        "JOIN contacts_source_links sl ON sl.local_contact_id = m.id",
        "SELECT * FROM contacts_sync_state",
        "INSERT INTO public.contacts_dropbak SELECT * FROM other",
        "# ORG (Organization) - from contacts.company",
        "anchor_row.contact_id",
    ]
    source.write_text("\n".join(benign))
    assert _run(repo).returncode == 0
    for offender in (
        "SELECT * FROM contacts WHERE id = $1",
        "JOIN public.contacts c ON c.id = pc.contact_id",
        "INSERT INTO contact_info (type, value) VALUES ($1, $2)",
        "UPDATE public.contacts SET name = $1",
        "DELETE\nFROM\npublic.contact_info",
        "REFERENCES contacts(id)",
    ):
        source.write_text("# prefix\n" + offender)
        _assert_failure(_run(repo), "src/butlers/example.py:2")
    source.write_text("live\n")
    # The exception is tied to its path and starting line, not to the whole file.
    briefing.write_text("# no JOIN contacts\nSELECT * FROM contacts\n")
    _assert_failure(_run(repo), "src/butlers/jobs/briefing.py:2")
    source.write_text("# no JOIN contacts\n")
    _assert_failure(_run(repo), "src/butlers/example.py:1")
    source.write_text("live\n")
    briefing.write_text("# no JOIN contacts\n")
    query = _track(repo, "roster/relationship/query.sql", "SELECT * FROM public.contacts")
    _assert_failure(_run(repo), "roster/relationship/query.sql:1")
    query.write_text("SELECT * FROM relationship.entity_facts")
    assert _run(repo).returncode == 0


def test_messenger_registry_rejects_each_retired_path(repo: Path) -> None:
    _track(repo, "src/butlers/live.py")
    _track(repo, "roster/messenger/butler.toml")
    assert _run(repo).returncode == 0
    for path in (
        "roster/messenger/api/router.py",
        "roster/messenger/modules/__init__.py",
        "roster/messenger/tools/delivery/tracking.py",
    ):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("live\n")
        # The old exists() check also covered untracked files.
        _assert_failure(_run(repo), f"{path}:1")
        _track(repo, path)
        _assert_failure(_run(repo), f"{path}:1")
        target.unlink()
        subprocess.run(["git", "add", "--", path], cwd=repo, check=True)
        target.mkdir()
        _assert_failure(_run(repo), f"{path}:1")
        target.rmdir()
        assert _run(repo).returncode == 0


def test_deprecated_ceiling_counts_occurrences_and_union(repo: Path) -> None:
    source = _track(repo, "src/live.py", "OLD OLD\n")
    registry = _deprecated(repo)
    assert _run(repo, registry).returncode == 0
    source.write_text("OLD OLD OLD\n")
    _assert_failure(_run(repo, registry), "src/live.py:1")
    other = _track(repo, "src/other.py", "OLD\n")
    registry = _deprecated(repo, ceiling=3)
    source.write_text("OLD OLD\n")
    assert _run(repo, registry).returncode == 0
    # Two independent edits lower different files and ceilings. Their union
    # retains both reductions and satisfies the smaller ceiling.
    for left, right, ceiling in (
        ("live\n", "OLD\n", 1),
        ("OLD OLD\n", "live\n", 2),
        ("live\n", "live\n", 1),
    ):
        source.write_text(left)
        other.write_text(right)
        registry = _deprecated(repo, ceiling=ceiling)
        assert _run(repo, registry).returncode == 0


def test_expiry_is_inclusive_and_tighten_cannot_hide_it(repo: Path) -> None:
    _track(repo, "src/live.py", "OLD\n")
    registry = _deprecated(repo, expires=date.today())
    assert _run(repo, registry).returncode == 0
    registry = _deprecated(repo, expires=date.today() - timedelta(days=1))
    before = registry.read_bytes()
    for args in ((), ("--tighten",)):
        _assert_failure(_run(repo, registry, *args), "registry.toml:")
        assert registry.read_bytes() == before


def test_empty_scopes_and_invalid_registry_fail_closed(repo: Path) -> None:
    registry = _deprecated(repo)
    _assert_failure(_run(repo, registry), "registry.toml:")
    _track(repo, "src/live.py", "OLD\n")
    assert _run(repo, registry).returncode == 0
    original = registry.read_text()
    for invalid in (
        original.replace('kind = "pattern"', 'kind = "symbol"'),
        original.replace("ceiling = 2", "ceiling = true"),
        original.replace('pattern = "OLD"', 'pattern = "["'),
        original.replace('scope = ["src"]', 'scope = ["../outside"]'),
        original.replace('survivor = "new_api"', 'survvor = "new_api"'),
        "surface = []\n",
    ):
        registry.write_text(invalid)
        assert _run(repo, registry).returncode == 1
    registry.write_text(original)
    (repo / "src/live.py").write_bytes(b"\xff")
    _assert_failure(_run(repo, registry), "registry.toml:")
    (repo / "src/live.py").write_text("live\n")
    assert _run(repo, registry).returncode == 0


def test_tighten_only_lowers_ceilings_and_preserves_registry_text(repo: Path) -> None:
    source = _track(repo, "src/live.py", "OLD\n")
    registry = _deprecated(repo, ceiling=3)
    registry.write_text("# owner rationale\n" + registry.read_text())
    before = registry.read_text()
    assert _run(repo, registry, "--tighten").returncode == 0
    assert registry.read_text() == before.replace("ceiling = 3", "ceiling = 1")
    assert tomllib.loads(registry.read_text())["surface"][0]["ceiling"] == 1
    source.write_text("OLD OLD\n")
    before = registry.read_bytes()
    _assert_failure(_run(repo, registry, "--tighten"), "src/live.py:1")
    assert registry.read_bytes() == before
    source.write_text("live\n")
    assert _run(repo, registry, "--tighten").returncode == 0
    assert tomllib.loads(registry.read_text())["surface"][0]["ceiling"] == 0
