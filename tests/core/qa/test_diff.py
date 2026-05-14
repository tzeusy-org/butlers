from __future__ import annotations

import pytest

from butlers.core.qa.diff import parse_unified_diff

pytestmark = pytest.mark.unit


def test_parse_representative_unified_diff():
    parsed = parse_unified_diff(
        """\
diff --git a/src/example.py b/src/example.py
index 1111111..2222222 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1,3 +1,3 @@
 unchanged = True
-old_value = 1
+new_value = 2
"""
    )

    assert [line.model_dump(mode="json") for line in parsed] == [
        {"kind": "meta", "text": "diff --git a/src/example.py b/src/example.py"},
        {"kind": "meta", "text": "index 1111111..2222222 100644"},
        {"kind": "meta", "text": "--- a/src/example.py"},
        {"kind": "meta", "text": "+++ b/src/example.py"},
        {"kind": "meta", "text": "@@ -1,3 +1,3 @@"},
        {"kind": " ", "text": "unchanged = True"},
        {"kind": "-", "text": "old_value = 1"},
        {"kind": "+", "text": "new_value = 2"},
    ]


def test_parse_truncates_over_limit_with_meta_marker():
    parsed = parse_unified_diff("\n".join(f"+line {i}" for i in range(5)), max_lines=3)

    assert [line.model_dump(mode="json") for line in parsed] == [
        {"kind": "+", "text": "line 0"},
        {"kind": "+", "text": "line 1"},
        {"kind": "+", "text": "line 2"},
        {"kind": "meta", "text": "... (truncated, 2 more lines)"},
    ]


def test_parse_empty_input():
    assert parse_unified_diff("") == []


def test_parse_binary_diff_markers_as_meta():
    parsed = parse_unified_diff(
        """\
diff --git a/icon.png b/icon.png
index 1111111..2222222 100644
Binary files a/icon.png and b/icon.png differ
"""
    )

    assert [line.model_dump(mode="json") for line in parsed] == [
        {"kind": "meta", "text": "diff --git a/icon.png b/icon.png"},
        {"kind": "meta", "text": "index 1111111..2222222 100644"},
        {"kind": "meta", "text": "Binary files a/icon.png and b/icon.png differ"},
    ]


def test_parse_rejects_negative_limit():
    with pytest.raises(ValueError, match="max_lines"):
        parse_unified_diff("+line", max_lines=-1)
