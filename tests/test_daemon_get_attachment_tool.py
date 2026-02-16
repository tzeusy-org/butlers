"""Integration test for get_attachment tool registration in daemon."""

from butlers.daemon import CORE_TOOL_NAMES


def test_get_attachment_in_core_tools():
    """Verify get_attachment is registered as a core tool."""
    assert "get_attachment" in CORE_TOOL_NAMES


# E2E tests requiring test_butler_daemon fixture are deferred until
# daemon test infrastructure is available. Core functionality is
# covered by unit tests in tests/tools/test_attachments.py.
