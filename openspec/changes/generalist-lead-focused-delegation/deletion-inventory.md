# RFC 0038 switch deletion inventory

Census commit: `348111b028468a61a44ff1b7efd48417bfd54dc8` (main, 2026-11-01). Issue: bu-idc5mx.1. Release that performs the deletions: bu-enbrs2.

This file lists what the RFC 0038 switch release deletes and what it must keep. It deletes nothing itself. Every repository path below is tracked at the census commit. Every `path::symbol` names identifiers that occur in that file at the census commit. Line numbers are census-time hints only. Re-run the census on the release branch before deleting: the release PR deletes exactly this list, refreshed, and nothing else.

Authority: `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md` (D1 hard cutover, D5, D7, D9, D15, D17, D18, D19) and the REMOVED/MODIFIED deltas in this change. Dashboard spec dispositions belong to bu-k2z6iw item (g), delivered as `openspec/changes/dashboard-one-mind-goal-surface/contract-migration.md` and `openspec/changes/dashboard-one-mind-goal-surface/old-contracts.md`; this file does not restate them.

Dispositions:

- **REMOVE**: the release deletes the path or symbol.
- **REPLACE**: the release deletes the old implementation once the named successor is in place.
- **KEEP**: out of scope for deletion; listed because a reader would otherwise assume it dies.
- **OPEN**: needs an owner answer before the release can act.
- **RE-ATTACH**: survives, but its only legacy caller dies, so the release must wire it to the goal-runtime caller.

The switch runs no destructive migration. All migrations and historical tables (routing, rule promotion, delegation ledger, `public.permissions`) stay.

## 1. Core and Switchboard production

Governing REMOVED deltas: `openspec/changes/generalist-lead-focused-delegation/specs/switchboard-rule-promotion/spec.md`, `openspec/changes/generalist-lead-focused-delegation/specs/complexity-classification/spec.md`, `openspec/changes/generalist-lead-focused-delegation/specs/routing-scorecard/spec.md`, `openspec/changes/generalist-lead-focused-delegation/specs/module-pipeline/spec.md` and the partial `openspec/changes/generalist-lead-focused-delegation/specs/core-spawner/spec.md`. D1 retires classify-and-route; D9 replaces domain-to-domain delegation with runner delegation; D18 limits the Spawner to engineering executors.

### 1.1 Whole modules (REMOVE)

