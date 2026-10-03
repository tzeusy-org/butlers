"""Checked-in presentation classification for canonical MCP tool names.

The inventory is representation metadata only. It never registers a handler or
changes FastMCP listing, invocation, approval, module-state, or caller authority.
"""

from __future__ import annotations

from types import MappingProxyType

from butlers.core.tool_catalog import ToolPresentation


def _declare(
    module: str,
    group: str,
    names: str,
    *,
    presentable: bool = True,
    posture: str = "deferred",
    namespace: str | None = None,
) -> tuple[ToolPresentation, ...]:
    return tuple(
        ToolPresentation(
            canonical_name=name,
            module_name=module,
            group_name=group,
            namespace=f"{module}.{namespace or group}",
            llm_presentable=presentable,
            load_posture=posture,  # type: ignore[arg-type]
        )
        for name in names.split()
    )


TOOL_PRESENTATION_INVENTORY = (
    # Core definitions. Direct registrations are kept distinct from group-gated definitions.
    *_declare(
        "core",
        "direct",
        "route.execute",
        presentable=False,
        posture="eager",
        namespace="routing",
    ),
    *_declare(
        "core",
        "direct",
        "cancel_session",
        presentable=False,
        posture="eager",
        namespace="sessions",
    ),
    *_declare(
        "core",
        "direct",
        "delivery_preferences_get delivery_preferences_set deferred_notification_cancel "
        "deferred_notifications_list scheduling_preferences_get scheduling_preferences_set",
        namespace="messenger_preferences",
    ),
    *_declare("core", "continuity", "carry_forward"),
    *_declare(
        "core",
        "cost_claims",
        "cost_claim_amend cost_claim_assert cost_claim_list_mine cost_claim_resolve "
        "cost_claim_retract",
    ),
    *_declare("core", "delegation", "delegate_ask delegate_answer"),
    *_declare(
        "core",
        "delegation",
        "delegate_receive delegate_wake",
        presentable=False,
        namespace="delegation_control",
    ),
    *_declare(
        "core",
        "domain_events",
        "list_my_subscriptions publish_event subscribe_to_event unsubscribe_from_event",
    ),
    *_declare(
        "core",
        "domain_events",
        "receive_domain_event report_event_reaction",
        presentable=False,
        namespace="domain_event_delivery",
    ),
    *_declare(
        "core",
        "fleet_cases",
        "close_case contribute_case_evidence find_open_case open_case propose_case_posture "
        "read_case record_case_link",
    ),
    *_declare("core", "graph", "entity_graph_path entity_graph_walk"),
    *_declare(
        "core",
        "infra",
        "chronicler_day_close_refresh shutdown status tick trigger",
        presentable=False,
        posture="eager",
        namespace="daemon_control",
    ),
    *_declare(
        "core",
        "infra",
        "conversation_recall conversation_thread_read correct memory_access memory_catalog_fetch",
        namespace="conversation",
    ),
    *_declare(
        "core",
        "infra",
        "conversation_reply",
        presentable=False,
        namespace="conversation_delivery",
    ),
    *_declare("core", "media", "attachment_view get_attachment"),
    *_declare("core", "module_mgmt", "module.states"),
    *_declare("core", "module_mgmt", "module.set_enabled", presentable=False),
    *_declare("core", "notifications", "notify remind"),
    *_declare(
        "core",
        "scheduling",
        "schedule_costs schedule_create schedule_delete schedule_list schedule_toggle "
        "schedule_trigger schedule_update",
    ),
    *_declare(
        "core",
        "sessions",
        "sessions_daily sessions_get sessions_list sessions_summary top_sessions",
    ),
    *_declare("core", "state", "state_delete state_get state_list state_set"),
    *_declare(
        "core",
        "switchboard_backfill",
        "backfill.poll backfill.progress",
        presentable=False,
        posture="eager",
    ),
    *_declare(
        "core",
        "switchboard_routing",
        "answer_question cannot_answer connector.heartbeat file_bug_report ingest route_to_butler",
        presentable=False,
        posture="eager",
    ),
    *_declare(
        "core",
        "temporal",
        "deadline_create deadline_delete deadline_list deadline_update event_chain_create "
        "event_chain_delete event_chain_list event_chain_update seasonal_period_create "
        "seasonal_period_create_preset seasonal_period_delete seasonal_period_list "
        "seasonal_period_update",
    ),
    # Built-in modules.
    *_declare(
        "approvals",
        "actions",
        "approve_action dispatch_approved_action expire_stale_actions list_executed_actions "
        "list_pending_actions pending_action_count reject_action show_pending_action",
    ),
    *_declare(
        "approvals",
        "rules",
        "create_approval_rule create_rule_from_action list_approval_rules revoke_approval_rule "
        "show_approval_rule suggest_rule_constraints",
    ),
    *_declare(
        "approvals",
        "promotions",
        "confirm_promotion_suggestion dismiss_promotion_suggestion list_promotion_suggestions",
    ),
    *_declare(
        "calendar",
        "core",
        "calendar_create_event calendar_delete_event calendar_delete_event_instance "
        "calendar_find_free_slots calendar_force_sync calendar_get_event calendar_list_calendars "
        "calendar_list_events calendar_propose_event calendar_set_primary calendar_sync_status "
        "calendar_update_event calendar_update_event_instance reminder_create reminder_dismiss "
        "reminder_list",
    ),
    *_declare(
        "calendar",
        "butler_events",
        "calendar_create_butler_event calendar_delete_butler_event calendar_toggle_butler_event "
        "calendar_update_butler_event",
    ),
    *_declare("calendar", "attendees", "calendar_add_attendees calendar_remove_attendees"),
    *_declare(
        "contacts",
        "sync",
        "contacts_source_list contacts_source_reconcile contacts_sync_now contacts_sync_status",
    ),
    *_declare(
        "dashboard_read",
        "fleet",
        "dashboard_read_butler_activity dashboard_read_butler_detail "
        "dashboard_read_fleet_errors_recent dashboard_read_fleet_search "
        "dashboard_read_fleet_status dashboard_read_timeline_recent",
    ),
    *_declare(
        "dashboard_read",
        "sessions",
        "dashboard_read_session_detail dashboard_read_sessions_aggregate "
        "dashboard_read_sessions_recent dashboard_read_sessions_trigger_breakdown",
    ),
    *_declare(
        "dashboard_read",
        "spend",
        "dashboard_read_spend_breakdown_by_butler dashboard_read_spend_breakdown_by_model "
        "dashboard_read_spend_daily dashboard_read_spend_summary dashboard_read_spend_top_sessions",
    ),
    *_declare("dashboard_read", "insights", "dashboard_read_insight_delivery_state"),
    *_declare("document_renderer", "render", "render_chart render_document"),
    *_declare("email", "read", "email_read_message email_search_inbox"),
    *_declare("email", "write", "email_reply_to_thread email_send_message"),
    *_declare(
        "google_drive",
        "files",
        "drive_create_folder drive_get_file_metadata drive_list_files drive_move_file "
        "drive_read_file drive_search_files drive_write_file",
    ),
    *_declare(
        "google_health",
        "health",
        "health_activity_summary health_breathing_rate_history health_hr_history "
        "health_hrv_history health_sleep_history health_sleep_latest health_spo2_history "
        "health_vo2_max_latest",
    ),
    *_declare(
        "memory",
        "core",
        "memory_confirm memory_context memory_get memory_recall memory_search memory_store_episode "
        "memory_store_fact memory_store_rule",
    ),
    *_declare("memory", "feedback", "memory_forget memory_mark_harmful memory_mark_helpful"),
    *_declare(
        "memory",
        "admin",
        "memory_predicate_list memory_predicate_search memory_reclassify memory_reembed "
        "memory_reembed_pending_count memory_run_consolidation memory_run_episode_cleanup "
        "memory_stats",
    ),
    *_declare(
        "memory",
        "entity",
        "memory_catalog_search memory_entity_create memory_entity_get memory_entity_merge "
        "memory_entity_neighbors memory_entity_resolve memory_entity_update",
    ),
    *_declare("memory", "preferences", "memory_get_preferences memory_set_preference"),
    *_declare(
        "metrics",
        "metrics",
        "metrics_define metrics_emit metrics_list metrics_query metrics_query_range",
    ),
    *_declare("pipeline", "routing", "pipeline.process", presentable=False, posture="eager"),
    *_declare("qa", "control", "force_patrol get_qa_status report_finding", presentable=False),
    *_declare(
        "self_healing",
        "control",
        "get_healing_status report_error retry_healing",
        presentable=False,
    ),
    *_declare(
        "spotify",
        "search",
        "spotify_get_recommendations spotify_get_related_artists spotify_search",
    ),
    *_declare(
        "spotify",
        "playback",
        "spotify_add_to_queue spotify_get_playback_state spotify_get_queue spotify_pause "
        "spotify_play spotify_seek spotify_set_volume spotify_skip_next spotify_skip_previous "
        "spotify_transfer_playback",
    ),
    *_declare(
        "spotify",
        "library",
        "spotify_get_saved_tracks spotify_remove_saved_tracks spotify_save_tracks",
    ),
    *_declare(
        "spotify",
        "playlists",
        "spotify_add_tracks_to_playlist spotify_create_playlist spotify_get_playlist_tracks "
        "spotify_get_playlists spotify_remove_tracks_from_playlist",
    ),
    *_declare("spotify", "profile", "spotify_get_top_items"),
    *_declare(
        "steam",
        "steam",
        "steam_get_achievements steam_get_current_players steam_get_friend_list "
        "steam_get_game_news steam_get_owned_games steam_get_player_level "
        "steam_get_player_summary steam_get_recently_played steam_resolve_vanity_url",
    ),
    *_declare(
        "telegram",
        "messages",
        "telegram_edit_message_text telegram_react_to_message telegram_reply_to_message "
        "telegram_send_message",
    ),
    *_declare("whatsapp", "messages", "whatsapp_reply_to_message whatsapp_send_message"),
    # Roster modules.
    *_declare(
        "chronicler",
        "chronicle",
        "chronicler_day_close_bundle chronicler_gap_interview chronicler_get_episode "
        "chronicler_list_corrections chronicler_list_episodes chronicler_list_events "
        "chronicler_resolve_gap_interview chronicler_submit_correction",
    ),
    *_declare(
        "education",
        "mind_maps",
        "mind_map_create mind_map_edge_create mind_map_edge_delete mind_map_frontier mind_map_get "
        "mind_map_list mind_map_node_create mind_map_node_get mind_map_node_list "
        "mind_map_node_update mind_map_subtree mind_map_update_status",
    ),
    *_declare(
        "education",
        "teaching",
        "teaching_cite_source teaching_flow_abandon teaching_flow_advance teaching_flow_get "
        "teaching_flow_list teaching_flow_start teaching_reading_pathways",
    ),
    *_declare(
        "education",
        "mastery",
        "mastery_detect_struggles mastery_get_map_summary mastery_get_node_history "
        "mastery_record_response",
    ),
    *_declare(
        "education",
        "spaced_repetition",
        "spaced_repetition_pending_reviews spaced_repetition_record_response "
        "spaced_repetition_schedule_cleanup",
    ),
    *_declare(
        "education", "diagnostics", "diagnostic_complete diagnostic_record_probe diagnostic_start"
    ),
    *_declare(
        "education", "curriculum", "curriculum_generate curriculum_next_node curriculum_replan"
    ),
    *_declare(
        "education",
        "analytics",
        "analytics_get_cross_topic analytics_get_snapshot analytics_get_trend",
    ),
    *_declare(
        "education",
        "source_material",
        "source_material_list source_material_register source_material_remove",
    ),
    *_declare(
        "finance",
        "core",
        "account_feed_freshness delete_transaction list_distinct_merchants list_transactions "
        "reconcile_feed_vs_email record_transaction update_transaction",
    ),
    *_declare(
        "finance",
        "subscriptions",
        "detect_price_changes detect_recurring subscription_audit track_subscription",
    ),
    *_declare(
        "finance",
        "bills",
        "compose_bills_digest predict_bills reconcile_bills track_bill upcoming_bills",
    ),
    *_declare(
        "finance",
        "analytics",
        "anomaly_scan cash_flow compute_baselines detect_duplicates net_worth_history "
        "net_worth_snapshot spending_forecast spending_summary spending_trends",
    ),
    *_declare(
        "finance",
        "facts",
        "list_transaction_facts spending_summary_facts track_account_fact track_subscription_fact",
    ),
    *_declare(
        "finance",
        "bulk",
        "bulk_recategorize bulk_record_transactions bulk_update_transactions import_transactions "
        "import_transactions_from_file merge_duplicates split_transaction",
    ),
    *_declare(
        "finance",
        "intelligence",
        "alert_configure alert_list flag_tax_deductible learn_merchant_categories "
        "recall_merchant_mappings suggest_categories",
    ),
    *_declare("finance", "budgets", "budget_list budget_remove budget_set budget_status"),
    *_declare("general", "context", "check_context clear_context set_context"),
    *_declare(
        "general",
        "collections",
        "collection_create collection_declare collection_delete collection_export "
        "collection_list collection_resolve",
    ),
    *_declare("general", "items", "item_create item_delete item_get item_search item_update"),
    *_declare(
        "health",
        "measurements",
        "measurement_delete measurement_history measurement_latest measurement_log "
        "measurement_update",
    ),
    *_declare(
        "health",
        "medications",
        "medication_add medication_history medication_list medication_log_dose "
        "medication_travel_snapshot",
    ),
    *_declare("health", "conditions", "condition_add condition_list condition_update"),
    *_declare(
        "health",
        "symptoms",
        "symptom_delete symptom_history symptom_log symptom_search symptom_update",
    ),
    *_declare(
        "health", "nutrition", "meal_delete meal_history meal_log meal_update nutrition_summary"
    ),
    *_declare("health", "reports", "health_summary trend_report"),
    *_declare(
        "health",
        "research",
        "research_delete research_save research_search research_summarize research_update",
    ),
    *_declare("health", "ingestion", "wellness_ingest_envelope", presentable=False),
    *_declare(
        "home_assistant",
        "core",
        "ha_activate_scene ha_call_service ha_get_entity_state ha_list_areas ha_list_entities "
        "ha_list_services",
    ),
    *_declare("home_assistant", "history", "ha_get_history ha_get_statistics ha_render_template"),
    *_declare(
        "home_assistant",
        "maintenance",
        "ha_maintenance_complete ha_maintenance_create ha_maintenance_list ha_maintenance_remove",
    ),
    *_declare(
        "lifestyle",
        "taste",
        "taste_add_verdict taste_backfill_ledger taste_get_summary taste_get_work taste_list_works",
    ),
    *_declare(
        "relationship",
        "contacts",
        "channel_add channel_search contact_create contact_get contact_resolve contact_search "
        "contact_update",
    ),
    *_declare(
        "relationship",
        "contacts_extended",
        "address_add address_list address_remove address_update channel_list contact_archive "
        "contact_export_vcard contact_import_vcard contact_merge",
    ),
    *_declare(
        "relationship",
        "social",
        "date_add date_list gift_add gift_list gift_update_status group_add_member group_create "
        "group_list group_members",
    ),
    *_declare(
        "relationship",
        "management",
        "contacts_overdue dunbar_tier_set stay_in_touch_set upcoming_dates",
    ),
    *_declare(
        "relationship",
        "interactions",
        "fact_list fact_set feed_get interaction_list interaction_log interaction_log_group",
    ),
    *_declare(
        "relationship",
        "notes",
        "contact_search_by_label label_assign label_create note_create note_list note_search",
    ),
    *_declare(
        "relationship",
        "relationships",
        "life_event_list life_event_log life_event_types_list relationship_add relationship_list "
        "relationship_remove relationship_type_get relationship_types_list",
    ),
    *_declare(
        "relationship",
        "tracking",
        "commitment_capture commitment_resolve_from_utterance loan_create loan_list loan_settle "
        "task_complete task_create task_delete task_list",
    ),
    *_declare(
        "relationship",
        "entity",
        "entity_get entity_neighbors entity_resolve entity_set_posture entity_update "
        "relationship_assert_fact "
        "relationship_fact_evidence relationship_lookup relationship_predicate_coverage "
        "relationship_record_coverage",
    ),
    *_declare("switchboard", "routing", "correct_route list_butlers route"),
    *_declare("switchboard", "delivery", "deliver", presentable=False),
    *_declare("switchboard", "lifecycle", "connector_disconnect", presentable=False),
    *_declare(
        "switchboard",
        "extraction",
        "extraction_log_list extraction_log_undo log_extraction",
    ),
    *_declare(
        "switchboard",
        "backfill",
        "backfill_cancel backfill_list backfill_pause backfill_resume create_backfill_job",
    ),
    *_declare(
        "switchboard",
        "operator",
        "abort_request cancel_request force_complete_request get_dead_letter_stats "
        "list_replay_eligible_requests manual_reroute_request replay_dead_letter_request",
        presentable=False,
    ),
    *_declare(
        "insight_broker",
        "insights",
        "insight_mark_useful insight_mute insight_snooze propose_insight_candidate",
    ),
    *_declare(
        "owner_conditions_broker",
        "conditions",
        "reconcile_owner_condition resolve_owner_condition",
        presentable=False,
    ),
    *_declare(
        "travel",
        "travel",
        "acknowledge_connection_risk add_document health_medication_snapshot list_trips "
        "record_booking trip_summary upcoming_travel update_itinerary",
    ),
)


TOOL_PRESENTATION_BY_NAME = MappingProxyType(
    {item.canonical_name: item for item in TOOL_PRESENTATION_INVENTORY}
)

if len(TOOL_PRESENTATION_BY_NAME) != len(TOOL_PRESENTATION_INVENTORY):
    raise RuntimeError("duplicate canonical name in tool presentation inventory")
