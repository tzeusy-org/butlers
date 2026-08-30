"""Source-to-test mapping for Butlers module structure.

Given a set of changed file paths (relative to repo root), produces a
deterministic list of test paths to run.  Used by the refinery to scope
test runs to changed modules instead of running the full suite.

Usage::

    from butlers.testing.source_test_map import resolve_test_paths

    paths = resolve_test_paths(["src/butlers/modules/memory/tools/search.py"])
    # -> ["tests/modules/memory/"]
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def configured_testpaths(repo_dir: str | Path | None = None) -> list[str]:
    """Read pytest's configured roots instead of maintaining a second list.

    A planner that calls ``tests/`` its full suite silently omits every
    ``roster/<butler>/tests/`` path.  Keeping this lookup at the boundary makes
    the planner follow the same configured roots as pytest and CI.
    """

    root = Path(repo_dir) if repo_dir is not None else Path(__file__).resolve().parents[3]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    testpaths = config["tool"]["pytest"]["ini_options"]["testpaths"]
    return [f"{Path(path).as_posix().rstrip('/')}/" for path in testpaths]


# ---------------------------------------------------------------------------
# Cross-cutting files: any change triggers the full test suite
# ---------------------------------------------------------------------------

FULL_SUITE_TRIGGERS: frozenset[str] = frozenset(
    {
        "conftest.py",
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "pricing.toml",
        "docker-compose.yml",
        "Dockerfile",
        "src/butlers/__init__.py",
        "src/butlers/db.py",
        "src/butlers/modules/base.py",
        "src/butlers/modules/registry.py",
        "src/butlers/testing/__init__.py",
        "src/butlers/testing/changed_files.py",
        "src/butlers/testing/migration.py",
        "src/butlers/testing/scoped_runner.py",
        "src/butlers/testing/source_test_map.py",
    }
)

# Prefixes whose changes require a human to state the broader verification
# boundary.  The planner returns the complete pytest roots as a conservative
# suggestion, but labels the result an escalation rather than pretending that
# inference is exhaustive.
FULL_SUITE_PREFIX_TRIGGERS: tuple[str, ...] = (
    ".github/",
    "alembic/",
    "docker/",
    "migrations/",
    "src/butlers/core/",
    "src/butlers/migrations/",
    "src/butlers/testing/",
)

# The sentinel returned when the plan must escalate.  It is loaded from pytest
# configuration so it cannot drift from ``testpaths``.
FULL_SUITE: list[str] = configured_testpaths()

# ---------------------------------------------------------------------------
# Prefix-based mapping: source path prefix -> test directories
#
# Order matters: more-specific prefixes MUST come before less-specific ones
# so that the first match wins.
# ---------------------------------------------------------------------------

_PREFIX_MAP: list[tuple[str, list[str]]] = [
    # --- Core: runtimes map to adapter tests ---
    ("src/butlers/core/runtimes/", ["tests/adapters/"]),
    # --- Core: telemetry & metrics ---
    ("src/butlers/core/telemetry.py", ["tests/telemetry/"]),
    ("src/butlers/core/metrics.py", ["tests/telemetry/", "tests/core/"]),
    # --- Core: skills ---
    ("src/butlers/core/skills.py", ["tests/features/"]),
    # --- Core: sessions ---
    ("src/butlers/core/sessions.py", ["tests/core/"]),
    # --- Core: catch-all (includes daemon tests since daemon exercises core) ---
    ("src/butlers/core/", ["tests/core/", "tests/daemon/"]),
    # --- API layer ---
    ("src/butlers/api/", ["tests/api/"]),
    # --- Connectors ---
    (
        "src/butlers/connectors/gmail",
        ["tests/connectors/", "tests/test_gmail_connector.py", "tests/test_gmail_policy.py"],
    ),
    ("src/butlers/connectors/", ["tests/connectors/"]),
    # --- Modules: memory (specific before generic) ---
    ("src/butlers/modules/memory/", ["tests/modules/memory/"]),
    # --- Modules: approvals ---
    (
        "src/butlers/modules/approvals/",
        ["tests/modules/", "tests/test_approvals_models.py"],
    ),
    # --- Modules: contacts ---
    (
        "src/butlers/modules/contacts/",
        [
            "tests/modules/",
            "tests/test_identity.py",
            "tests/test_resolve_owner_entity_info.py",
            "tests/test_upsert_delete_owner_entity_info.py",
        ],
    ),
    # --- Modules: mailbox ---
    ("src/butlers/modules/mailbox/", ["tests/modules/", "tests/integration/"]),
    # --- Modules: metrics ---
    ("src/butlers/modules/metrics/", ["tests/modules/"]),
    # --- Modules: calendar ---
    ("src/butlers/modules/calendar.py", ["tests/modules/"]),
    # --- Modules: catch-all ---
    ("src/butlers/modules/", ["tests/modules/"]),
    # --- Tools ---
    ("src/butlers/tools/", ["tests/tools/"]),
    # --- Storage ---
    ("src/butlers/storage/", ["tests/test_blob_storage.py"]),
    # --- CLI ---
    ("src/butlers/cli.py", ["tests/cli/"]),
    # --- Daemon ---
    ("src/butlers/daemon.py", ["tests/daemon/"]),
    # --- Config ---
    ("src/butlers/config.py", ["tests/config/"]),
    # --- DB ---
    ("src/butlers/db.py", ["tests/core/test_db.py", "tests/core/test_db_ssl.py"]),
    # --- Credentials ---
    (
        "src/butlers/credential_store.py",
        [
            "tests/test_credential_store.py",
            "tests/test_secrets_credentials.py",
            "tests/test_shared_credential_consumption.py",
        ],
    ),
    ("src/butlers/credentials.py", ["tests/config/test_credentials.py"]),
    (
        "src/butlers/google_credentials.py",
        ["tests/test_google_credentials.py", "tests/test_google_credentials_credential_store.py"],
    ),
    # --- Alembic migrations ---
    ("alembic/", ["tests/migrations/", "tests/config/"]),
    # --- Scripts ---
    ("scripts/", ["tests/scripts/"]),
]

# ---------------------------------------------------------------------------
# Non-Python paths: changes here produce no Python test paths.
# ---------------------------------------------------------------------------

_NO_TEST_PREFIXES: tuple[str, ...] = (
    "docs/",
    "grafana/",
    ".beads/",
    "LICENSE",
    "README.md",
)

# ---------------------------------------------------------------------------
# Roster: butler-specific changes map to butler-specific tests
# ---------------------------------------------------------------------------


def _roster_test_paths(path: str) -> list[str] | None:
    """Return test paths for a roster/<butler>/... change, or None if not roster."""
    if not path.startswith("roster/"):
        return None
    parts = path.split("/")
    if len(parts) < 3:
        return None
    butler = parts[1]
    roster_test_dir = f"roster/{butler}/tests/"

    # Module-level migrations in roster may also be tested by tests/config/
    subpath = "/".join(parts[2:])
    extra: list[str] = []
    if subpath.startswith("migrations/"):
        extra.append("tests/config/")

    return [roster_test_dir] + extra


def _normalise_path(path: str) -> str:
    """Strip only an optional relative ``./`` prefix, never a leading dot."""

    while path.startswith("./"):
        path = path[2:]
    return path


def _requires_full_suite(path: str) -> bool:
    """Whether a path is too shared or risky for a guessed narrow scope."""

    if path in FULL_SUITE_TRIGGERS:
        return True
    if any(path.startswith(prefix) for prefix in FULL_SUITE_PREFIX_TRIGGERS):
        return True
    if path.startswith("roster/") and "/migrations/" in path:
        return True
    if path.startswith("src/butlers/modules/") and "/migrations/" in path:
        return True
    return False


def _support_file_scope(
    path: str,
    *,
    repo_dir: str | Path | None,
    full_suite: list[str],
) -> list[str]:
    """Return a test-bearing owner for support code, or escalate safely.

    Fixture assets can sit many directories below ``tests/`` without any
    collectable test nearby.  Selecting that leaf (or its empty parent) gives a
    green, zero-test run.  Cross-suite fixtures are especially ambiguous, so
    they escalate instead of guessing which consumer owns them.
    """

    if path.startswith("tests/fixtures/"):
        return full_suite

    root = Path(repo_dir) if repo_dir is not None else Path(__file__).resolve().parents[3]
    parent = Path(path).parent
    if any((root / parent).glob("test_*.py")):
        return [f"{parent.as_posix().rstrip('/')}/"]
    return full_suite


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_test_paths(
    changed_files: list[str], *, repo_dir: str | Path | None = None
) -> list[str]:
    """Return a sorted, deduplicated list of test paths for *changed_files*.

    Parameters
    ----------
    changed_files:
        File paths relative to the repository root (e.g. from ``git diff --name-only``).

    Returns
    -------
    list[str]
        Sorted test directory/file paths.  An empty list means no tests are
        needed. The configured pytest roots mean the plan must escalate.
    """
    # Normalise an optional relative prefix while preserving dot-directories
    # such as .github/ and .beads/.
    normalised = []
    for f in changed_files:
        f = _normalise_path(f)
        if f:
            normalised.append(f)

    full_suite = configured_testpaths(repo_dir)

    # 1. Check for full-suite triggers.
    for f in normalised:
        if _requires_full_suite(f):
            return full_suite

    test_paths: set[str] = set()

    for f in normalised:
        # 2. If the changed file IS a test file, include it directly
        if f.startswith("tests/") or (f.startswith("roster/") and "/tests/" in f):
            # For conftest changes inside test dirs, include the parent test dir
            if f.endswith("conftest.py"):
                # e.g. tests/api/conftest.py -> tests/api/
                parent = f.rsplit("/", 1)[0] + "/"
                test_paths.add(parent)
            elif Path(f).name.startswith("test_") and f.endswith(".py"):
                test_paths.add(f)
            else:
                # Support modules and package markers are imported by real test
                # files. Running the helper itself collects nothing, so select
                # a test-bearing owner or escalate when that ownership is not
                # mechanically knowable.
                support_scope = _support_file_scope(
                    f,
                    repo_dir=repo_dir,
                    full_suite=full_suite,
                )
                if support_scope == full_suite:
                    return full_suite
                test_paths.update(support_scope)
            continue

        if f == "roster/conftest.py":
            test_paths.add("roster/")
            continue

        # 3. Skip non-Python / non-testable paths
        if any(f.startswith(prefix) or f == prefix.rstrip("/") for prefix in _NO_TEST_PREFIXES):
            continue

        # 4. Roster-specific mapping
        roster = _roster_test_paths(f)
        if roster is not None:
            test_paths.update(roster)
            continue

        # 5. Prefix-based source-to-test mapping
        matched = False
        for prefix, targets in _PREFIX_MAP:
            if f.startswith(prefix) or f == prefix.rstrip("/"):
                test_paths.update(targets)
                matched = True
                break

        # 6. Catch-all: source, configuration, workflow, and other unknown
        # paths must escalate.  Only the explicit non-test allowlist above may
        # produce an empty plan.
        if not matched:
            return full_suite

    return sorted(test_paths)
