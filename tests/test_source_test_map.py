"""Tests for the source-to-test mapping module."""

from butlers.testing.source_test_map import (
    FULL_SUITE,
    FULL_SUITE_TRIGGERS,
    resolve_test_paths,
)

# ---------------------------------------------------------------------------
# Full suite triggers
# ---------------------------------------------------------------------------


class TestFullSuiteTriggers:
    def test_conftest_triggers_full_suite(self):
        assert resolve_test_paths(["conftest.py"]) == FULL_SUITE

    def test_pyproject_triggers_full_suite(self):
        assert resolve_test_paths(["pyproject.toml"]) == FULL_SUITE

    def test_module_base_triggers_full_suite(self):
        assert resolve_test_paths(["src/butlers/modules/base.py"]) == FULL_SUITE

    def test_testing_init_triggers_full_suite(self):
        assert resolve_test_paths(["src/butlers/testing/__init__.py"]) == FULL_SUITE

    def test_tests_conftest_triggers_full_suite(self):
        assert resolve_test_paths(["tests/conftest.py"]) == FULL_SUITE

    def test_modules_registry_triggers_full_suite(self):
        assert resolve_test_paths(["src/butlers/modules/registry.py"]) == FULL_SUITE

    def test_all_triggers_are_tested(self):
        for trigger in FULL_SUITE_TRIGGERS:
            result = resolve_test_paths([trigger])
            assert result == FULL_SUITE, f"{trigger} should trigger full suite"


# ---------------------------------------------------------------------------
# Direct module-to-test-dir mappings
# ---------------------------------------------------------------------------


class TestApiMapping:
    def test_api_router(self):
        result = resolve_test_paths(["src/butlers/api/routers/search.py"])
        assert result == ["tests/api/"]

    def test_api_model(self):
        result = resolve_test_paths(["src/butlers/api/models/butler.py"])
        assert result == ["tests/api/"]

    def test_api_middleware(self):
        result = resolve_test_paths(["src/butlers/api/middleware.py"])
        assert result == ["tests/api/"]


class TestCoreMapping:
    def test_core_scheduler(self):
        result = resolve_test_paths(["src/butlers/core/scheduler.py"])
        assert "tests/core/" in result
        assert "tests/daemon/" in result

    def test_core_runtimes_map_to_adapters(self):
        result = resolve_test_paths(["src/butlers/core/runtimes/claude_code.py"])
        assert result == ["tests/adapters/"]

    def test_core_telemetry(self):
        result = resolve_test_paths(["src/butlers/core/telemetry.py"])
        assert result == ["tests/telemetry/"]

    def test_core_metrics(self):
        result = resolve_test_paths(["src/butlers/core/metrics.py"])
        assert "tests/telemetry/" in result
        assert "tests/core/" in result

    def test_core_skills(self):
        result = resolve_test_paths(["src/butlers/core/skills.py"])
        assert result == ["tests/features/"]


class TestConnectorsMapping:
    def test_generic_connector(self):
        result = resolve_test_paths(["src/butlers/connectors/telegram_bot.py"])
        assert result == ["tests/connectors/"]

    def test_gmail_connector_includes_top_level_tests(self):
        result = resolve_test_paths(["src/butlers/connectors/gmail.py"])
        assert "tests/connectors/" in result
        assert "tests/test_gmail_connector.py" in result
        assert "tests/test_gmail_policy.py" in result


class TestModulesMapping:
    def test_memory_module(self):
        result = resolve_test_paths(["src/butlers/modules/memory/tools/search.py"])
        assert result == ["tests/modules/memory/"]

    def test_approvals_module(self):
        result = resolve_test_paths(["src/butlers/modules/approvals/gate.py"])
        assert "tests/modules/" in result
        assert "tests/test_approvals_models.py" in result

    def test_contacts_module(self):
        result = resolve_test_paths(["src/butlers/modules/contacts/__init__.py"])
        assert "tests/modules/" in result
        assert "tests/test_identity.py" in result

    def test_mailbox_module(self):
        result = resolve_test_paths(["src/butlers/modules/mailbox/__init__.py"])
        assert "tests/modules/" in result
        assert "tests/integration/" in result

    def test_calendar_module(self):
        result = resolve_test_paths(["src/butlers/modules/calendar.py"])
        assert result == ["tests/modules/"]

    def test_generic_module(self):
        result = resolve_test_paths(["src/butlers/modules/metrics/__init__.py"])
        assert result == ["tests/modules/"]


class TestToolsMapping:
    def test_tools(self):
        result = resolve_test_paths(["src/butlers/tools/extraction.py"])
        assert result == ["tests/tools/"]


class TestTopLevelSourceFiles:
    def test_cli(self):
        assert resolve_test_paths(["src/butlers/cli.py"]) == ["tests/cli/"]

    def test_daemon(self):
        assert resolve_test_paths(["src/butlers/daemon.py"]) == ["tests/daemon/"]

    def test_config(self):
        assert resolve_test_paths(["src/butlers/config.py"]) == ["tests/config/"]

    def test_db(self):
        result = resolve_test_paths(["src/butlers/db.py"])
        assert "tests/core/test_db.py" in result
        assert "tests/core/test_db_ssl.py" in result

    def test_credential_store(self):
        result = resolve_test_paths(["src/butlers/credential_store.py"])
        assert "tests/test_credential_store.py" in result
        assert "tests/test_secrets_credentials.py" in result

    def test_google_credentials(self):
        result = resolve_test_paths(["src/butlers/google_credentials.py"])
        assert "tests/test_google_credentials.py" in result
        assert "tests/test_google_credentials_credential_store.py" in result

    def test_storage(self):
        result = resolve_test_paths(["src/butlers/storage/__init__.py"])
        assert result == ["tests/test_blob_storage.py"]


