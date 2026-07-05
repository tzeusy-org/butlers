"""Tests for scripts/combine_review_comment_sources.py.

Covers the CI-glue script that flattens raw `gh api ... --paginate` dumps
(review comments, issue comments, PR reviews) into the flat
`[{"source":..., "body":...}]` shape session_link_guard.py's
`--review-comments-file` expects — including the concatenated-JSON-array
shape `--paginate` produces across multiple pages, and the best-effort
handling of a missing/empty/malformed input file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import combine_review_comment_sources as crcs  # noqa: E402

pytestmark = pytest.mark.unit


def test_combine_flattens_a_single_page_array(tmp_path: Path) -> None:
    path = tmp_path / "page.json"
    path.write_text(json.dumps([{"id": 1, "body": "hello"}, {"id": 2, "body": "world"}]))

    combined = crcs.combine([("review-comment", path)])

    assert combined == [
        {"source": "review-comment-1", "body": "hello"},
        {"source": "review-comment-2", "body": "world"},
    ]


def test_combine_flattens_multiple_concatenated_pages(tmp_path: Path) -> None:
    # `gh api --paginate` (without --jq) concatenates one JSON array per page
    # back-to-back rather than emitting one well-formed document.
    path = tmp_path / "pages.json"
    path.write_text(json.dumps([{"id": 1, "body": "a"}]) + json.dumps([{"id": 2, "body": "b"}]))

    combined = crcs.combine([("issue-comment", path)])

    assert combined == [
        {"source": "issue-comment-1", "body": "a"},
        {"source": "issue-comment-2", "body": "b"},
    ]


def test_combine_merges_multiple_labeled_sources(tmp_path: Path) -> None:
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps([{"id": 9, "body": "lgtm"}]))
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([{"id": 3, "body": "nit"}]))

    combined = crcs.combine([("review", reviews), ("review-comment", comments)])

    assert combined == [
        {"source": "review-9", "body": "lgtm"},
        {"source": "review-comment-3", "body": "nit"},
    ]


def test_combine_treats_missing_file_as_empty(tmp_path: Path) -> None:
    combined = crcs.combine([("review-comment", tmp_path / "does-not-exist.json")])
    assert combined == []


def test_combine_treats_malformed_json_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    combined = crcs.combine([("review-comment", path)])
    assert combined == []


def test_combine_defaults_missing_body_to_empty_string(tmp_path: Path) -> None:
    path = tmp_path / "no-body.json"
    path.write_text(json.dumps([{"id": 5}]))
    combined = crcs.combine([("review", path)])
    assert combined == [{"source": "review-5", "body": ""}]


def test_main_writes_combined_output_file(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    src.write_text(json.dumps([{"id": 1, "body": "x"}]))
    out = tmp_path / "out.json"

    exit_code = crcs.main(["--input", "review-comment", str(src), "--output", str(out)])

    assert exit_code == 0
    assert json.loads(out.read_text()) == [{"source": "review-comment-1", "body": "x"}]
