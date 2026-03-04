"""Tests for OpenCodeAdapter — parse_system_prompt_file() and build_config_file().

Covers:
- parse_system_prompt_file(): reads OPENCODE.md, falls back to AGENTS.md,
  returns empty string when neither file exists.
- build_config_file(): writes valid JSONC with mcp key and remote server entries,
  skips invalid server configs with warnings.
- Adapter registration and create_worker().
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from butlers.core.runtimes import get_adapter
from butlers.core.runtimes.opencode import OpenCodeAdapter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registration and basic adapter tests
# ---------------------------------------------------------------------------


def test_opencode_adapter_registered():
    """get_adapter('opencode') returns OpenCodeAdapter."""
    assert get_adapter("opencode") is OpenCodeAdapter


def test_opencode_adapter_instantiates():
    """OpenCodeAdapter can be instantiated without arguments."""
    adapter = OpenCodeAdapter()
    assert adapter is not None


def test_opencode_adapter_with_custom_binary():
    """OpenCodeAdapter accepts a custom binary path."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/local/bin/opencode")
    assert adapter._opencode_binary == "/usr/local/bin/opencode"


def test_opencode_adapter_binary_name():
    """binary_name property returns 'opencode'."""
    adapter = OpenCodeAdapter()
    assert adapter.binary_name == "opencode"


def test_opencode_adapter_create_worker_preserves_binary():
    """create_worker() returns a distinct adapter with the same binary config."""
    adapter = OpenCodeAdapter(opencode_binary="/usr/local/bin/opencode")
    worker = adapter.create_worker()

    assert worker is not adapter
    assert isinstance(worker, OpenCodeAdapter)
    assert worker._opencode_binary == "/usr/local/bin/opencode"


def test_opencode_adapter_create_worker_no_binary():
    """create_worker() preserves None binary path."""
    adapter = OpenCodeAdapter()
    worker = adapter.create_worker()
    assert worker._opencode_binary is None


# ---------------------------------------------------------------------------
# parse_system_prompt_file tests
# ---------------------------------------------------------------------------


