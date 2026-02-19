"""Tests for module-first memory config parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import ButlerConfig, load_config

pytestmark = pytest.mark.unit


def _write_toml(tmp_path: Path, content: str) -> Path:
    (tmp_path / "butler.toml").write_text(content)
    return tmp_path


def test_butler_config_no_longer_has_memory_field() -> None:
    cfg = ButlerConfig(name="test", port=9000)
    assert not hasattr(cfg, "memory")


def test_load_config_parses_modules_memory_section(tmp_path: Path) -> None:
    toml = """\
[butler]
name = "membot"
port = 40200

[modules.memory]

[modules.memory.retrieval]
context_token_budget = 5000
default_limit = 50
default_mode = "semantic"

[modules.memory.retrieval.score_weights]
relevance = 0.5
importance = 0.2
recency = 0.2
confidence = 0.1
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert "memory" in cfg.modules
    retrieval = cfg.modules["memory"]["retrieval"]
    assert retrieval["context_token_budget"] == 5000
    assert retrieval["default_limit"] == 50
    assert retrieval["default_mode"] == "semantic"
    assert retrieval["score_weights"] == {
        "relevance": 0.5,
        "importance": 0.2,
        "recency": 0.2,
        "confidence": 0.1,
    }


def test_load_config_without_modules_memory_keeps_module_disabled(tmp_path: Path) -> None:
    toml = """\
[butler]
name = "nomembot"
port = 40201
"""
    cfg = load_config(_write_toml(tmp_path, toml))
    assert "memory" not in cfg.modules


def test_legacy_butler_memory_section_is_not_used(tmp_path: Path) -> None:
    toml = """\
[butler]
name = "legacy"
port = 8202

[butler.memory]
enabled = true
port = 8150
"""
    cfg = load_config(_write_toml(tmp_path, toml))
    assert "memory" not in cfg.modules
    assert not hasattr(cfg, "memory")
