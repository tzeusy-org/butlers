"""No registered MCP tool takes a caller-named local filesystem path (bu-4bgsty).

A runtime session's arguments are untrusted. A tool that opens whatever path the
caller names can read any file the daemon can, which is how finance's old
``import_transactions_from_file`` leaked (bu-o3bt53). Bytes enter a butler by
reference instead: ``storage_ref`` (RFC 0016 blob prefix), a provider id such as
``file_id``, or inline content.

The guard reads each tool's advertised JSON schema, so it sees exactly what a
model sees, across every module plus the core groups. It flags a parameter when
its name ends in a path word (``path``, ``file_path``, ``filename``, ``dir``,
...) or its schema declares a pydantic path format (``pathlib.Path``,
``FilePath``, ``DirectoryPath``), at any nesting depth.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.contracts.test_tool_descriptions import _core_tools, _module_tools

pytestmark = pytest.mark.contract

# Matched against the last ``_``-separated token, so ``source_path`` and
# ``filepath`` trip it while ``file_id``, ``folder_id``, ``direction`` and
# ``storage_ref`` do not.
_PATH_WORDS = frozenset(
    {
        "path",
        "paths",
        "filepath",
        "filepaths",
        "pathname",
        "filename",
        "filenames",
        "file",
        "files",
        "dir",
        "dirs",
        "directory",
        "directories",
    }
)
# Formats pydantic emits for pathlib.Path / NewPath, FilePath, and DirectoryPath.
_PATH_FORMATS = frozenset({"path", "file-path", "directory-path"})


def _names_a_path(param: str) -> bool:
    return param.lower().rsplit("_", 1)[-1] in _PATH_WORDS


def _declares_path_format(schema: Any) -> bool:
    if isinstance(schema, dict):
        if schema.get("format") in _PATH_FORMATS:
            return True
        return any(_declares_path_format(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_declares_path_format(item) for item in schema)
    return False


def _path_params(parameters: dict[str, Any]) -> list[str]:
    properties = parameters.get("properties") or {}
    return [
        name
        for name, schema in properties.items()
        if _names_a_path(name) or _declares_path_format(schema)
    ]


@pytest.mark.parametrize(
    ("param", "flagged"),
    [
        ("path", True),
        ("file_path", True),
        ("source_dir", True),
        ("filename", True),
        ("storage_ref", False),
        ("file_id", False),
        ("direction", False),
    ],
)
def test_path_word_matches_the_last_name_token(param: str, flagged: bool) -> None:
    assert _names_a_path(param) is flagged


async def test_no_registered_tool_accepts_a_local_filesystem_path() -> None:
    tools = await _module_tools()
    for butler_name in ("general", "switchboard", "messenger", "chronicler"):
        tools.extend(await _core_tools(butler_name))

    assert len(tools) > 100, "registration produced too few tools to be a real sweep"
    offenders = sorted(
        f"{tool.name}({param})" for tool in tools for param in _path_params(tool.parameters)
    )
    assert offenders == [], (
        "Tools must not open caller-named local paths; take storage_ref, a provider id, "
        f"or inline content instead: {offenders}"
    )
