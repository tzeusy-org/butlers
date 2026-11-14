"""Importing the host loads no model, spawner or runtime module (RFC 0039 D14 item 1, partial).

PR 2 adds the full static import-ban contract over all three packages; this guard keeps the
host's own runtime import closure clean in the meantime.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

BANNED = (
    "butlers.core.spawner",
    "butlers.core.runtimes",
    "butlers.modules.memory.embedding",
    "anthropic",
    "openai",
    "google.genai",
    "claude_agent_sdk",
    "sentence_transformers",
    "transformers",
)


def test_the_host_import_closure_holds_no_model_path():
    code = (
        "import sys, butlers.capability_host as h; h.CapabilityHost; "
        f"print([m for m in sys.modules if m.startswith({BANNED!r})])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
