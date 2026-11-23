"""Catalog-driven exact-call previews (bu-jv1moa.26 PR3).

A tool that declares ``content_params`` gets its preview rebuilt from the descriptor's
``recipient_params`` plus ``content_params``; every other tool keeps the S1 registry. Anything the
catalog cannot show completely stays Open only.
"""

from __future__ import annotations

import pytest

from butlers.modules.approvals.previews import (
    args_digest,
    dashboard_call_preview,
    resolve_call_preview,
)

pytestmark = pytest.mark.unit

EDIT = {"chat_id": "-100123", "message_id": 9, "text": "Moved to 5pm"}
WHATSAPP = {"chat_jid": "123@s.whatsapp.net", "message_id": "ABC", "text": "See you at 5"}


def test_telegram_edit_is_rebuilt_from_recipient_and_content_params() -> None:
    preview = resolve_call_preview("telegram_edit_message_text", EDIT)
    assert preview is not None
    shown = {f.label: f.value for f in preview.facts}
    # Every stored argument value is shown, nothing else is.
    assert sorted(shown.values()) == sorted(["-100123", "9", "Moved to 5pm"])
    assert list(shown)[-1] == "Message"  # content last, recipients first


def test_whatsapp_reply_is_rebuilt_the_same_way() -> None:
    preview = resolve_call_preview("whatsapp_reply_to_message", WHATSAPP)
    assert preview is not None
    assert sorted(f.value for f in preview.facts) == sorted(
        ["123@s.whatsapp.net", "ABC", "See you at 5"]
    )


@pytest.mark.parametrize(
    "args",
    [
        {**EDIT, "parse_mode": "HTML"},  # an argument the card would not show
        {k: v for k, v in EDIT.items() if k != "text"},  # missing content
        {**EDIT, "text": ""},
        {**EDIT, "text": ["a"]},
        {**EDIT, "text": None},
        {k: v for k, v in EDIT.items() if k != "chat_id"},  # missing recipient
        {**EDIT, "chat_id": ""},
        {**EDIT, "message_id": True},
        {**EDIT, "message_id": 0},
    ],
    ids=[
        "unshown-arg",
        "no-content",
        "empty-content",
        "list-content",
        "null-content",
        "no-recipient",
        "blank-recipient",
        "bool-message-id",
        "zero-message-id",
    ],
)
def test_anything_not_shown_exactly_stays_open_only(args) -> None:
    assert resolve_call_preview("telegram_edit_message_text", args) is None
    assert dashboard_call_preview("telegram_edit_message_text", args) is None


def test_the_model_written_dossier_keys_are_ignored_not_shown() -> None:
    args = {**EDIT, "_why": "because", "_blast_radius": "x"}
    preview = resolve_call_preview("telegram_edit_message_text", args)
    assert preview is not None
    assert "because" not in {f.value for f in preview.facts}


def test_oversize_content_is_open_only_on_the_dashboard() -> None:
    args = {**EDIT, "text": "x" * 20_000}
    assert dashboard_call_preview("telegram_edit_message_text", args) is None


def test_a_changed_call_changes_the_bound_digest_and_the_shown_text() -> None:
    first = dashboard_call_preview("telegram_edit_message_text", EDIT)
    edited = {**EDIT, "text": "Cancelled"}
    second = dashboard_call_preview("telegram_edit_message_text", edited)
    assert first and second
    assert first["displayed_call"]["args_digest"] == args_digest(EDIT)
    assert second["displayed_call"]["args_digest"] == args_digest(edited)
    assert first["displayed_call"]["args_digest"] != second["displayed_call"]["args_digest"]
    assert "Cancelled" in {f["value"] for f in second["facts"]}


def test_an_unknown_tool_or_one_without_content_params_has_no_catalog_preview() -> None:
    assert resolve_call_preview("not_a_tool", {"text": "x"}) is None
    # calendar_create_event is classified but declares no content_params and has no S1 renderer.
    assert resolve_call_preview("calendar_create_event", {"title": "x", "attendees": ["a"]}) is None


def test_s1_registry_tools_keep_their_renderer() -> None:
    preview = resolve_call_preview("telegram_send_message", {"chat_id": "1", "text": "hi"})
    assert preview is not None and preview.title == "Send Telegram message"


def test_a_non_communicate_tool_is_never_previewed_from_the_catalog(monkeypatch) -> None:
    from dataclasses import replace

    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME
    from butlers.modules.approvals import previews

    risky = replace(
        TOOL_PRESENTATION_BY_NAME["telegram_edit_message_text"], effect_class="administer"
    )
    monkeypatch.setattr(previews, "_catalog_descriptor", lambda name: risky)
    assert resolve_call_preview("telegram_edit_message_text", EDIT) is None
