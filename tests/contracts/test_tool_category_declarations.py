"""Every catalog tool declares its S1 data categories, cited to its domain spec.

REQ-capability-authorization-019 / RFC 0038 S1: the descriptor carries
``categories``, a set of the six names, and missing metadata is ungrantable
(bu-dhloi1.27). The checked-in inventory is the declaration; these tests pin
that nothing reaches the catalog undeclared and that each declaration names
the spec it was read from.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from butlers.core.goal_labels import CATEGORIES
from butlers.core.tool_catalog import ToolCatalogError, validate_authorization_catalog
from butlers.core.tool_presentation_inventory import (
    ALL_CATEGORIES,
    CATEGORY_SPECS,
    NARROWED_CATEGORIES,
    STORE_WRITERS,
    TOOL_PRESENTATION_BY_NAME,
    TOOL_PRESENTATION_INVENTORY,
    _declare,
    category_spec,
)

REPO = Path(__file__).resolve().parents[2]


def test_every_inventory_declaration_names_a_subset_of_the_six_categories():
    for declaration in TOOL_PRESENTATION_INVENTORY:
        categories = declaration.categories
        assert isinstance(categories, tuple), declaration.canonical_name
        assert set(categories) <= CATEGORIES, declaration.canonical_name
        assert list(categories) == sorted(set(categories)), declaration.canonical_name


def test_every_declaration_cites_an_existing_domain_spec():
    for declaration in TOOL_PRESENTATION_INVENTORY:
        specs = category_spec(declaration.module_name, declaration.group_name)
        assert specs, declaration.canonical_name
        for spec in specs:
            assert (REPO / spec).is_file(), (declaration.canonical_name, spec)
    for key, specs in CATEGORY_SPECS.items():
        assert specs and all((REPO / spec).is_file() for spec in specs), key


def test_a_declaration_without_categories_does_not_build():
    with pytest.raises(TypeError, match="categories"):
        _declare("core", "state", "state_get", effect_class="read")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="categories"):
        _declare("core", "state", "state_get", categories=("horoscope",))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Free text or cross-domain content can carry any category.
        ("email_read_message", ALL_CATEGORIES),
        ("state_get", ALL_CATEGORIES),
        ("calendar_list_events", ALL_CATEGORIES),
        ("memory_search", ALL_CATEGORIES),
        ("note_list", ALL_CATEGORIES),
        ("ha_get_entity_state", ALL_CATEGORIES),
    ],
)
def test_declared_categories_follow_the_domain_data(name, expected):
    assert TOOL_PRESENTATION_BY_NAME[name].categories == expected


async def test_the_catalog_gate_refuses_a_presentable_tool_without_categories():
    from tests.contracts.test_tool_presentation_inventory import _core_catalog

    catalog = await _core_catalog("general", ("state", "notifications"))
    reader = catalog["state_get"]
    assert reader.categories == ALL_CATEGORIES and reader.grantable
    missing = replace(reader, categories=None)
    assert not missing.authorization_complete and not missing.grantable
    changed = replace(
        catalog,
        descriptors=tuple(
            missing if item.canonical_name == "state_get" else item for item in catalog.descriptors
        ),
    )
    with pytest.raises(ToolCatalogError, match="state_get.*categories"):
        validate_authorization_catalog(changed, store_writers=STORE_WRITERS)


# Every declaration narrower than all six, pinned. Widening a tool's output (a new
# notes field, a raw provider payload) must come back here and to
# NARROWED_CATEGORIES; anything not listed must declare all six.
NARROWED = {
    "learn_merchant_categories": ("finance",),
    "measurement_delete": ("health", "routine"),
    "meal_delete": ("health", "routine"),
    "research_delete": ("health", "routine"),
    "symptom_delete": ("health", "routine"),
    "nutrition_summary": ("health", "routine"),
    "health_hr_history": ("health", "routine"),
    "health_hrv_history": ("health", "routine"),
    "health_spo2_history": ("health", "routine"),
    "health_breathing_rate_history": ("health", "routine"),
    "health_activity_summary": ("health", "routine"),
    "health_vo2_max_latest": ("health", "routine"),
    "taste_add_verdict": ("routine",),
    "taste_backfill_ledger": ("routine",),
    "taste_get_summary": ("routine",),
    "analytics_get_snapshot": ("routine",),
    "analytics_get_trend": ("routine",),
    "curriculum_replan": (),
    "diagnostic_complete": (),
    "mastery_get_map_summary": (),
    "mind_map_create": (),
    "mind_map_edge_delete": (),
    "mind_map_node_create": (),
    "spaced_repetition_record_response": ("routine",),
    "spaced_repetition_schedule_cleanup": (),
    "teaching_flow_abandon": (),
    "teaching_flow_start": ("routine",),
    "spotify_play": (),
    "spotify_pause": (),
    "spotify_skip_next": (),
    "spotify_skip_previous": (),
    "spotify_seek": (),
    "spotify_set_volume": (),
    "spotify_save_tracks": (),
    "spotify_remove_saved_tracks": (),
    "steam_get_player_level": (),
    "steam_get_current_players": (),
    "steam_resolve_vanity_url": (),
    "module.states": (),
    "list_butlers": (),
    "pending_action_count": (),
}


def test_the_narrowed_declarations_are_exactly_the_pinned_set():
    actual = {
        item.canonical_name: item.categories
        for item in TOOL_PRESENTATION_INVENTORY
        if item.categories != ALL_CATEGORIES
    }
    assert actual == NARROWED
    assert set(NARROWED_CATEGORIES) == set(NARROWED)
    assert all(reason.strip() for reason in NARROWED_CATEGORIES.values())


@pytest.mark.parametrize(
    "name",
    [
        # Free text in the output keeps all six (notes, descriptions, raw payloads).
        "list_transactions",
        "account_feed_freshness",
        "medication_list",
        "symptom_log",
        "health_sleep_history",
        "mind_map_get",
        "taste_get_work",
        "spotify_get_playlists",
        "spotify_get_playback_state",
        "steam_get_game_news",
        # Owner identity (personaname, realname) is free text too.
        "steam_get_player_summary",
        "steam_get_friend_list",
        # Category labels are owner free text; merchant, payee and service names
        # can name a pharmacy or clinic; tax flags select medical spend.
        "cash_flow",
        "compute_baselines",
        "detect_duplicates",
        "spending_forecast",
        "predict_bills",
        "budget_status",
        "list_distinct_merchants",
        "reconcile_feed_vs_email",
        "flag_tax_deductible",
        "suggest_categories",
        "detect_price_changes",
        "detect_recurring",
        "subscription_audit",
        # Work, game and achievement titles are third-party text.
        "taste_list_works",
        "steam_get_owned_games",
        "steam_get_recently_played",
        "steam_get_achievements",
        # Stored fact content prose, as in health_sleep_history.
        "health_sleep_latest",
    ],
)
def test_free_text_output_keeps_all_six(name):
    assert TOOL_PRESENTATION_BY_NAME[name].categories == ALL_CATEGORIES