def test_parse_system_prompt_reads_opencode_md(tmp_path: Path):
    """OpenCodeAdapter prefers OPENCODE.md for system prompt."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("You are an OpenCode butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are an OpenCode butler."


def test_parse_system_prompt_falls_back_to_agents_md(tmp_path: Path):
    """Falls back to AGENTS.md when OPENCODE.md is missing."""
    adapter = OpenCodeAdapter()
    (tmp_path / "AGENTS.md").write_text("You are an agent butler.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "You are an agent butler."


def test_parse_system_prompt_prefers_opencode_over_agents(tmp_path: Path):
    """OPENCODE.md takes priority over AGENTS.md."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("OpenCode instructions.")
    (tmp_path / "AGENTS.md").write_text("Agent instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "OpenCode instructions."


def test_parse_system_prompt_missing_all(tmp_path: Path):
    """Returns empty string when no prompt files exist."""
    adapter = OpenCodeAdapter()
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_empty_opencode_md_falls_back(tmp_path: Path):
    """Falls back to AGENTS.md when OPENCODE.md is empty (whitespace only)."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("   \n  ")
    (tmp_path / "AGENTS.md").write_text("Agent fallback.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Agent fallback."


def test_parse_system_prompt_both_empty(tmp_path: Path):
    """Returns empty string when both OPENCODE.md and AGENTS.md are empty."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("   \n  ")
    (tmp_path / "AGENTS.md").write_text("  ")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_ignores_claude_md(tmp_path: Path):
    """CLAUDE.md is not used by OpenCodeAdapter."""
    adapter = OpenCodeAdapter()
    (tmp_path / "CLAUDE.md").write_text("This is Claude instructions.")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == ""


def test_parse_system_prompt_opencode_md_with_leading_trailing_whitespace(tmp_path: Path):
    """OPENCODE.md content is stripped of surrounding whitespace."""
    adapter = OpenCodeAdapter()
    (tmp_path / "OPENCODE.md").write_text("  Instructions here.  \n")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Instructions here."


def test_parse_system_prompt_agents_md_with_leading_trailing_whitespace(tmp_path: Path):
    """AGENTS.md content is stripped of surrounding whitespace."""
    adapter = OpenCodeAdapter()
    (tmp_path / "AGENTS.md").write_text("\n  Agent instructions.  \n")
    prompt = adapter.parse_system_prompt_file(config_dir=tmp_path)
    assert prompt == "Agent instructions."


# ---------------------------------------------------------------------------
# build_config_file tests
# ---------------------------------------------------------------------------


def test_build_config_file_writes_opencode_jsonc(tmp_path: Path):
    """build_config_file() writes opencode.jsonc with mcp key."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    assert config_path == tmp_path / "opencode.jsonc"
    assert config_path.exists()


def test_build_config_file_remote_server_entry(tmp_path: Path):
    """build_config_file() maps servers to remote type entries."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert "mcp" in data
    entry = data["mcp"]["my-butler"]
    assert entry["type"] == "remote"
    assert entry["url"] == "http://localhost:9100/mcp"
    assert entry["enabled"] is True


def test_build_config_file_includes_permission_key(tmp_path: Path):
    """build_config_file() includes empty permission object for auto-mode."""
    adapter = OpenCodeAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert "permission" in data
    assert data["permission"] == {}


def test_build_config_file_empty_servers(tmp_path: Path):
    """build_config_file() writes an empty mcp section when no servers provided."""
    adapter = OpenCodeAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcp"] == {}


def test_build_config_file_multiple_servers(tmp_path: Path):
    """build_config_file() writes all valid MCP servers."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "butler-a": {"url": "http://localhost:9100/mcp"},
        "butler-b": {"url": "http://localhost:9200/mcp"},
    }
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert len(data["mcp"]) == 2
    assert "butler-a" in data["mcp"]
    assert "butler-b" in data["mcp"]
    assert data["mcp"]["butler-a"]["url"] == "http://localhost:9100/mcp"
    assert data["mcp"]["butler-b"]["url"] == "http://localhost:9200/mcp"


def test_build_config_file_skips_non_dict_server(tmp_path: Path, caplog):
    """build_config_file() skips servers with non-dict config and logs warning."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "valid-server": {"url": "http://localhost:9100/mcp"},
        "bad-server": "not-a-dict",  # type: ignore[dict-item]
    }
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    data = json.loads(config_path.read_text())
    assert "valid-server" in data["mcp"]
    assert "bad-server" not in data["mcp"]
    assert "bad-server" in caplog.text


def test_build_config_file_skips_server_without_url(tmp_path: Path, caplog):
    """build_config_file() skips servers missing a url key and logs warning."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "valid-server": {"url": "http://localhost:9100/mcp"},
        "no-url-server": {"transport": "remote"},
    }
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    data = json.loads(config_path.read_text())
    assert "valid-server" in data["mcp"]
    assert "no-url-server" not in data["mcp"]
    assert "no-url-server" in caplog.text


def test_build_config_file_skips_server_with_empty_url(tmp_path: Path, caplog):
    """build_config_file() skips servers with empty url string."""
    adapter = OpenCodeAdapter()
    mcp_servers = {
        "empty-url-server": {"url": "   "},
    }
    with caplog.at_level(logging.WARNING):
        config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)

    data = json.loads(config_path.read_text())
    assert "empty-url-server" not in data["mcp"]


def test_build_config_file_url_is_stripped(tmp_path: Path):
    """build_config_file() strips whitespace from server URLs."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"my-butler": {"url": "  http://localhost:9100/mcp  "}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    data = json.loads(config_path.read_text())
    assert data["mcp"]["my-butler"]["url"] == "http://localhost:9100/mcp"


def test_build_config_file_is_valid_json(tmp_path: Path):
    """build_config_file() writes valid JSON (JSONC with no comments for now)."""
    adapter = OpenCodeAdapter()
    mcp_servers = {"butler": {"url": "http://localhost:9100/mcp"}}
    config_path = adapter.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_path)
    # Must parse as valid JSON
    data = json.loads(config_path.read_text())
    assert isinstance(data, dict)
