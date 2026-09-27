"""Parity gate for the language-neutral citation and shell route contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

pytestmark = pytest.mark.contract

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "src/butlers/core/citation_routes.json"
_SHELL = _ROOT / "frontend/src/lib/shell-capability.ts"
_SHELL_PATH = re.compile(r'\{\s*path:\s*"([^"]+)"')


def test_citation_routes_match_shell_capabilities_and_literal_query_contracts() -> None:
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    routes = manifest["routes"]
    by_path = {route["path"]: route for route in routes}
    assert len(by_path) == len(routes)

    shell_targets = _SHELL_PATH.findall(_SHELL.read_text(encoding="utf-8"))
    assert shell_targets
    shell_paths = {urlsplit(target).path for target in shell_targets}
    assert set(by_path) == shell_paths

    for target in shell_targets:
        parsed = urlsplit(target)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if not query:
            continue
        rules = by_path[parsed.path].get("query", {})
        assert set(query) <= set(rules)
        for key, value in query.items():
            assert value in rules[key].get("values", [])

    for route in routes:
        parameter_names = {
            segment[1:] for segment in route["path"].split("/") if segment.startswith(":")
        }
        assert set(route.get("parameters", {})) == parameter_names
