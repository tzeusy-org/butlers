"""Source-to-test mapping for butlers module structure.

Given a set of changed file paths (relative to repo root), produces a
deterministic list of test paths to run.  Used by the refinery to scope
test runs to changed modules instead of running the full suite.

Usage::

    from butlers.testing.source_test_map import resolve_test_paths

    paths = resolve_test_paths(["src/butlers/modules/memory/tools/search.py"])
    # -> ["tests/modules/memory/"]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cross-cutting files: any change triggers the full test suite
# ---------------------------------------------------------------------------

FULL_SUITE_TRIGGERS: frozenset[str] = frozenset(
    {
        "conftest.py",
        "pyproject.toml",
        "src/butlers/__init__.py",
        "src/butlers/modules/base.py",
        "src/butlers/testing/__init__.py",
        "src/butlers/testing/migration.py",
        "src/butlers/testing/source_test_map.py",
    }
)

# The sentinel returned when the full suite should run.
FULL_SUITE: list[str] = ["tests/"]

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
            "tests/test_resolve_owner_contact_info.py",
            "tests/test_upsert_delete_owner_contact_info.py",
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
    "frontend/",
    "docker/",
    "docker-compose.yml",
    "Dockerfile",
    "docs/",
    "grafana/",
    "Makefile",
    ".beads/",
    "pricing.toml",
    "LICENSE",
    "README.md",
    ".github/",
    "openspec/",
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_test_paths(changed_files: list[str]) -> list[str]:
    """Return a sorted, deduplicated list of test paths for *changed_files*.

    Parameters
    ----------
    changed_files:
        File paths relative to the repository root (e.g. from ``git diff --name-only``).

    Returns
    -------
    list[str]
        Sorted test directory/file paths.  An empty list means no tests are
        needed.  ``["tests/"]`` means the full suite should run.
    """
    # Normalise: strip leading ./ or /
    normalised = []
    for f in changed_files:
        f = f.lstrip("./")
        if f:
            normalised.append(f)

    # 1. Check for full-suite triggers
    for f in normalised:
        if f in FULL_SUITE_TRIGGERS:
            return list(FULL_SUITE)

    test_paths: set[str] = set()

    for f in normalised:
        # 2. If the changed file IS a test file, include it directly
        if f.startswith("tests/"):
            # For conftest changes inside test dirs, include the parent test dir
            if f.endswith("conftest.py"):
                # e.g. tests/api/conftest.py -> tests/api/
                parent = f.rsplit("/", 1)[0] + "/"
                test_paths.add(parent)
            else:
                test_paths.add(f)
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

        # 6. Catch-all: unrecognised src/butlers/ files -> run full suite
        if not matched and f.startswith("src/butlers/"):
            return list(FULL_SUITE)

    return sorted(test_paths)