# ---------------------------------------------------------------------------
# Roster mapping
# ---------------------------------------------------------------------------


class TestRosterMapping:
    def test_roster_module_change(self):
        result = resolve_test_paths(["roster/finance/modules/spending.py"])
        assert "roster/finance/tests/" in result

    def test_roster_migration_includes_config_tests(self):
        result = resolve_test_paths(["roster/switchboard/migrations/001_init.py"])
        assert "roster/switchboard/tests/" in result
        assert "tests/config/" in result

    def test_roster_api_change(self):
        result = resolve_test_paths(["roster/education/api/router.py"])
        assert result == ["roster/education/tests/"]

    def test_roster_tools_change(self):
        result = resolve_test_paths(["roster/travel/tools/booking.py"])
        assert result == ["roster/travel/tests/"]

    def test_roster_butler_toml(self):
        result = resolve_test_paths(["roster/health/butler.toml"])
        assert result == ["roster/health/tests/"]

    def test_roster_top_level_ignored(self):
        """roster/ alone (no butler subdir) should not crash."""
        result = resolve_test_paths(["roster/shared/skills/consolidate/SKILL.md"])
        assert result == ["roster/shared/tests/"]


# ---------------------------------------------------------------------------
# Alembic migrations
# ---------------------------------------------------------------------------


class TestMigrationMapping:
    def test_alembic_migration(self):
        result = resolve_test_paths(["alembic/versions/abc123_add_table.py"])
        assert "tests/migrations/" in result
        assert "tests/config/" in result


# ---------------------------------------------------------------------------
# Non-Python / non-testable paths
# ---------------------------------------------------------------------------


class TestNonTestable:
    def test_frontend_no_tests(self):
        assert resolve_test_paths(["frontend/src/App.tsx"]) == []

    def test_docker_no_tests(self):
        assert resolve_test_paths(["docker/Dockerfile.dev"]) == []

    def test_dockerfile_no_tests(self):
        assert resolve_test_paths(["Dockerfile"]) == []

    def test_docs_no_tests(self):
        assert resolve_test_paths(["docs/QUICKSTART.md"]) == []

    def test_grafana_no_tests(self):
        assert resolve_test_paths(["grafana/dashboard.json"]) == []

    def test_makefile_no_tests(self):
        assert resolve_test_paths(["Makefile"]) == []

    def test_readme_no_tests(self):
        assert resolve_test_paths(["README.md"]) == []

    def test_openspec_no_tests(self):
        assert resolve_test_paths(["openspec/main/features.yaml"]) == []


# ---------------------------------------------------------------------------
# Test file passthrough
# ---------------------------------------------------------------------------


class TestFilePassthrough:
    def test_changed_test_file_included(self):
        result = resolve_test_paths(["tests/api/test_app.py"])
        assert result == ["tests/api/test_app.py"]

    def test_test_conftest_includes_parent_dir(self):
        result = resolve_test_paths(["tests/api/conftest.py"])
        assert result == ["tests/api/"]

    def test_multiple_test_files(self):
        result = resolve_test_paths(["tests/api/test_app.py", "tests/core/test_audit.py"])
        assert result == ["tests/api/test_app.py", "tests/core/test_audit.py"]


# ---------------------------------------------------------------------------
# Multiple changed files / deduplication
# ---------------------------------------------------------------------------


class TestMultipleFiles:
    def test_deduplication(self):
        result = resolve_test_paths(
            [
                "src/butlers/api/routers/search.py",
                "src/butlers/api/routers/memory.py",
            ]
        )
        assert result == ["tests/api/"]

    def test_mixed_source_and_test(self):
        result = resolve_test_paths(
            [
                "src/butlers/core/scheduler.py",
                "tests/core/test_core_scheduler.py",
            ]
        )
        assert "tests/core/" in result
        assert "tests/daemon/" in result
        assert "tests/core/test_core_scheduler.py" in result

    def test_mixed_source_and_non_testable(self):
        result = resolve_test_paths(
            [
                "src/butlers/api/routers/search.py",
                "frontend/src/App.tsx",
            ]
        )
        assert result == ["tests/api/"]

    def test_full_suite_overrides_all(self):
        result = resolve_test_paths(
            [
                "conftest.py",
                "src/butlers/api/routers/search.py",
                "frontend/src/App.tsx",
            ]
        )
        assert result == FULL_SUITE

    def test_multiple_modules(self):
        result = resolve_test_paths(
            [
                "src/butlers/modules/memory/tools/search.py",
                "src/butlers/api/routers/memory.py",
            ]
        )
        assert "tests/modules/memory/" in result
        assert "tests/api/" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_input(self):
        assert resolve_test_paths([]) == []

    def test_leading_dot_slash_stripped(self):
        result = resolve_test_paths(["./src/butlers/api/app.py"])
        assert result == ["tests/api/"]

    def test_unknown_src_butlers_file_triggers_full_suite(self):
        """Unrecognised files under src/butlers/ -> full suite (safety net)."""
        result = resolve_test_paths(["src/butlers/brand_new_file.py"])
        assert result == FULL_SUITE

    def test_deterministic_output(self):
        """Same input always produces same output."""
        files = [
            "src/butlers/core/scheduler.py",
            "src/butlers/api/routers/search.py",
            "roster/finance/modules/spending.py",
        ]
        result1 = resolve_test_paths(files)
        result2 = resolve_test_paths(list(reversed(files)))
        assert result1 == result2

    def test_scripts(self):
        result = resolve_test_paths(["scripts/staging.py"])
        assert result == ["tests/scripts/"]
