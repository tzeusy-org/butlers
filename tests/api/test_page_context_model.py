"""Tests for the typed `PageContext`/`VisibleResource` request model (bu-0ynlk.4).

Covers the redaction/size-budget validators that make page context a safe
untrusted-display-data payload (`about/heart-and-soul/security.md`): secret-ish
query keys are stripped server-side even if a client forgot to, an oversize
payload is truncated (never silently dropped) with `truncated=True`, and
`visible_resource.kind` is validated against the closed registry vocabulary.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from butlers.api.models.conversation import PageContext, VisibleResource

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# visible_resource.kind vocabulary
# ---------------------------------------------------------------------------


def test_visible_resource_accepts_a_known_kind():
    resource = VisibleResource(kind="session", id="sess-1")
    assert resource.kind == "session"
    assert resource.id == "sess-1"


def test_visible_resource_rejects_an_unknown_kind():
    with pytest.raises(ValidationError, match="visible_resource.kind must be one of"):
        VisibleResource(kind="not-a-real-kind")


def test_page_context_rejects_an_unknown_visible_resource_kind_via_nested_validation():
    with pytest.raises(ValidationError):
        PageContext(route="/spend", visible_resource={"kind": "bogus"})


# ---------------------------------------------------------------------------
# Secret-ish query-param redaction (server-side backstop)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["token", "api_key", "Secret", "PASSWORD", "authorization", "auth_token"],
)
def test_page_context_strips_secret_ish_query_keys(key):
    context = PageContext(route="/settings", query_params={key: "leak-me", "safe": "1"})
    assert key not in context.query_params
    assert context.query_params == {"safe": "1"}


def test_page_context_keeps_ordinary_query_keys():
    context = PageContext(route="/entities/concentration", query_params={"predicate": "child-of"})
    assert context.query_params == {"predicate": "child-of"}


# ---------------------------------------------------------------------------
# Size budget + truncation
# ---------------------------------------------------------------------------


def test_page_context_under_budget_is_left_untouched():
    context = PageContext(route="/spend", query_params={"window": "week"})
    assert context.truncated is False
    assert context.query_params == {"window": "week"}


def test_page_context_over_budget_truncates_filters_first_and_flags_truncated():
    huge_filters = {f"key_{i}": "x" * 50 for i in range(60)}
    context = PageContext(
        route="/entities/concentration",
        visible_resource=VisibleResource(kind="concentration", filters=huge_filters),
    )
    assert context.truncated is True
    assert context.visible_resource is not None
    assert context.visible_resource.filters is None


def test_page_context_over_budget_falls_back_to_dropping_query_params():
    huge_query_params = {f"q{i}": "y" * 50 for i in range(60)}
    context = PageContext(route="/entities/concentration", query_params=huge_query_params)
    assert context.truncated is True
    assert context.query_params == {}


def test_page_context_over_budget_truncates_visible_summary_last():
    context = PageContext(
        route="/spend",
        visible_resource=VisibleResource(
            kind="spend_window", filters={f"k{i}": "z" * 40 for i in range(50)}
        ),
        visible_summary="s" * 150,
    )
    assert context.truncated is True
    # filters dropped first; if that alone doesn't fit, visible_summary is cut too.
    assert context.visible_resource is not None
    assert context.visible_resource.filters is None
    assert len(context.visible_summary or "") <= 150