| Path | Lines | Reason |
| --- | --- | --- |
| `roster/switchboard/jobs/rule_promotion_trigger.py` | 112 | Rule promotion/demotion (switchboard-rule-promotion REMOVED). |
| `roster/switchboard/tools/routing/__init__.py` | 23 | Classify-and-route dispatch (D1). |
| `roster/switchboard/tools/routing/classify.py` | 214 | LLM classification (complexity-classification REMOVED, D1). |
| `roster/switchboard/tools/routing/contracts.py` | 255 | route.v1 models only. ingest.v1/notify.v1 already moved to `tools/contracts.py` (PR #4900), so nothing here survives. |
| `roster/switchboard/tools/routing/correct_route.py` | 473 | Classify-and-route dispatch (D1). |
| `roster/switchboard/tools/routing/preflight.py` | 183 | Route eligibility/preflight. bu-2c1g7v names the surviving service relays first. |
| `roster/switchboard/tools/routing/route.py` | 897 | Classify-and-route dispatch (D1). |
| `roster/switchboard/tools/routing/rule_demotion.py` | 381 | Rule promotion/demotion (switchboard-rule-promotion REMOVED). |
| `roster/switchboard/tools/routing/rule_promotion.py` | 920 | Rule promotion/demotion (switchboard-rule-promotion REMOVED). |
| `roster/switchboard/tools/routing/rule_promotion_apply.py` | 269 | Rule promotion/demotion (switchboard-rule-promotion REMOVED). |
| `roster/switchboard/tools/routing/structured_classify.py` | 594 | LLM classification (complexity-classification REMOVED, D1). |
| `roster/switchboard/tools/routing/telemetry.py` | 241 | Routing verdict log and scorecard telemetry (routing-scorecard REMOVED). |
| `roster/switchboard/tools/routing/verdict_log.py` | 214 | Routing verdict log and scorecard telemetry (routing-scorecard REMOVED). |
| `roster/switchboard/tools/triage/__init__.py` | 26 | Sticky thread-affinity routing (D1). |
| `roster/switchboard/tools/triage/thread_affinity.py` | 475 | Sticky thread-affinity routing (D1). |
| `src/butlers/core/delegation_ledger.py` | 608 | Domain-to-domain session delegation (cross-butler-delegation REMOVED, D9). |
| `src/butlers/core/delegation_wake.py` | 378 | Domain-to-domain session delegation (cross-butler-delegation REMOVED, D9). |
| `src/butlers/core/mcp_warmup.py` | 227 | Per-butler session MCP warmup; goal invocations get broker-issued tool surfaces (D7/D18). |
| `src/butlers/core/route_observability.py` | 39 | Route-only failure context (D1). |
| `src/butlers/core/routing_context.py` | 25 | Classify-and-route dispatch (D1). |
| `src/butlers/core_tools/_delegation.py` | 576 | Domain-to-domain session delegation (cross-butler-delegation REMOVED, D9). |
| `src/butlers/core_tools/_switchboard_route_dispatch.py` | 182 | Classify-and-route dispatch (D1). |
| `src/butlers/modules/pipeline.py` | 4596 | Classification, decomposition and session fan-out (module-pipeline REMOVED). Identity injection and the engagement hook already moved out (PR #4900). |
| `src/butlers/routing_guidance.py` | 191 | Duplicate of routed-prompt guidance in `core_tools/_routing.py`; D15 input fencing replaces it. |

Total: 24 files, 12099 lines at the census commit.

### 1.2 Already carved out (KEEP)

bu-idc5mx.2 (PR #4900) moved the live contracts out of the deletion set before the switch. These homes import nothing from section 1.1 and survive:

| Path | Disposition | Note |
| --- | --- | --- |
| `roster/switchboard/tools/contracts.py::parse_ingest_envelope; parse_notify_request` | KEEP | ingest.v1 and notify.v1 wire contracts. |
| `roster/switchboard/tools/transport.py` | KEEP | Transport outcome vocabulary. |
| `roster/switchboard/tools/registry/registry.py::load_available_butlers` | KEEP | Butler candidate loader used by ingest's `pinned_target` check. |
| `src/butlers/switchboard_wiring.py::build_switchboard_buffer` | KEEP | Buffer construction; never reads `daemon.spawner`. |
| `roster/switchboard/tools/identity/inject.py` | RE-ATTACH | Only production caller is `MessagePipeline`. Re-attach to goal admission in `src/butlers/core/goal_admission.py` (bu-fkoexp). |
| `roster/switchboard/tools/insight/engagement.py::record_owner_ingress_engagement` | RE-ATTACH | Called from `MessagePipeline.process` today. Re-attach to goal admission; the buffer's `process_fn` becomes the admission callback. |

### 1.3 Partial modules and registrations

Only the named symbols die. Everything else in the file stays.

| Path::symbols | Disposition | Scope |
| --- | --- | --- |
| `src/butlers/core_tools/_routing.py::route_execute; parse_route_envelope` | REMOVE | `route.execute` registration/dispatch and its route.v1 import. Messenger native delivery, approval and receipt behavior moves to bu-2c1g7v's named relays first (D7). |
| `src/butlers/core_tools/_switchboard.py::route_to_butler; _dispatch_dashboard_target; answer_question; cannot_answer; _dashboard_cannot_answer; file_bug_report` | REMOVE | Model-facing routing and dashboard-lane tools. KEEP ingest, `connector.heartbeat`, `backfill.*` and registration. |
| `src/butlers/core/corrections.py::check_misroute_preconditions; handle_misroute` | REMOVE | Misroute re-dispatch only. KEEP data correction, memory deletion and action reversal. |
| `roster/switchboard/tools/operator/controls.py::manual_reroute_request` | REMOVE | Writes `lifecycle_state='rerouted'`, which no code reads. KEEP cancel, abort and force-complete. |
| `src/butlers/core/dashboard_turns.py::claim_target; mark_route_enqueued; register_session_and_check_cancel; reconcile_route_recovery; claim_invoke; release_invoke; complete_session; live_sessions; dispatch_status` | REPLACE | Route/session linkage; goal admission, receipts and Stop replace it. KEEP ingress idempotence and conversation binding. |
| `src/butlers/core/spawner_context.py::fetch_general_timezone_instruction; fetch_memory_context; fetch_system_prompt_override; fetch_routing_instructions; fetch_situational_context_preamble; fetch_blind_spot_preamble; store_session_episode` | REMOVE | Per-domain session context fetchers. Goal input (D15) and outcome capture replace them. |
| `src/butlers/core/runtimes/api.py::invoke_structured` | REMOVE | `ApiAdapter.invoke_structured`, the classifier multi-tool path. KEEP tool-less invoke. |
| `src/butlers/lifecycle.py::_warmup_mcp_endpoints_best_effort` | REMOVE | MCP warmup call and task, plus domain Spawner construction. KEEP module lifecycle, pools, schedules, heartbeat, shutdown. |
| `src/butlers/daemon.py::_wire_pipelines; _recover_route_inbox` | REMOVE | Pipeline wiring and route-inbox recovery, with the `modules.pipeline` and `routing.preflight` imports. KEEP the MCP HTTP route helpers. |
| `src/butlers/switchboard_wiring.py::modules.pipeline; parse_route_envelope` | REMOVE | Pipeline and route.v1 imports; `build_switchboard_buffer` stays (1.2). |
| `src/butlers/background.py::dispatch_scheduled_task` | REMOVE | Only the prompt-mode `spawner.trigger` branch. KEEP job dispatch; prompt obligations become admissions (bu-o9qax4). |
| `roster/switchboard/tools/ingestion/ingest.py::thread_affinity` | REMOVE | Triage/thread-affinity target selection only. KEEP UUID/context, inbox/dedup, policy, channel binding and partition durability. |
| `roster/switchboard/tools/__init__.py::routing.route; routing.telemetry` | REMOVE | Re-exports of route.v1, `routing.route` and `routing.telemetry`. |
| `roster/switchboard/tools/notification/deliver.py::route; parse_route_envelope` | REPLACE | notify rides route.v1 today; bu-2c1g7v moves it to a named relay. |
| `roster/switchboard/tools/extraction/audit_log.py::route` | REPLACE | `routing.route` caller; bu-2c1g7v. |
| `src/butlers/core_tools/_domain_events.py::dispatch_via_switchboard_route` | REPLACE | Route caller; bu-2c1g7v. |
| `src/butlers/core_tools/_dashboard_system_answer.py::route` | REPLACE | Route caller; bu-2c1g7v. |
| `roster/switchboard/modules/tools.py::_route` | REPLACE | Route caller; bu-2c1g7v. |
| `roster/switchboard/api/router.py::rule_promotion` | REMOVE | Rule-promotion imports (routes in section 3). |
| `roster/switchboard/jobs/__init__.py::run_rule_promotion_trigger_job` | REMOVE | Export of the promotion job. |
| `roster/switchboard/butler.toml::rule-promotion-trigger` | REMOVE | The `[[butler.schedule]]` entry `rule-promotion-trigger` (`job_name = "rule_promotion_trigger"`). |

### 1.4 Route callers outside the deleted modules

An AST scan of production code for `route` calls found 25 call sites; 8 sit inside section 1.1 files and die with them. bu-2c1g7v must classify each of the 17 below as an authenticated named service relay or a removed call. A generic route proxy is not a survivor.

| Path | Line | Call | Owner |
| --- | --- | --- | --- |
| `roster/switchboard/modules/tools.py` | 89 | `_route` | bu-2c1g7v: named service relay or removed. |
| `roster/switchboard/tools/extraction/audit_log.py` | 224 | `route` | bu-2c1g7v: named service relay or removed. |
| `roster/switchboard/tools/notification/deliver.py` | 340 | `route` | bu-2c1g7v: named service relay or removed. |
| `roster/switchboard/tools/notification/deliver.py` | 651 | `route` | bu-2c1g7v: named service relay or removed. |
| `roster/switchboard/tools/notification/deliver.py` | 892 | `route` | bu-2c1g7v: named service relay or removed. |
| `roster/switchboard/tools/notification/deliver.py` | 1048 | `route` | bu-2c1g7v: named service relay or removed. |
| `roster/travel/tools/health.py` | 61 | `switchboard_client.call_tool` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core/qa/relay.py` | 161 | `client.call_tool` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core_tools/_dashboard_system_answer.py` | 130 | `route` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core_tools/_domain_events.py` | 153 | `dispatch_via_switchboard_route` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core_tools/_domain_events.py` | 501 | `_switchboard_route` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core_tools/_fleet_cases.py` | 85 | `dispatch_via_switchboard_route` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core_tools/_memory_catalog.py` | 97 | `dispatch_via_switchboard_route` | bu-2c1g7v: named service relay or removed. |
| `src/butlers/core_tools/_switchboard.py` | 754 | `_switchboard_route` | Dies with `route_to_butler` and the dashboard lane (1.3). |
| `src/butlers/core_tools/_switchboard.py` | 1502 | `_switchboard_route` | Dies with `route_to_butler` and the dashboard lane (1.3). |
| `src/butlers/modules/calendar_response_fence.py` | 190 | `client.call_tool` | bu-2c1g7v (named relay) or bu-qozovd.1 (fence owner change); see section 2. |
| `src/butlers/modules/contacts/owner.py` | 37 | `client.call_tool` | bu-2c1g7v: named service relay or removed. |

### 1.5 Absence guard

bu-ly3lv5.13 (retired-surfaces registry) is open. Until it lands, bu-enbrs2 AC2 must scan module paths as well as tool names: the release head must contain no file at any section 1.1 path and no import or string reference (including `unittest.mock.patch` targets) to these modules outside docs and migrations:

- `butlers.core.delegation_ledger`
- `butlers.core.delegation_wake`
- `butlers.core.mcp_warmup`
- `butlers.core.route_observability`
- `butlers.core.routing_context`
- `butlers.core_tools._delegation`
- `butlers.core_tools._switchboard_route_dispatch`
- `butlers.modules.pipeline`
- `butlers.routing_guidance`
- `butlers.tools.switchboard.routing`
- `butlers.tools.switchboard.routing.classify`
- `butlers.tools.switchboard.routing.contracts`
- `butlers.tools.switchboard.routing.correct_route`
- `butlers.tools.switchboard.routing.preflight`
- `butlers.tools.switchboard.routing.route`
- `butlers.tools.switchboard.routing.rule_demotion`
- `butlers.tools.switchboard.routing.rule_promotion`
- `butlers.tools.switchboard.routing.rule_promotion_apply`
- `butlers.tools.switchboard.routing.structured_classify`
- `butlers.tools.switchboard.routing.telemetry`
- `butlers.tools.switchboard.routing.verdict_log`
- `butlers.tools.switchboard.triage`
- `butlers.tools.switchboard.triage.thread_affinity`

Also scan for the section 1.3 symbols marked REMOVE. If bu-ly3lv5.13 lands first, register these paths there instead and keep one guard.

## 2. Domain code, configuration and skills

| Path::symbols | Disposition | Scope |
| --- | --- | --- |
| `src/butlers/modules/calendar_response_fence.py::check_peers; call_tool` | REPLACE | The generic `route` call in `CalendarResponseFence.check_peers`. bu-2c1g7v adds it to its relay inventory, or bu-qozovd.1 removes it via the fence's owner change. Recipient, approval, idempotency and uncertain-delivery fencing survive. |
| `src/butlers/modules/memory/consolidation.py::cc_spawner` | REMOVE | Only the flag-off branch that calls `cc_spawner.trigger` per group, once consolidation runs as an obligation goal (bu-vbdug9.2). Episode claims, partitioning, failure recovery and idempotency survive. |
| `src/butlers/core/memory_hooks.py` | KEEP | Generic dependency injection; not part of the spawn branch. |
| `src/butlers/modules/calendar/internal_projection.py::_scheduler_projection_fingerprint; _project_scheduler_source; _publish_calendar_fleet_event` | REPLACE | Calendar prompt-task lane projected into the scheduler. Re-key to declared obligations or deterministic jobs (bu-o9qax4, bu-idc5mx.4). Calendar data, sync and native effects survive. |
| `src/butlers/modules/calendar/butler_events.py::dispatch_mode` | REPLACE | The `dispatch_mode="prompt"` default and branch that push prompt tasks into that lane. |
| `src/butlers/jobs/briefing.py::dispatch_delegated_ask` | REPLACE | Delegated finance ask; the admitted briefing goal uses capability reads instead (D9). Read-only facts, no-send and degraded evidence survive. |
| `roster/finance/butler.toml::"sessions"; "delegation"` | REMOVE | The `sessions` and `delegation` entries in `[butler.core_tools] groups`. |
| `roster/relationship/butler.toml::"sessions"; "delegation"` | REMOVE | The `sessions` and `delegation` entries in `[butler.core_tools] groups`. |

The eight `cross-butler-delegation` skill symlinks die with cross-butler delegation (REMOVE the links; the shared skill body goes once nothing references it):

- `roster/chronicler/.agents/skills/cross-butler-delegation`
- `roster/education/.agents/skills/cross-butler-delegation`
- `roster/finance/.agents/skills/cross-butler-delegation`
- `roster/general/.agents/skills/cross-butler-delegation`
- `roster/health/.agents/skills/cross-butler-delegation`
- `roster/home/.agents/skills/cross-butler-delegation`
- `roster/lifestyle/.agents/skills/cross-butler-delegation`
- `roster/relationship/.agents/skills/cross-butler-delegation`

## 3. Dashboard, API and read models

D19 replaces butler-unit views with goals and regions. bu-idc5mx.3 already moved the tab-only domain views into per-domain component directories such as `frontend/src/components/finance/` (PR #4905); those survive as region bodies and are not listed.

**Dark-build boundary.** The goal, region and overview pages are not built yet; bu-vvsfi8 owns them and has not chosen a route prefix or flag. bu-vvsfi8 records the prefix here when it picks one, so the switch is "flip nav, `git rm` this list".

### 3.1 Frontend files (REMOVE unless marked)

| Path | Disposition |
| --- | --- |
| `frontend/src/components/butler-detail/ButlerActivitySection.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerActivityTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerApprovalsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerChroniclerTimelinesTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerConfigTab.tsx` | KEEP (operator view: config, state, runtime config, skills) |
| `frontend/src/components/butler-detail/ButlerDelegationsPanel.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerDetailActions.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerDetailHeader.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerDomainEventsPanel.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerEducationReviewsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerFrictionPanel.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerHealthMeasurementsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerLogsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerManagementTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerMcpTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerMemoryTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerModelOverridesTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerOverviewTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerQaInvestigationsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerRegistryTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerRelationshipContactsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerSchedulesTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerSessionsTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerSkillsTab.tsx` | KEEP (operator view: config, state, runtime config, skills) |
| `frontend/src/components/butler-detail/ButlerSpendTab.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerStateTab.tsx` | KEEP (operator view: config, state, runtime config, skills) |
| `frontend/src/components/butler-detail/ButlerSystemSection.tsx` | REMOVE |
| `frontend/src/components/butler-detail/ButlerVerdictOpener.tsx` | REMOVE |
| `frontend/src/components/butler-detail/GoogleHealthStatusCard.tsx` | REMOVE |
| `frontend/src/components/butler-detail/RuntimeConfigCard.tsx` | KEEP (operator view: config, state, runtime config, skills) |
| `frontend/src/components/butler-detail/SiblingButlerNav.tsx` | REMOVE |
| `frontend/src/components/butler-detail/command-bar-prefill.tsx` | REMOVE |
| `frontend/src/components/butler-detail/schedule-header-facts.ts` | REMOVE |
| `frontend/src/components/chat/ChatDock.tsx` | REMOVE |
| `frontend/src/components/chat/ChatPanel.tsx` | REMOVE |
| `frontend/src/components/chat/ChatRecallCommands.tsx` | REMOVE |
| `frontend/src/components/chat/ContextChip.tsx` | REMOVE |
| `frontend/src/components/chat/ConversationHeader.tsx` | REMOVE |
| `frontend/src/components/chat/ConversationList.tsx` | REMOVE |
| `frontend/src/components/chat/ConversationReadError.tsx` | REMOVE |
| `frontend/src/components/chat/FloatingChatWidget.tsx` | REMOVE |
| `frontend/src/components/chat/MessageInput.tsx` | REMOVE |
| `frontend/src/components/chat/MessageThread.tsx` | REMOVE |
| `frontend/src/components/chat/ToolCallDetails.tsx` | REMOVE |
| `frontend/src/components/chat/TypingIndicator.tsx` | REMOVE |
| `frontend/src/components/chat/answer/AnswerBody.tsx` | REMOVE |
| `frontend/src/components/chat/answer/CitationRow.tsx` | REMOVE |
| `frontend/src/components/chat/answer/markdown.ts` | REMOVE |
| `frontend/src/components/chat/chat-constants.ts` | REMOVE |
| `frontend/src/components/chat/live-announcer.ts` | REMOVE |
| `frontend/src/components/chat/message-id.ts` | REMOVE |
| `frontend/src/components/chat/message-reconciliation.ts` | REMOVE |
| `frontend/src/components/chat/send-error-utils.ts` | REMOVE |
| `frontend/src/components/chat/send-error.tsx` | REMOVE |
| `frontend/src/components/chat/sse-utils.ts` | REMOVE |
| `frontend/src/components/sessions/PurposeLaneBadge.tsx` | REMOVE |
| `frontend/src/components/sessions/SessionDetailDrawer.tsx` | REMOVE |
| `frontend/src/components/sessions/SessionDossier.tsx` | REMOVE |
| `frontend/src/components/sessions/SessionTable.tsx` | REMOVE |
| `frontend/src/components/sessions/SessionsKpiStrip.tsx` | REMOVE |
| `frontend/src/components/sessions/SessionsPinnedStrip.tsx` | REMOVE |
| `frontend/src/components/sessions/SessionsVerdictOpener.tsx` | REMOVE |
| `frontend/src/components/sessions/StatusBadge.tsx` | REMOVE |
| `frontend/src/components/sessions/ToolCallTimeline.tsx` | REMOVE |
| `frontend/src/pages/ButlerDetailPage.tsx` | REMOVE |
| `frontend/src/pages/ButlersPage.tsx` | REMOVE |
| `frontend/src/pages/ChatPage.tsx` | REMOVE |
| `frontend/src/pages/SessionDetailPage.tsx` | REMOVE |
| `frontend/src/pages/SessionsPage.tsx` | REMOVE |

### 3.2 Mixed frontend files

| Path | Scope |
| --- | --- |
| `frontend/src/router-config.tsx` | REPLACE the `/chat`, `/chat/:conversationId`, `/butlers`, `/butlers/:name`, `/sessions`, `/sessions/:id` entries with the D19 routes. KEEP domain routes such as `/butlers/relationship/...`. |
| `frontend/src/lib/shell-capability.ts` | REPLACE the matching nav entries, shortcuts and query warmups. |
| `frontend/src/api/client.ts` | REMOVE the client functions for REMOVE/REPLACE endpoints in 3.3 (list below). |
| `frontend/src/api/types.ts` | REMOVE types used only by those functions (reverse-import check at release). |
| `docs/frontend/purpose-and-single-pane.md` | REWRITE: it still promises sessions, trigger and MCP debug. |
| `frontend/src/hooks/use-butler-analytics.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-butler-logs.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-butler-management.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-butler-status-board.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-chat-unread.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-conversation-turn.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-conversations.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-delegation.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-healing.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-schedules.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |
| `frontend/src/hooks/use-sessions.ts` | REMOVE the session, conversation, delegation and per-butler query/mutation hooks; KEEP hooks for KEEP endpoints (healing reads, schedules jobs). |

Candidate client functions in `frontend/src/api/client.ts` (remove each one whose endpoint row below is REMOVE/REPLACE): `getButlers`, `getButlersBoard`, `getButler`, `getButlerConfig`, `getButlerModules`, `getSessions`, `getSessionAggregate`, `getSession`, `getSessionPrompt`, `getButlerSessions`, `getButlerHourlyActivity`, `getButlerDailyActivity`, `getButlerSessionKinds`, `getButlerLatencyStats`, `getButlerFrictionSummary`, `getButlerActivityFeed`, `getButlerMemoryStats`, `getButlerNotifications`, `getTopSessions`, `getButlerSchedules`, `createButlerSchedule`, `updateButlerSchedule`, `deleteButlerSchedule`, `triggerButlerSchedule`, `toggleButlerSchedule`, `getButlerSkills`, `getButlerLogs`, `getButlerState`, `setButlerState`, `deleteButlerState`, `triggerButler`, `forceButlerTick`, `getButlerMcpTools`, `callButlerMcpTool`, `mutateCalendarWorkspaceButlerEvent`, `previewCalendarWorkspaceButlerEvent`, `setButlerEligibility`, `getIngestionEventSessions`, `listButlerModelOverrides`, `upsertButlerModelOverrides`, `deleteButlerModelOverride`, `resolveButlerModel`, `listConversations`, `getConversationById`, `getConversationMessages`, `searchConversations`, `createConversation`, `cancelConversationMessageTurn`, `getButlerHeartbeats`, `listDelegationLedger`, `getHealingDispatchEvents`, `getButlerPrompt`, `getButlerEffectivePrompt`, `updateButlerPrompt`, `getButlerPromptHistory`, `getButlerMemoryAccess`, `killButler`.

### 3.3 API endpoints

Router-local paths; each router's prefix is in `src/butlers/api/app.py`.

| Owner | Route | Disposition | Reason |
| --- | --- | --- | --- |
| `src/butlers/api/routers/butlers.py::list_butlers` | `GET` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butlers.py::get_butlers_board` | `GET /board` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butlers.py::get_butler_detail` | `GET /{name}` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butlers.py::get_butler_config` | `GET /{name}/config` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/butlers.py::list_butler_skills` | `GET /{name}/skills` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/butlers.py::list_butler_mcp_tools` | `GET /{name}/mcp/tools` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butlers.py::call_butler_mcp_tool` | `POST /{name}/mcp/call` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butlers.py::get_butler_modules` | `GET /{name}/modules` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/butlers.py::trigger_butler` | `POST /{name}/trigger` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butlers.py::force_butler_tick` | `POST /{name}/tick` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::list_sessions` | `GET` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_session_aggregate` | `GET /aggregate` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_session_prompt_receipt` | `GET /{session_id}/prompt` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_session` | `GET /{session_id}` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::list_butler_sessions` | `GET /{name}/sessions` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_butler_session_kinds` | `GET /{name}/analytics/session-kinds` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_butler_daily_activity` | `GET /{name}/analytics/daily-activity` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_butler_hourly_activity` | `GET /{name}/analytics/hourly-activity` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_butler_latency_stats` | `GET /{name}/analytics/latency-stats` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/sessions.py::get_butler_friction_summary` | `GET /{name}/analytics/friction` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butler_management.py::get_butler_prompt` | `GET /{name}/prompt` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butler_management.py::get_butler_effective_prompt` | `GET /{name}/prompt/effective` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butler_management.py::update_butler_prompt` | `PUT /{name}/prompt` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butler_management.py::get_butler_prompt_history` | `GET /{name}/prompt/history` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/butler_management.py::get_butler_memory_access` | `GET /{name}/memory-access` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/butler_management.py::kill_butler` | `POST /{name}/kill` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::list_conversations` | `GET /{name}/conversations` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::search_conversations` | `GET /{name}/conversations/search` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::search_messages` | `GET /messages/search` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::get_conversation_by_id` | `GET /{conversation_id}` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::get_conversation_summary` | `GET /{name}/conversations/summary` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::create_conversation` | `POST /{name}/conversations` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::list_messages` | `GET /{name}/conversations/{conversation_id}/messages` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::send_message` | `POST /{name}/conversations/{conversation_id}/messages` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::cancel_dashboard_message_turn` | `POST /{name}/conversation-turns/{message_id}/cancel` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::cancel_conversation_turn` | `POST /{name}/conversations/{conversation_id}/cancel` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/conversations.py::update_conversation` | `PATCH /{name}/conversations/{conversation_id}` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/healing.py::list_healing_attempts` | `GET /attempts` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/healing.py::get_healing_attempt` | `GET /attempts/{attempt_id}` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/healing.py::get_circuit_breaker_status` | `GET /circuit-breaker` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/healing.py::reset_circuit_breaker` | `POST /circuit-breaker/reset` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/healing.py::list_healing_dispatch_events` | `GET /dispatch-events` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/state.py::list_state` | `GET /{name}/state` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/state.py::get_state` | `GET /{name}/state/{key:path}` | KEEP | Operator/QA read evidence. |
| `src/butlers/api/routers/state.py::set_state` | `PUT /{name}/state/{key:path}` | REPLACE | Editing surface per D19 disposition; state reads stay. |
| `src/butlers/api/routers/state.py::delete_state` | `DELETE /{name}/state/{key:path}` | REPLACE | Editing surface per D19 disposition; state reads stay. |
| `src/butlers/api/routers/delegation.py::list_delegation_ledger` | `GET /ledger` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/delegation.py::get_delegation_ledger_entry` | `GET /ledger/{ledger_id}` | REMOVE | Butler-unit/session/conversation/delegation lane (D19). |
| `src/butlers/api/routers/schedules.py::list_schedules` | `GET /{name}/schedules` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::create_schedule` | `POST /{name}/schedules` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::update_schedule` | `PUT /{name}/schedules/{schedule_id}` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::delete_schedule` | `DELETE /{name}/schedules/{schedule_id}` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::trigger_schedule` | `POST /{name}/schedules/{schedule_id}/trigger` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::toggle_schedule` | `PATCH /{name}/schedules/{schedule_id}/toggle` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::create_break_through_clause` | `POST /attention/break-through-clauses` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/schedules.py::revoke_break_through_clause` | `DELETE /attention/break-through-clauses/{clause_id}` | REPLACE | Prompt-mode schedule semantics only; job schedules and their controls stay. |
| `src/butlers/api/routers/permissions.py::get_permissions_matrix` | `GET` | KEEP | Read-only history view (bu-i661vs = A). |
| `src/butlers/api/routers/permissions.py::set_permission` | `PUT /{butler}/{perm}` | REMOVE | Matrix editor PUT (bu-i661vs = A). |

Switchboard routing routes (17, not the 7 first estimated). Heartbeat, ingestion, backfill, fleet cases and insight routes stay:

| Owner | Route | Disposition | Reason |
| --- | --- | --- | --- |
| `roster/switchboard/api/router.py::list_routing_log` | `GET /routing-log` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::list_registry` | `GET /registry` | REPLACE | Registry health/eligibility is infrastructure; rehome off the butler-unit view. |
| `roster/switchboard/api/router.py::set_butler_eligibility` | `POST /registry/{name}/eligibility` | REPLACE | Registry health/eligibility is infrastructure; rehome off the butler-unit view. |
| `roster/switchboard/api/router.py::get_thread_affinity_settings` | `GET /thread-affinity/settings` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::update_thread_affinity_settings` | `PATCH /thread-affinity/settings` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::list_thread_affinity_overrides` | `GET /thread-affinity/overrides` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::upsert_thread_affinity_override` | `PUT /thread-affinity/overrides/{thread_id}` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::delete_thread_affinity_override` | `DELETE /thread-affinity/overrides/{thread_id}` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::list_routing_instructions` | `GET /routing-instructions` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::create_routing_instruction` | `POST /routing-instructions` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::update_routing_instruction` | `PATCH /routing-instructions/{instruction_id}` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::delete_routing_instruction` | `DELETE /routing-instructions/{instruction_id}` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::list_rule_promotion_suggestions` | `GET /rule-promotion-suggestions` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::get_rule_promotion_stats` | `GET /rule-promotion-stats` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::confirm_rule_promotion_suggestion` | `POST /rule-promotion-suggestions/{suggestion_id}/confirm` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::dismiss_rule_promotion_suggestion` | `POST /rule-promotion-suggestions/{suggestion_id}/dismiss` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |
| `roster/switchboard/api/router.py::set_rule_promotion_rule_enabled` | `POST /rule-promotion-suggestions/{suggestion_id}/rule-enabled` | REMOVE | Routing log, thread affinity, routing instructions or rule promotion (D1). |

Read models:

| Path | Version | Disposition |
| --- | --- | --- |
| `src/butlers/api/read_models/sessions.py` | `sessions_v1` | REPLACE: per-butler/session projection; underlying tables stay. |
| `src/butlers/api/read_models/activity.py` | `activity_v1` | REPLACE: per-butler/session projection; underlying tables stay. |
| `src/butlers/api/read_models/butlers.py` | `butlers_v1` | REPLACE: per-butler/session projection; underlying tables stay. |

## 4. Tests and surviving invariants

Counts are declared test functions (AST), not collected cases; parametrization expands them. The release re-collects before and after.

Survivor accounts. A deleted test either carries no invariant or names where its invariant lives after the switch:

- **A** (bu-dj4jql, adversarial suite): no unauthorized send, `route.execute`-style caller authorization becomes broker grant checks, recipient/approval authority, isolation.
- **F** (bu-dj4jql): prompt fencing of exposed input, immutable request-context injection, origin/taint, no escalation.
- **R** (bu-u5s4w8, runner): owner reply/receipt truth, unknown vs confirmed outcomes, no replay after uncertain effects, Stop, recovery, partition and request durability through admitted writes.
- **B** (bu-u5s4w8): budgets, turn/token/spend ceilings, finite relaunch.
- **N**: no invariant. Classifier, target choice, rule promotion or wiring mechanics only.

Each deleted test below names at least one account. Before the release deletes a test whose account is A, F, R or B, the named suite must carry a test for that invariant. Owner decision bu-lm2b4v = B and the retired condensation ledger (bu-28av8x.2) mean these deletions need no `Retired:` route or manifest.

Until the switch, fixes to frozen classify-and-route paths add cases to existing test files and create no new test files.

### 4.1 Whole test files (REMOVE)

| Path | Tests | Account |
| --- | --- | --- |
| `roster/switchboard/tests/test_correct_route.py` | 24 | N |
| `roster/switchboard/tests/test_routing_telemetry.py` | 1 | N |
| `roster/switchboard/tests/test_routing_verdict_log.py` | 19 | N |
| `roster/switchboard/tests/test_rule_demotion.py` | 21 | N |
| `roster/switchboard/tests/test_rule_promotion_trigger.py` | 50 | N |
| `roster/switchboard/tests/test_staffer_routing_exclusion.py` | 21 | A |
| `roster/switchboard/tests/test_structured_classify.py` | 26 | N |
| `roster/switchboard/tests/test_thread_affinity.py` | 35 | N |
| `roster/switchboard/tests/test_thread_affinity_api.py` | 22 | N |
| `tests/api/test_dashboard_turn_cancellation.py` | 11 | R |
| `tests/api/test_delegation.py` | 10 | R |
| `tests/api/test_switchboard_rule_promotions.py` | 15 | N |
| `tests/benchmarks/switchboard/test_bench.py` | 3 | N |
| `tests/config/test_delegation_guidance_reachability.py` | 6 | F, R |
| `tests/contracts/test_routing_pipeline.py` | 6 | F, R |
| `tests/core/test_core_spawner_context.py` | 8 | F, R |
| `tests/core/test_dashboard_turns.py` | 2 | R |
| `tests/core/test_delegation_ledger.py` | 43 | R |
| `tests/core/test_delegation_wake.py` | 19 | R |
| `tests/core/test_mcp_warmup.py` | 14 | N |
| `tests/core/test_spawner_speculative_prewarm.py` | 6 | N |
| `tests/core_tools/test_delegation.py` | 28 | R |
| `tests/daemon/test_dashboard_lane_tools.py` | 33 | F, R |
| `tests/daemon/test_mcp_warmup_integration.py` | 7 | N |
| `tests/daemon/test_route_default_complexity.py` | 1 | N |
| `tests/daemon/test_route_execute_async_dispatch.py` | 18 | R |
| `tests/daemon/test_route_execute_authz.py` | 14 | A |
| `tests/daemon/test_route_execute_conversation_anchor.py` | 6 | R |
| `tests/daemon/test_route_execute_prompt_fencing.py` | 3 | F |
| `tests/daemon/test_route_execute_request_context_injection.py` | 2 | F |
| `tests/daemon/test_route_execute_sender_entity_id.py` | 7 | F |
| `tests/daemon/test_route_execute_trace_decoupling.py` | 2 | F, R |
| `tests/daemon/test_route_execute_trace_propagation.py` | 2 | F, R |
| `tests/daemon/test_route_to_butler_accepted_status.py` | 4 | N |
| `tests/integration/test_decomposition_flow.py` | 8 | F, R |
| `tests/integration/test_delegation_ask_dispatch_roundtrip.py` | 4 | R |
| `tests/integration/test_delegation_ledger_roundtrip.py` | 9 | R |
| `tests/integration/test_delegation_wake_roundtrip.py` | 16 | R |
| `tests/integration/test_switchboard_routing_tools_search_path.py` | 3 | N |
| `tests/integration/test_switchboard_rule_promotion_apply.py` | 7 | N |
| `tests/integration/test_switchboard_rule_promotion_trigger_job.py` | 14 | N |
| `tests/modules/test_module_pipeline.py` | 130 | F, R |
| `tests/tools/test_rule_promotion_trigger_job.py` | 9 | N |

43 files, 689 declared tests.

### 4.2 Mixed test files, per test

Every declared test in these files is listed. KEEP tests stay unchanged apart from import repointing.

#### `tests/daemon/test_daemon.py`

4 REMOVE, 30 KEEP.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `test_startup_sequence` | 452 | KEEP | - |
| `test_all_core_tools_registered` | 546 | KEEP | - |
| `test_graph_core_group_registration` | 617 | KEEP | - |
| `test_module_lifecycle` | 663 | KEEP | - |
| `test_schedule_sync_precedes_enabled_module_startup` | 713 | KEEP | - |
| `test_shutdown_stops_mcp_server` | 758 | KEEP | - |
| `test_start_mcp_server_waits_until_uvicorn_reports_started` | 838 | KEEP | - |
| `test_build_mcp_http_app_routes_and_health` | 872 | KEEP | - |
| `test_sse_disconnect_guard` | 906 | KEEP | - |
| `test_sse_disconnect_guard_completes_started_response` | 954 | KEEP | - |
| `test_sse_disconnect_guard_leaves_completed_response_untouched` | 986 | KEEP | - |
| `test_streamable_http_disconnect_patch_is_idempotent` | 1022 | KEEP | - |
| `test_streamable_http_disconnect_patch_downgrades_client_disconnect` | 1038 | KEEP | - |
| `test_streamable_http_disconnect_patch_preserves_unrelated_errors` | 1071 | KEEP | - |
| `test_streamable_http_client_disconnect_patch_is_idempotent` | 1104 | KEEP | - |
| `test_streamable_http_client_disconnect_patch_downgrades_closed_resource_error` | 1120 | KEEP | - |
| `test_streamable_http_client_disconnect_patch_preserves_unrelated_errors` | 1145 | KEEP | - |
| `test_status_tool` | 1218 | KEEP | - |
| `test_health_degraded` | 1247 | KEEP | - |
| `test_startup_failure_propagation` | 1270 | KEEP | - |
| `test_switchboard_client_lifecycle` | 1336 | KEEP | - |
| `test_switchboard_heartbeat_uses_ping_for_liveness_probe` | 1430 | KEEP | - |
| `test_switchboard_heartbeat_reconnects_on_stale_ping_error` | 1462 | KEEP | - |
| `test_notify_schema_and_channels` | 1543 | KEEP | - |
| `test_notify_delivery_and_failures` | 1616 | KEEP | - |
| `test_manual_chronicler_refresh_suppresses_notify_without_affecting_schedule` | 1674 | KEEP | - |
| `test_route_execute_messenger_scenarios` | 1763 | REMOVE | A, F, R |
| `test_route_execute_parks_email_actions_with_native_tool_args` | 1888 | REMOVE | A, F, R |
| `test_route_execute_revalidates_notify_decision_dossier` | 1976 | REMOVE | A, F, R |
| `test_route_execute_rejects_non_owner_missing_decision_dossier` | 2094 | REMOVE | A, F, R |
| `test_non_fatal_module_failures` | 2177 | KEEP | - |
| `test_staffer_briefing_exclusion_and_type_field` | 2330 | KEEP | - |
| `test_registered_messenger_preserves_native_telegram_partial_outcome` | 2433 | KEEP | - |
| `test_actual_http_app_keeps_finance_bearer_pending_through_mcp_handshake` | 2501 | KEEP | - |

#### `tests/integration/test_email_outbound_safety.py`

12 REMOVE, 31 KEEP.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `TestEmailToolSurfaceRestriction.test_default_config_suppresses_send_tools` | 205 | KEEP | - |
| `TestEmailToolSurfaceRestriction.test_send_tools_true_registers_all_tools` | 230 | KEEP | - |
| `TestEmailToolSurfaceRestriction.test_send_tools_defaults_to_false` | 253 | KEEP | - |
| `TestNotifyRecipientValidation.test_hallucinated_email_is_parked` | 479 | KEEP | - |
| `TestNotifyRecipientValidation.test_unknown_email_is_parked` | 506 | KEEP | - |
| `TestNotifyRecipientValidation.test_owner_email_is_allowed` | 527 | KEEP | - |
| `TestNotifyRecipientValidation.test_known_non_owner_email_is_blocked_without_rule` | 557 | KEEP | - |
| `TestNotifyRecipientValidation.test_standing_rule_permits_known_non_owner_email` | 589 | KEEP | - |
| `TestNotifyRecipientValidation.test_standing_rule_permits_unknown_email` | 633 | KEEP | - |
| `TestNotifyRecipientValidation.test_no_standing_rule_still_parks` | 677 | KEEP | - |
| `TestNotifyRecipientValidation.test_telegram_channel_not_affected` | 704 | KEEP | - |
| `TestNotifyTelegramRecipientValidation.test_owner_telegram_is_allowed` | 741 | KEEP | - |
| `TestNotifyTelegramRecipientValidation.test_owner_telegram_via_definer_fallback_is_allowed` | 772 | KEEP | - |
| `TestNotifyTelegramRecipientValidation.test_known_non_owner_telegram_is_blocked_without_rule` | 807 | KEEP | - |
| `TestNotifyTelegramRecipientValidation.test_standing_rule_permits_known_non_owner_telegram` | 838 | KEEP | - |
| `TestNotifyTelegramRecipientValidation.test_unknown_telegram_is_parked` | 883 | KEEP | - |
| `TestNotifyTelegramRecipientValidation.test_owner_directed_notify_auto_approves_across_channels` | 914 | KEEP | - |
| `TestMessengerApprovalGate.test_email_send_to_unknown_is_parked` | 971 | KEEP | - |
| `TestMessengerApprovalGate.test_email_send_to_owner_is_auto_approved` | 1007 | KEEP | - |
| `TestMessengerApprovalGate.test_email_reply_to_thread_is_also_gated` | 1039 | KEEP | - |
| `TestMessengerApprovalGate.test_email_send_to_known_non_owner_without_rule_is_parked` | 1075 | KEEP | - |
| `TestIncidentReplay.test_travel_butler_cannot_reply_to_albatross_email` | 1120 | KEEP | - |
| `TestIncidentReplay.test_finance_butler_notify_rejects_hallucinated_jo_email` | 1148 | KEEP | - |
| `TestIncidentReplay.test_messenger_gate_blocks_albatross_reply` | 1181 | KEEP | - |
| `TestEntityIdBypassFix.test_entity_id_with_unknown_email_is_parked` | 1253 | KEEP | - |
| `TestRouteExecuteApprovalGate.test_reply_to_non_owner_email_is_blocked` | 1440 | REMOVE | A, F |
| `TestRouteExecuteApprovalGate.test_send_to_secondary_owner_email_is_allowed_cross_schema` | 1491 | REMOVE | A, F |
| `TestRouteExecuteApprovalGate.test_send_to_non_owner_without_rule_is_blocked` | 1539 | REMOVE | A, F |
| `TestRouteExecuteApprovalGate.test_reply_without_provider_thread_identity_fails_before_parking` | 1588 | REMOVE | A, F |
| `TestRouteExecuteTelegramApprovalGate.test_send_to_non_owner_telegram_is_blocked` | 1686 | REMOVE | A, F |
| `TestRouteExecuteTelegramApprovalGate.test_send_to_owner_telegram_is_allowed` | 1745 | REMOVE | A, F |
| `TestRouteExecuteTelegramApprovalGate.test_reply_fallback_send_to_non_owner_is_gated` | 1780 | REMOVE | A, F |
| `TestRouteExecuteTelegramApprovalGate.test_reply_to_non_owner_telegram_parks_native_reply_command` | 1868 | REMOVE | A, F |
| `TestRouteExecuteWhatsAppApprovalGate.test_owner_send_respects_disabled_runtime_policy` | 1915 | REMOVE | A, F |
| `TestRouteExecuteWhatsAppApprovalGate.test_send_to_non_owner_parks_native_send_command` | 1946 | REMOVE | A, F |
| `TestRouteExecuteWhatsAppApprovalGate.test_reply_parks_current_native_send_semantics` | 1982 | REMOVE | A, F |
| `TestApprovalRequestDelivery.test_telegram_owner_push_has_decision_keyboard` | 2047 | KEEP | - |
| `TestApprovalRequestDelivery.test_non_owner_approval_target_is_rejected_before_delivery` | 2096 | KEEP | - |
| `TestApprovalRequestDelivery.test_email_fallback_preserves_summary_and_dashboard_link` | 2126 | KEEP | - |
| `TestApprovalRequestDelivery.test_whatsapp_fallback_preserves_summary_and_dashboard_link` | 2158 | KEEP | - |
| `TestDBSIncidentReplay.test_dbs_reply_via_notify_entity_id_is_blocked` | 2210 | KEEP | - |
| `TestDBSIncidentReplay.test_dbs_reply_via_route_execute_is_blocked` | 2253 | REMOVE | A, F |
| `test_registered_messenger_park_has_durable_exact_envelope_and_once_replay` | 2304 | KEEP | - |

#### `tests/core/test_core_spawner.py`

20 REMOVE, 55 KEEP. KEEP the engineering-executor launcher cases.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `test_append_runtime_session_query` | 58 | KEEP | - |
| `test_merge_tool_call_records` | 69 | KEEP | - |
| `test_merge_tool_call_records_dedup_by_id` | 114 | KEEP | - |
| `test_catalog_codex_adapter_keeps_explicit_authority_despite_generic_factory_kwargs` | 416 | KEEP | - |
| `TestSpawnerResult.test_result_fields` | 474 | KEEP | - |
| `TestSpawnerInvocation.test_success_result_tool_calls_and_duration` | 509 | KEEP | - |
| `TestSpawnerInvocation.test_runtime_args_forwarded_to_runtime_invoke` | 542 | KEEP | - |
| `TestSpawnerInvocation.test_session_timeout_forwarded_to_runtime_invoke` | 620 | KEEP | - |
| `TestSpawnerInvocation.test_timeout_override_takes_precedence` | 671 | KEEP | - |
| `TestSpawnerInvocation.test_declared_adapter_setup_allowance_is_outside_runtime_timeout` | 719 | KEEP | - |
| `TestSpawnerInvocation.test_adapter_timeout_error_is_not_masked_by_spawner_guard` | 747 | KEEP | - |
| `TestSpawnerInvocation.test_error_wrapping_and_reset_behavior` | 799 | KEEP | - |
| `TestSpawnerInvocation.test_pre_spawn_mcp_warmup_runs_once_per_endpoint` | 834 | REMOVE | F, R |
| `TestSpawnerInvocation.test_pre_spawn_mcp_warmup_failure_does_not_block_session` | 865 | REMOVE | F, R |
| `TestSpawnerInvocation.test_terminal_codex_mcp_discovery_failure_marks_session_failed` | 896 | KEEP | - |
| `TestSpawnerInvocation.test_runtime_worker_factory_used_per_trigger` | 978 | KEEP | - |
| `TestSpawnerInvocation.test_session_timeout_and_semaphore_release` | 995 | KEEP | - |
| `TestSpawnSystemPromptResolution.test_next_session_uses_on_disk_prompt_when_no_history` | 1033 | REMOVE | F, R |
| `TestSpawnSystemPromptResolution.test_next_session_uses_edited_prompt_from_history` | 1048 | REMOVE | F, R |
| `TestFetchSystemPromptOverride.test_returns_head_prompt` | 1076 | REMOVE | F, R |
| `TestFetchSystemPromptOverride.test_none_pool_returns_none` | 1083 | REMOVE | F, R |
| `TestFetchSystemPromptOverride.test_blank_prompt_treated_as_absent` | 1088 | REMOVE | F, R |
| `TestFetchSystemPromptOverride.test_missing_table_fails_open` | 1095 | REMOVE | F, R |
| `TestCredentialPassthrough.test_env_path_passthrough_and_optional_exclusion` | 1121 | KEEP | - |
| `TestCredentialPassthrough.test_db_credential_resolution` | 1195 | KEEP | - |
| `TestCredentialPassthrough.test_env_and_db_creds_passed_to_adapter` | 1237 | KEEP | - |
| `TestSerialDispatch.test_concurrent_triggers_are_serialized` | 1273 | KEEP | - |
| `TestSerialDispatch.test_semaphore_released_on_error` | 1303 | KEEP | - |
| `TestSerialDispatch.test_trigger_source_rejected_while_semaphore_full` | 1328 | KEEP | - |
| `TestSemaphoreConcurrencyPool.test_n3_allows_three_concurrent_sessions` | 1363 | KEEP | - |
| `TestSemaphoreConcurrencyPool.test_self_trigger_guard_with_free_and_full_n3_slots` | 1408 | KEEP | - |
| `TestSemaphoreConcurrencyPool.test_drain_handles_multiple_concurrent_sessions` | 1442 | KEEP | - |
| `TestSemaphoreConcurrencyPool.test_semaphore_slot_count_and_queue_backpressure` | 1472 | KEEP | - |
| `TestSessionLogging.test_session_logging_on_success_and_error` | 1518 | KEEP | - |
| `TestSessionLogging.test_session_create_failure_preserves_original_error` | 1601 | KEEP | - |
| `TestSessionLogging.test_no_session_logging_without_pool` | 1633 | KEEP | - |
| `TestModelPassthrough.test_model_passed_to_invoke_and_result` | 1673 | KEEP | - |
| `TestFullFlow.test_full_trigger_flow` | 1709 | KEEP | - |
| `TestParametrizedOrchestration.test_trigger_returns_spawner_result` | 1811 | KEEP | - |
| `TestToolOutcomePersistence.test_session_complete_persists_failed_then_retried_tool_outcomes` | 1834 | KEEP | - |
| `TestTokenUsageCapture.test_token_counts_present_absent_and_partial` | 1902 | KEEP | - |
| `TestTokenUsageCapture.test_token_counts_on_error_and_sequence` | 1966 | KEEP | - |
| `TestTokenUsageCapture.test_token_counts_passed_to_session_complete` | 1996 | KEEP | - |
| `TestCatalogModelResolution.test_catalog_selection_and_empty_catalog_refusal` | 2037 | KEEP | - |
| `TestCatalogModelResolution.test_image_route_without_vision_candidate_fails_before_runtime` | 2113 | REMOVE | F, R |
| `TestCatalogModelResolution.test_pool_free_image_route_requires_proven_direct_runtime_vision` | 2176 | REMOVE | F, R |
| `TestCatalogModelResolution.test_spend_rule_breaker_override_cannot_bypass_original_vision_fit` | 2202 | KEEP | - |
| `TestCatalogModelResolution.test_private_routing_context_uses_normal_catalog_selection` | 2299 | REMOVE | F, R |
| `TestCatalogModelResolution.test_private_routing_context_refuses_live_catalog_miss` | 2351 | REMOVE | F, R |
| `TestCatalogModelResolution.test_complexity_routing` | 2385 | REMOVE | F, R |
| `TestCatalogModelResolution.test_extra_args_merging` | 2442 | KEEP | - |
| `TestCatalogModelResolution.test_catalog_error_and_unknown_runtime_fail_before_invocation` | 2543 | KEEP | - |
| `TestCatalogModelResolution.test_audit_log_resolution_metadata` | 2594 | KEEP | - |
| `TestGlobalSpawnConcurrencyCap.test_global_cap_limits_concurrent_sessions_across_spawners` | 2691 | KEEP | - |
| `TestGlobalSpawnConcurrencyCap.test_global_cap_env_var_and_semaphore_release` | 2749 | KEEP | - |
| `TestGlobalSpawnConcurrencyCap.test_global_cap_queuing_logged_at_info` | 2788 | KEEP | - |
| `TestGlobalSpawnConcurrencyCap.test_per_butler_semaphore_still_enforced_with_global_cap` | 2815 | KEEP | - |
| `TestSpawnerCwdAndBypassSemaphore.test_cwd_override_and_default` | 2858 | KEEP | - |
| `TestSpawnerCwdAndBypassSemaphore.test_bypass_and_normal_butler_semaphore_behavior` | 2884 | KEEP | - |
| `TestIngestionEventIdPropagation.test_trigger_ingestion_event_id_forwarding` | 2949 | REMOVE | F, R |
| `TestSpendEventBusWiring.test_spend_event_published_with_correct_payload` | 3007 | KEEP | - |
| `TestSpendEventBusWiring.test_spend_event_preserves_unknown_model_cost_as_unpriced` | 3078 | KEEP | - |
| `TestSpendEventBusWiring.test_spend_event_not_published_when_no_token_usage` | 3133 | KEEP | - |
| `TestSpendEventBusWiring.test_spend_event_publish_failure_does_not_break_session` | 3166 | KEEP | - |
| `TestCancelSession.test_unknown_session_id_is_benign_noop` | 3230 | KEEP | - |
| `TestCancelSession.test_cancel_kills_running_invocation_and_marks_ledger` | 3237 | KEEP | - |
| `TestCancelSession.test_cancel_after_completion_is_benign_noop` | 3288 | KEEP | - |
| `TestCancelSession.test_cancel_before_invoke_task_registered_skips_invocation` | 3314 | KEEP | - |
| `TestCancelSession.test_dashboard_route_lease_loss_cancels_runtime_without_terminal_completion` | 3380 | REMOVE | F, R |
| `TestCancelSession.test_dashboard_control_gate_blocks_runtime_before_invoke` | 3480 | REMOVE | F, R |
| `TestCancelSession.test_dashboard_registration_stop_returns_cancelled_result` | 3538 | REMOVE | F, R |
| `TestCancelSession.test_dashboard_stop_waits_through_claim_invoke_before_task_registration` | 3588 | REMOVE | F, R |
| `TestCancelSession.test_dashboard_preinvoke_stop_waits_for_cancelled_completion` | 3688 | REMOVE | F, R |
| `TestCancelSession.test_dashboard_control_releases_each_runtime_attempt_before_completion` | 3777 | REMOVE | F, R |
| `TestCancelSession.test_drain_forced_cancel_not_swallowed_by_overlapping_owner_cancel` | 3832 | KEEP | - |

#### `tests/contracts/test_mcp_only_inter_butler.py`

3 REMOVE, 13 KEEP. KEEP the isolation rules.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `TestMcpOnlyInterButler.test_module_abc_has_no_direct_butler_references` | 23 | KEEP | - |
| `TestMcpOnlyInterButler.test_identity_module_uses_shared_public_schema_only` | 40 | KEEP | - |
| `TestMcpOnlyInterButler.test_switchboard_is_the_only_inter_butler_channel` | 63 | KEEP | - |
| `TestMcpOnlyInterButler.test_no_direct_cross_butler_schema_queries_in_core` | 76 | KEEP | - |
| `TestMcpOnlyInterButler.test_core_notify_tool_routes_through_switchboard` | 107 | KEEP | - |
| `TestMcpOnlyInterButler.test_ephemeral_mcp_config_restricts_to_own_butler` | 144 | REMOVE | A, F |
| `TestMcpOnlyInterButler.test_module_register_tools_signature_has_no_cross_butler_params` | 174 | KEEP | - |
| `TestMcpOnlyInterButler.test_roster_modules_do_not_import_other_roster_modules` | 195 | KEEP | - |
| `TestMcpOnlyInterButler.test_context_bus_write_permission_enforced_at_runtime` | 266 | KEEP | - |
| `TestMcpOnlyInterButler.test_context_bus_uses_shared_public_table_api` | 285 | KEEP | - |
| `TestMcpOnlyInterButler.test_insight_candidates_submitted_via_mcp_tool` | 313 | KEEP | - |
| `TestMcpOnlyInterButler.test_briefing_exception_is_read_only_view` | 326 | KEEP | - |
| `TestMcpOnlyInterButler.test_no_direct_butler_to_butler_function_calls_in_registry` | 340 | KEEP | - |
| `TestMcpOnlyInterButler.test_switchboard_heartbeat_is_separate_from_inter_butler_data_flow` | 388 | KEEP | - |
| `TestMcpOnlyInterButler.test_switchboard_router_classifies_then_dispatches` | 401 | REMOVE | A, F |
| `TestMcpOnlyInterButler.test_route_execute_is_the_only_cross_butler_dispatch_mechanism` | 430 | REMOVE | A, F |

#### `roster/switchboard/tests/test_route_transport_outcomes.py`

16 REMOVE, 30 KEEP. KEEP the deliver and receipt cases.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `TestTransportVocabulary.test_only_not_attempted_is_retryable` | 66 | KEEP | - |
| `TestTransportVocabulary.test_evidence_pairs_outside_the_database_allowlist_are_rejected` | 77 | KEEP | - |
| `TestTransportVocabulary.test_confirmed_transport_carries_no_failure_evidence` | 85 | KEEP | - |
| `TestTransportVocabulary.test_failure_outcome_requires_evidence` | 93 | KEEP | - |
| `TestTransportVocabulary.test_refused_connect_proves_not_attempted_but_reset_does_not` | 97 | KEEP | - |
| `TestTransportVocabulary.test_fastmcp_connect_wrapper_is_unwrapped` | 108 | KEEP | - |
| `TestTransportVocabulary.test_unknown_exception_classifies_as_uncertain` | 115 | KEEP | - |
| `TestTransportVocabulary.test_peer_error_and_timeout_are_distinct` | 120 | KEEP | - |
| `TestTransportVocabulary.test_envelope_roundtrip` | 131 | KEEP | - |
| `TestTransportVocabulary.test_legacy_envelope_has_no_transport_classification` | 142 | KEEP | - |
| `TestRouteTransportClassification.test_default_route_retains_legacy_authority_without_cutover` | 152 | REMOVE | R, A |
| `TestRouteTransportClassification.test_flagged_stale_route_rechecks_once_without_policy_bypass` | 171 | REMOVE | R, A |
| `TestRouteTransportClassification.test_internal_context_folds_into_route_envelope_without_target_keyword` | 289 | REMOVE | R, A |
| `TestRouteTransportClassification.test_internal_context_preserves_existing_string_input_context` | 338 | REMOVE | R, A |
| `TestRouteTransportClassification.test_target_failure_is_content_blind` | 365 | REMOVE | R, A |
| `TestRouteTransportClassification.test_unrelated_target_failure_preserves_existing_error_envelope` | 386 | REMOVE | R, A |
| `TestRouteTransportClassification.test_success_reports_confirmed` | 399 | REMOVE | R, A |
| `TestRouteTransportClassification.test_unresolvable_target_is_proven_not_attempted` | 412 | REMOVE | R, A |
| `TestRouteTransportClassification.test_rejected_wake_callback_is_policy_denied` | 425 | REMOVE | R, A |
| `TestRouteTransportClassification.test_timeout_is_uncertain_not_retryable_transport` | 438 | REMOVE | R, A |
| `TestRouteTransportClassification.test_refused_connect_is_not_attempted` | 451 | REMOVE | R, A |
| `TestRouteTransportClassification.test_peer_error_is_rejected_not_uncertain` | 462 | REMOVE | R, A |
| `TestRouteTransportClassification.test_legacy_retryable_field_is_preserved` | 474 | REMOVE | R, A |
| `TestRoutePostSendBookkeeping.test_confirmed_send_survives_bookkeeping_acl_failure` | 492 | REMOVE | R, A |
| `TestRoutePostSendBookkeeping.test_bookkeeping_failure_logs_no_raw_provider_error` | 527 | REMOVE | R, A |
| `TestRoutePostSendBookkeeping.test_pre_send_failure_still_logs_routing` | 547 | REMOVE | R, A |
| `TestDeliverPostSendBookkeeping.test_null_recovery_uses_generic_delivery` | 628 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_nested_route_application_error_is_never_reported_sent` | 656 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_nested_timeout_without_transport_remains_uncertain` | 693 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_sent_requires_complete_matching_messenger_receipt` | 754 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_flagged_recovery_admits_only_ready_sender_without_generic_effects` | 793 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_flagged_recovery_fails_closed_when_receiver_lookup_fails` | 863 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_recovery_uses_trusted_internal_context_and_bypasses_generic_records` | 914 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_recovery_rejects_untrusted_or_malformed_source_before_observability` | 961 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_confirmed_delivery_survives_notification_log_failure` | 1015 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_confirmed_delivery_survives_message_inbox_failure` | 1036 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_uncertain_route_failure_is_not_reported_as_retryable` | 1055 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_pre_send_failure_stays_distinguishable` | 1081 | KEEP | - |
| `TestDeliverPostSendBookkeeping.test_messenger_business_error_is_provider_rejected` | 1107 | KEEP | - |
| `TestCallButlerToolExceptionTypes.test_peer_error_raises_transport_rejected` | 1148 | KEEP | - |
| `TestCallButlerToolExceptionTypes.test_empty_content_raises_transport_uncertain` | 1166 | KEEP | - |
| `TestCallButlerToolExceptionTypes.test_double_connect_failure_raises_transport_not_attempted` | 1181 | KEEP | - |
| `TestCallButlerToolExceptionTypes.test_ambiguous_failure_stays_plain_connection_error` | 1194 | KEEP | - |
| `TestNoEventLoopStarvation.test_cancellation_during_bookkeeping_still_propagates` | 1213 | KEEP | - |
| `test_public_notify_refuses_link_claims_and_raw_urls` | 1224 | KEEP | - |
| `test_telegram_effect_reconnect_requires_pre_transport_proof` | 1271 | KEEP | - |

#### `tests/core/test_spawner_guardrails.py`

15 REMOVE, 35 KEEP. KEEP the generic budget cases.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `test_purpose_lane_uses_trusted_channel_metadata_only` | 68 | REMOVE | R, F |
| `TestCheckDegenerateToolLoop.test_no_calls_returns_none` | 105 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_single_call_returns_none` | 108 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_below_threshold_returns_none` | 111 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_at_threshold_triggers` | 115 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_above_threshold_triggers` | 122 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_non_consecutive_identical_does_not_trigger` | 128 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_streak_reset_by_different_call` | 137 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_custom_threshold_respected` | 148 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_zero_threshold_disables` | 153 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_different_inputs_are_distinct` | 158 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_same_name_same_input_triggers` | 170 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_input_fingerprint_distinguishes_hidden_inputs` | 180 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_same_input_fingerprint_triggers` | 194 | KEEP | - |
| `TestCheckDegenerateToolLoop.test_loop_mid_session_reports_looping_tool_name` | 208 | KEEP | - |
| `TestCheckToolCallBudget.test_disabled_when_zero` | 235 | KEEP | - |
| `TestCheckToolCallBudget.test_not_exceeded_returns_none` | 238 | KEEP | - |
| `TestCheckToolCallBudget.test_at_limit_returns_none` | 242 | KEEP | - |
| `TestCheckToolCallBudget.test_exceeded_returns_reason` | 247 | KEEP | - |
| `TestCheckToolCallBudget.test_single_call_exceeds_budget_of_zero` | 255 | KEEP | - |
| `TestCheckToolCallBudget.test_negative_max_treated_as_disabled` | 260 | KEEP | - |
| `TestCheckToolCallBudget.test_empty_calls_not_exceeded` | 264 | KEEP | - |
| `TestCheckTokenBudget.test_none_budget_returns_none` | 276 | KEEP | - |
| `TestCheckTokenBudget.test_none_tokens_returns_none` | 279 | KEEP | - |
| `TestCheckTokenBudget.test_within_budget_returns_none` | 282 | KEEP | - |
| `TestCheckTokenBudget.test_at_budget_returns_none` | 285 | KEEP | - |
| `TestCheckTokenBudget.test_exceeded_returns_reason` | 289 | KEEP | - |
| `TestCheckTokenBudget.test_both_none_returns_none` | 296 | KEEP | - |
| `TestCheckTokenBudget.test_zero_tokens_within_budget` | 299 | KEEP | - |
| `TestGuardrailClassifierGate.test_guardrail_message_suppresses_failover` | 319 | KEEP | - |
| `TestGuardrailClassifierGate.test_guardrail_with_tool_calls_also_suppressed` | 330 | KEEP | - |
| `TestSpawnerGuardrailEmission.test_degenerate_tool_loop_raises` | 421 | KEEP | - |
| `TestSpawnerGuardrailEmission.test_tool_call_budget_exceeded_raises` | 466 | KEEP | - |
| `TestSpawnerGuardrailEmission.test_token_budget_exceeded_raises` | 513 | KEEP | - |
| `TestSpawnerGuardrailEmission.test_no_guardrail_below_thresholds` | 559 | KEEP | - |
| `TestSpawnerGuardrailEmission.test_guardrail_suppresses_failover_end_to_end` | 604 | KEEP | - |
| `TestUndeliveredInteractiveReply.test_attempted_but_all_failed_is_flagged` | 690 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_notify_status_error_is_flagged` | 703 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_notify_outcome_error_is_flagged` | 707 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_delivered_ok_is_not_flagged` | 711 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_deferred_counts_as_delivered` | 715 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_mixed_attempts_with_one_success_is_not_flagged` | 720 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_no_notify_attempt_is_not_flagged` | 727 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_prefixed_notify_name_is_detected` | 732 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_non_route_trigger_is_ignored` | 737 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_non_interactive_channel_is_ignored` | 741 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_missing_routing_context_is_ignored` | 746 | REMOVE | R, F |
| `TestUndeliveredInteractiveReply.test_source_channel_from_source_metadata_fallback` | 750 | REMOVE | R, F |
| `TestSpawnerUndeliveredReplyAccounting.test_route_reply_with_failed_notify_marks_session_failed` | 763 | REMOVE | R, F |
| `TestSpawnerUndeliveredReplyAccounting.test_route_reply_with_delivered_notify_marks_session_success` | 823 | REMOVE | R, F |

#### `tests/contracts/test_staffer_routing_exclusion.py`

4 REMOVE, 9 KEEP.

| Test | Line | Disposition | Account |
| --- | --- | --- | --- |
| `TestStafferVsButlerDistinction.test_two_agent_types_defined` | 21 | KEEP | - |
| `TestStafferVsButlerDistinction.test_staffers_have_infrastructure_domain` | 31 | KEEP | - |
| `TestStafferVsButlerDistinction.test_staffers_excluded_from_user_message_routing` | 44 | REMOVE | A |
| `TestStafferVsButlerDistinction.test_staffers_are_still_reachable` | 55 | KEEP | - |
| `TestStafferVsButlerDistinction.test_butler_to_staffer_routing_is_unaffected` | 70 | REMOVE | A |
| `TestStafferVsButlerDistinction.test_dispatcher_routes_to_named_target_regardless_of_type` | 87 | REMOVE | A |
| `TestStafferVsButlerDistinction.test_staffers_excluded_from_briefing_contribution` | 113 | KEEP | - |
| `TestStafferVsButlerDistinction.test_staffer_identity_defined_by_infrastructure_contract` | 137 | KEEP | - |
| `TestStafferVsButlerDistinction.test_butler_identity_defined_by_manifesto` | 156 | KEEP | - |
| `TestStafferTypeInRegistry.test_staffer_type_field_is_required_for_exclusion` | 177 | KEEP | - |
| `TestStafferTypeInRegistry.test_user_message_routing_only_includes_domain_butlers` | 197 | REMOVE | A |
| `TestStafferTypeInRegistry.test_switchboard_registration_advertise_true` | 221 | KEEP | - |
| `TestStafferTypeInRegistry.test_notify_tool_reaches_messenger_regardless_of_type` | 239 | KEEP | - |

### 4.3 Other test files that reference a deleted module

30 more test files import or patch a section 1.1 module. They are not whole-file deletions: the release repoints or removes only the referencing tests, giving each removed test an account from the list above. Migration and history tests stay. `tests/integration/test_pipeline_ingress_partition_durability.py` carries the partition-durability invariant (R): repoint it at the surviving ingest/buffer path rather than delete it.

| Path | Tests | References |
| --- | --- | --- |
| `roster/concierge/tests/test_dashboard_read.py` | 10 | `butlers.core.delegation_ledger` |
| `roster/finance/tests/test_jobs.py` | 77 | `butlers.tools.switchboard.routing.route` |
| `roster/switchboard/tests/test_contract_models.py` | 44 | `butlers.tools.switchboard.routing.contracts` |
| `roster/switchboard/tests/test_deliver_unit.py` | 56 | `butlers.tools.switchboard.routing.route` |
| `roster/switchboard/tests/test_identity_mint_concurrency_integration.py` | 1 | `butlers.core.routing_context`, `butlers.modules.pipeline`, `butlers.tools.switchboard.routing.classify` |
| `roster/switchboard/tests/test_spotify_spoken_policy_bypass.py` | 3 | `butlers.modules.pipeline` |
| `roster/switchboard/tests/test_tools.py` | 83 | `butlers.tools.switchboard.routing.route` |
| `tests/core/test_dashboard_channel_extensions.py` | 4 | `butlers.tools.switchboard.routing.contracts` |
| `tests/core/test_dsa4_validation.py` | 13 | `butlers.tools.switchboard.triage.thread_affinity` |
| `tests/core/test_engagement_detection.py` | 6 | `butlers.modules.pipeline`, `butlers.tools.switchboard.routing.classify` |
| `tests/core/test_finance_warning_control.py` | 17 | `butlers.tools.switchboard.routing.route` |
| `tests/core/test_liveness.py` | 27 | `butlers.tools.switchboard.routing.preflight` |
| `tests/core/test_routing_contracts.py` | 11 | `butlers.tools.switchboard.routing.contracts` |
| `tests/core_tools/test_domain_events.py` | 50 | `butlers.tools.switchboard.routing`, `butlers.tools.switchboard.routing.route` |
| `tests/daemon/test_ingest_reaction_lifecycle.py` | 2 | `butlers.modules.pipeline` |
| `tests/daemon/test_switchboard_identity_wiring.py` | 3 | `butlers.modules.pipeline` |
| `tests/e2e/test_cross_butler.py` | 8 | `butlers.tools.switchboard.routing.route` |
| `tests/integration/test_approval_push_on_park.py` | 18 | `butlers.modules.pipeline` |
| `tests/integration/test_calendar_response.py` | 1 | `butlers.tools.switchboard.routing.route` |
| `tests/integration/test_conversation_history_db.py` | 9 | `butlers.modules.pipeline` |
| `tests/integration/test_dashboard_conversation_lineage.py` | 3 | `butlers.modules.pipeline`, `butlers.tools.switchboard.routing.route` |
| `tests/integration/test_domain_event_bus_roundtrip.py` | 28 | `butlers.tools.switchboard.routing`, `butlers.tools.switchboard.routing.route` |
| `tests/integration/test_file_bug_report_qa_relay_db.py` | 2 | `butlers.core.routing_context`, `butlers.tools.switchboard.routing.route` |
| `tests/integration/test_infra_state_views_migration.py` | 12 | `butlers.tools.switchboard.routing.route` |
| `tests/integration/test_pipeline_ingress_partition_durability.py` | 1 | `butlers.modules.pipeline` |
| `tests/integration/test_switchboard_routing_verdict_log_migration.py` | 12 | `butlers.tools.switchboard.routing.verdict_log` |
| `tests/integration/test_switchboard_spot_check_index_migration.py` | 6 | `butlers.tools.switchboard.routing.rule_demotion`, `butlers.tools.switchboard.routing.verdict_log` |
| `tests/modules/test_contacts_owner.py` | 6 | `butlers.tools.switchboard.routing.route` |
| `tests/modules/test_conversation_history.py` | 9 | `butlers.modules.pipeline` |
| `tests/modules/test_pipeline_dedupe_scrub.py` | 5 | `butlers.modules.pipeline` |

### 4.4 Frontend tests

These test files cover the section 3.1 surfaces. A test file goes with its component; KEEP components keep their tests. Accessibility, keyboard, error-truth and domain-data assertions move to the D19 page tests (bu-vvsfi8) rather than vanish; layout-only assertions carry no invariant (N).

| Path | Disposition |
| --- | --- |
| `frontend/src/components/butler-detail/ButlerActivityTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerApprovalsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerChroniclerTimelinesTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerConfigTab.test.tsx` | KEEP |
| `frontend/src/components/butler-detail/ButlerDelegationsPanel.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerDetailActions.pause-undo.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerDetailActions.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerDetailHeader.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerDomainEventsPanel.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerEducationReviewsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerFrictionPanel.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerHealthMeasurementsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerLogsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerManagementTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerMcpTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerMemoryTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerModelOverridesTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerOverviewTab.cost-sessions.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerOverviewTab.process-facts.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerOverviewTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerQaInvestigationsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerRegistryTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerRelationshipContactsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerRoutingLogTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerSchedulesTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerSessionsTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerSkillsTab.test.tsx` | KEEP |
| `frontend/src/components/butler-detail/ButlerSpendTab.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/ButlerVerdictOpener.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/GoogleHealthStatusCard.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/butler-detail/SiblingButlerNav.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ChatDock.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ChatPanel.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ChatRecallCommands.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ContextChip.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ConversationHeader.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ConversationList.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/ConversationReadError.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/FloatingChatWidget.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/MessageInput.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/MessageThread.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/answer/AnswerBody.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/answer/CitationRow.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/answer/markdown.test.ts` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/live-announcer.test.ts` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/message-id.test.ts` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/message-reconciliation.test.ts` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/send-error-utils.test.ts` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/chat/send-error.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionDetailDrawer.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionDossier.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionTable.keyboard.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionTable.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionsKpiStrip.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionsPinnedStrip.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/SessionsVerdictOpener.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/StatusBadge.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/components/sessions/ToolCallTimeline.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/ButlerDetailPage.a11y.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/ButlerDetailPage.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/ButlersPage.a11y.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/ButlersPage.interaction.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/ButlersPage.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/ChatPage.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/SessionDetailPage.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |
| `frontend/src/pages/SessionsPage.test.tsx` | REMOVE (A/F/R assertions move to D19 pages; layout N) |

### 4.5 Kept history tests

The tables stay as history and no destructive migration runs, so these migration tests stay:

- `tests/integration/test_switchboard_routing_verdict_log_migration.py`
- `tests/integration/test_switchboard_rule_promotion_suggestions_migration.py`

### 4.6 Corrected test estimate

bu-enbrs2's "-80" is several times too low. At the census commit the floor is **763 declared Python tests** (689 in 43 whole files plus 74 in the mixed files, which keep 203), before parametrization, before the per-test removals in the 30 section 4.3 files, and before the 66 frontend test files. Treat about -800 Python tests plus the frontend files as the planning number; the release PR reports the collected delta.

## 5. `public.permissions`

Owner decision bu-i661vs = A (2026-10-21): the switch release retires the matrix editor, its PUT API and the remaining effect gates. The audit reel, data operations and webhooks stay. The table stays, read-only, as history. No table drop or data deletion.

| Path::symbols | Disposition | Scope |
| --- | --- | --- |
| `src/butlers/api/routers/permissions.py::set_permission` | REMOVE | `PUT /{butler}/{perm}`. The `GET` matrix read stays read-only. |
| `frontend/src/pages/SettingsPermissionsPage.tsx` | REPLACE | Matrix editor becomes a read-only view; drop the edit controls. |
| `src/butlers/core_tools/_notifications.py::NOTIFY_PERMISSION; check_permission` | REMOVE | notify gate; broker grants replace it (D7). |
| `src/butlers/modules/telegram.py::NOTIFY_PERMISSION; require_permission` | REMOVE | notify gates. |
| `src/butlers/modules/telegram_confirms.py::NOTIFY_PERMISSION; require_permission` | REMOVE | notify gate. |
| `src/butlers/core_tools/_switchboard.py::CROSS_BUTLER_PERMISSION; check_permission` | REMOVE | Cross-butler routing gate; dies with `route_to_butler` (1.3). |
| `src/butlers/core_tools/_dashboard_system_answer.py::CROSS_BUTLER_PERMISSION; check_permission` | REMOVE | Cross-butler routing gate. |
| `roster/travel/tools/health.py::CROSS_BUTLER_PERMISSION; check_permission` | REMOVE | Cross-butler routing gate. |
| `src/butlers/modules/email.py::EMAIL_SEND_PERMISSION; require_permission` | REMOVE | `email.send` gate; broker grant and recipient approval replace it. |
| `src/butlers/modules/calendar/mutations.py::CALENDAR_WRITE_PERMISSION; require_permission` | REMOVE | `calendar.write` gate. |
| `src/butlers/core/spawner.py::SPAWN_PERMISSION; check_permission` | OPEN | Spawn gate. bu-i661vs names notify, cross-butler routing, `email.send` and `calendar.write`, not spawn. The domain sessions it gates die (D18); whether the surviving `qa` launcher keeps a check against the frozen table needs an owner answer before the release. |
| `src/butlers/core/permissions.py::check_permission; require_permission` | REMOVE | Effect-gate helpers once no caller remains; keep any constant the read view still needs. |
| `src/butlers/api/routers/data_ops.py::public.permissions` | KEEP | Data operations export. |
| `src/butlers/core/tool_presentation_inventory.py::public.permissions` | KEEP | Presentation inventory entries; update their wording to read-only history. |

## 6. Spawner fate

After the switch the Spawner survives only as the engineering-executor launcher (`qa`), as the core-spawner delta says (`openspec/changes/generalist-lead-focused-delegation/specs/core-spawner/spec.md`, Requirement: Trigger Source Tracking). Goal invocations start only from the goal runner (bu-u5s4w8); engineering executors are not goal invocations. The shared model-call kernel from bu-m0vjeb.1 has landed as `src/butlers/core/model_call.py`; the release moves the surviving launcher onto it where the contracts match.

This change drops `healing` from the delta's surviving sources: bu-dss7he.1 retired the healing dispatcher (PR #5017), and the canonical spec keeps only historical `healing` provenance.

| Path | Disposition |
| --- | --- |
| `src/butlers/core/spawner.py` | KEEP as the engineering-executor launcher. REMOVE the domain prompt, route and trigger paths listed above. The spawn permission gate is OPEN (section 5). |
| `src/butlers/core/spawner_env.py` | KEEP: explicit executor environment isolation. |
| `src/butlers/core/runtimes/base.py` | KEEP: adapter interface. |

Bead notes this inventory implies (the coordinator appends them):

- bu-kiuh8v: its non-goal says the Spawner is "deleted at the switch". The delta keeps it as the engineering-executor launcher; only domain spawning dies.
- bu-u5s4w8: the runner is the only thing that starts goal invocations, not every session. Engineering executors still start from the Spawner.
- bu-enbrs2: link this inventory; replace the "-80" estimate with section 4.6; AC2 must scan the section 1.5 module paths, not only tool names.
- bu-2ckast: link this inventory.
- bu-vbdug9.2: deletion criterion for the consolidation spawn branch (section 2).
- bu-2c1g7v: the calendar fence call site and the section 1.4 caller list.
- bu-svqpzo.14: its `structured_classify` and insight targets die at the switch.
- bu-mmax8j.1/.2/.3/.5: superseded per `openspec/changes/generalist-lead-focused-delegation/collision-dispositions.md` row 42.
- bu-60pwv6.11: needs bu-k2z6iw's design (`openspec/changes/dashboard-one-mind-goal-surface/design.md`).

## Verifying this inventory

Every backticked repository path must exist at the census commit:

```bash
grep -o '`[^`]*`' openspec/changes/generalist-lead-focused-delegation/deletion-inventory.md | tr -d '`' | sed 's/::.*//' \
  | grep -E '^(src|roster|tests|frontend|docs|about|openspec|alembic)/' | sort -u \
  | while read -r p; do git cat-file -e "348111b02846:$p" 2>/dev/null || echo "missing: $p"; done
```

No output means every path resolves. To refresh at the release, regenerate the tables against the release branch and diff them against this file.
