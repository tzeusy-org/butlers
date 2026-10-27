# Shape condensation: evidence tables (2026-10-03)

**Reader:** someone executing or reviewing the [condensation plan](2026-10-03-shape-condensation-plan.md). **Status:** generated from five independent read-only reviews at baseline `252b77c76`; every row carries the reviewer label (Observed, Inferred, Unknown). Rows are inputs to owner decisions and OpenSpec changes, not adopted requirements.

## T1 Target spec list

| Family | Target spec | Absorbs | Purpose |
|---|---|---|---|
| Dashboard | dashboard-api | dashboard-api: Application Factory, Envelopes (cite RFC 0007), Error Middleware, DatabaseManager, Cross-Butler Fan-Out, Cross-Butler Search  | Shared dashboard API core: app, envelopes, pools, fan-out, discovery, proxy, SSE, pagination. About 35KB, down |
| Dashboard | dashboard-shell | dashboard-shell, dashboard-api: Dashboard Query Defaults and Refresh, dashboard-relationship: App-wide Cmd-K Finder (menu half), dashboard-c | Outer frame: navigation, one command menu, shortcuts, theme, time zone, refresh, route registry, chat dock slo |
| Dashboard | dashboard-design-language | dashboard-design-language, deleted restatements: ingestion Dispatch Visual Language, relationship token discipline, domain-pages Consistent  | Dispatch tokens, primitives, archetypes, copy and motion rules for every page. |
| Dashboard | dashboard-credentials | dashboard-admin-gateway, dashboard-api: OAuth Bootstrap Flow, Generic Secrets Management, Secrets Inventory, Spotify exclusion from generic  | The /secrets passport and its /api/secrets and /api/oauth contracts, plus frontend-gating. About 60KB after de |
| Dashboard | dashboard-calendar | dashboard-api: Calendar Workspace and the 19 Calendar*/Meeting-Prep/Day-Briefing/ICS/Dedup/Undo requirements, dashboard-domain-pages: 6 Cale | The /calendar workspace page and its API. About 55KB. Dedup Rules may move to module-calendar. |
| Dashboard | dashboard-memory | dashboard-api: Memory Endpoints, Consolidation Run Audit Table, Memory stats graph-health, Memory APIs Truthful Source Episode State, dashbo | The /memory house ledger, the fact/rule/episode detail pages, and their API. About 55KB. |
| Dashboard | dashboard-health | dashboard-domain-pages: Health read surfaces, Medications (x2), Conditions, Symptoms, Meals, Research, Health hooks, Health measurements pag | Health butler pages. About 32KB. |
| Dashboard | dashboard-entities | dashboard-relationship (minus Finder menu), dashboard-domain-pages: Relationship overdue surfaces | Entity index, Plex, Concentration, detail, curation, and the contact compatibility API. About 70KB. A further  |
| Dashboard | dashboard-sessions | dashboard-visibility: Session Explorer, Sessions Verdict, Session Table, Session Detail Drawer/Full Page, End-to-End Request Trace Story, Pi | Cross-butler session explorer and detail. About 28KB. |
| Dashboard | dashboard-timeline | dashboard-visibility: Unified Timeline and the 6 Timeline* requirements | The /timeline page. About 20KB. |
| Dashboard | dashboard-notifications | dashboard-visibility: Notification Audit Trail, Pending-approvals visibility, dashboard-api: Cross-Domain API Route Rules notification degra | Notification feed, retry and escalate. About 11KB. |
| Dashboard | dashboard-issues | dashboard-api: Issues Aggregation, Exact Audit-To-Issues Evidence Door, dashboard-visibility: Issues page presents grouped issues | The /issues page and its aggregation API. About 14KB. |
| Dashboard | dashboard-butler-fleet | dashboard-butler-management: Butler List Page, status-board chrome, Truthful Status-Board Summary, Canonical Cadence Labels, header schedule | The /butlers fleet board and topology. About 25KB. |
| Dashboard | dashboard-butler-detail | dashboard-butler-management: Tab Set, Domain Tabs, Command Bar, all tab requirements, prompt/tools/memory APIs, Dispatch Fold-In rewritten a | The /butlers/:name tabs and the per-butler control APIs. About 38KB. |
| Dashboard | dashboard-overview | dashboard-overview, dashboard-briefing | Home page plus the briefing endpoint, with one attention model. About 40KB. |
| Dashboard | dashboard-approvals | dashboard-approvals, dashboard-api: Approval metrics identify partial source families, approvals-degraded scenario | One Trust Console: queue, dossier, verbs, autonomy ledger and suggestions. |
| Dashboard | dashboard-conversations | dashboard-conversations, dashboard-chat-ui: Cross-Butler Conversation Lookup | Conversation persistence, turn control and the SSE wire protocol. |
| Dashboard | dashboard-chat-ui | dashboard-chat-ui (minus lookup API and palette recall) | Chat dock, popover and full-page UI. |
| Dashboard | dashboard-chronicles | dashboard-chronicles | The Chronicles lived-time page. |
| Dashboard | dashboard-ingestion | dashboard-ingestion-dispatch-console (minus Dispatch Visual Language and Visual and Route Verification), dashboard-connector-batch-settings, | The /ingestion timeline, connectors roster and detail, filters and replay. |
| Dashboard | dashboard-settings | dashboard-settings-console, dashboard-permissions, dashboard-model-settings (minus Routing Selection Contract and Hourly Sweep, which move t | The /settings console and its panels: permissions, models, data ops, webhooks. About 35KB. |
| Dashboard | dashboard-spend | dashboard-spend-dashboard, dashboard-api: Pricing and Cost Estimation | The /spend page, cost API, forecast and rules. |
| Dashboard | dashboard-decisions | dashboard-decisions, dashboard-bead-detail, dashboard-api: Decisions Digest Endpoint, Scheduled Decision-Convention Lint, Snapshot-backed Be | Read-only beads projection: /decisions and /beads/:id. About 17KB. |
| Dashboard | dashboard-education | dashboard-education-api, dashboard-education-ui | The education butler page and its API. About 40KB. |
| Dashboard | audit-log | dashboard-audit-log, dashboard-api: Audit Log Credential-Key Filter | Core public.audit_log primitive, append helper, read API and page. Leaves the dashboard-* family. |
| Platform | core-daemon | staffer-archetype (testable bits only) | Daemon config parse, type awareness, core tool surface, boot ordering. |
| Platform | core-modules |: | Module ABC, registry, dependency order, tool naming/egress rules. |
| Platform | core-scheduler |: | Cron dispatch, TOML sync, schedule CRUD. |
| Platform | core-state |: | Per-butler KV store with CAS. |
| Platform | core-sessions | session-process-logs, core-spawner#Spawner Session Lifecycle, core-spawner#Ingestion Event Propagation Through Trigger Pipeline | Session record lifecycle, lineage, process logs, corrections. |
| Platform | core-skills |: | CLAUDE.md/AGENTS.md/skill loading. |
| Platform | core-spawner |: | Trigger, MCP lockdown, concurrency, prompt composition, guardrails, drain (slimmed). |
| Platform | runtime-adapters | runtime-opencode, core-spawner#Multi-Runtime Adapter Support, core-spawner#Pre-Launch and Prewarm Codex Auth Synchronization, core-credentia | Per-adapter invocation, auth sync, usage reporting for claude/codex/gemini/opencode/api. |
| Platform | core-telemetry |: | Tracing/logging/metrics behavior; cites RFC 0005 for instrument catalog. |
| Platform | core-credentials |: | CredentialStore, validation, expiry derivation, lifecycle notifications (provider-specific flows moved out). |
| Platform | core-notify |: | notify() envelope, validation, routing, attention ledger (split later after active changes land). |
| Platform | runtime-config | runtime-config-table, runtime-config-api, runtime-config-dashboard-ui, core-daemon#Config loading parses runtime_seed section, core-daemon#B | Per-butler operational config: seed, reconcile, read, edit. |
| Platform | model-catalog | healing-model-tier | Catalog schema, resolution, fit, breaker, receipts. |
| Platform | model-failover | core-spawner#Runtime Failure Classification, core-spawner#Logical Session Attempt Orchestration | Same-tier failover eligibility, bounds and provenance. |
| Platform | catalog-token-limits |: | Token ledger, quotas, monthly ceiling pricing. |
| Platform | e2e-benchmark-harness | model-benchmark-harness, tool-call-scorecard, testing#E2E* requirements, testing#Smoke Test Tier and smoke tests | E2E staging harness, smoke tier, cross-model benchmark and scorecard. |
| Platform | cross-butler-briefing | cross-butler-briefing-aggregation, cross-butler-briefing-contribution | Daily contribution schema, aggregation view and job. |
| Platform | cross-butler-delegation | core-daemon#Delegation Core Tool Inventory And Admission Boundary, core-daemon#Delegation Inventory Does Not Activate Runtime Configuration | Delegation ledger, routing, wake, discoverability. |
| Platform | domain-event-bus | domain-event-delivery-recovery | Durable event log, subscriptions, delivery, replay. |
| Platform | ingestion-event-registry |: | public.ingestion_events ledger and rollups. |
| Platform | ingestion-policy |: | ingestion_rules model and evaluator (migration req dropped). |
| Platform | switchboard-rule-promotion |: | Verdict log, promotion, demotion. |
| Platform | switchboard-identity |: | Inbound sender resolution and identity propagation. |
| Platform | s3-blob-storage | core-daemon#Blob storage initialization at startup phase 8c, core-daemon#Removal of blob_storage_dir config | BlobStore behavior and startup semantics. |
| Platform | qa-staffer | staffer-qa, qa-log-scanner, qa-triage | Patrol loop, discovery sources, triage, patrol schema. |
| Platform | qa-investigation | qa-investigation-dispatch, healing-worktree, healing-anonymizer, healing-session-tracking, core-spawner#Healing Session Semaphore Bypass, co | Investigation dispatch, sandbox, worktree, anonymized PR egress, attempt ledger. |
| Platform | qa-dashboard |: | QA pages and APIs (candidate for A-dashboard). |
| Platform | owner-condition-ledger |: | Owner standing-concern ledger (Finance job moved to finance spec). |
| Platform | owner-timezone-context |: | Dashboard owner-timezone rendering (candidate for A-dashboard). |
| Platform | system-overview-page |: | /system ownership facts page (candidate for A-dashboard). |
| Butlers, | butler-contract | butler-base-spec | What a butler is: tool composition, module system, DB isolation, staffer vs domain, credential resolution, ins |
| Butlers, | butler-switchboard | butler-switchboard, butler-base-spec#Staffer: Switchboard | Routing eligibility, registry, misroute correction, non-interactive channel dispatch |
| Butlers, | butler-messenger | butler-messenger, butler-base-spec#Staffer: Messenger | Sole outbound channel execution plane |
| Butlers, | butler-concierge | butler-concierge, module-dashboard-read | Read-only system-plane staffer and its dashboard_read tools |
| Butlers, | butler-chronicler | butler-chronicler | Retrospective episode storage, attribution, projection rules |
| Butlers, | butler-general | butler-general | Collections/items behavior and EOD briefing format |
| Butlers, | butler-health | butler-health | Health facts, wellness ingest translation, medication and routes |
| Butlers, | butler-relationship | butler-relationship, passive-interaction-sync | Relationship facts, stale-contact producer, passive interaction detection |
| Butlers, | butler-finance | butler-finance | Finance tables, reconciliation, settlement integrity |
| Butlers, | butler-education | butler-education | Education butler behavior (education modules stay in their own cluster) |
| Butlers, | butler-travel | butler-travel | Trip/booking model, journey integrity, medication snapshot consumer |
| Butlers, | butler-home | butler-home | HA event response patterns and live-state cache |
| Butlers, | butler-lifestyle | butler-lifestyle | Lifestyle domain boundary, taste ledger honesty, briefing contribution |
| Butlers, | dashboard-secrets | butler-secrets | /secrets passport-book page (moves to dashboard cluster) |
| Butlers, | connector-contract | connector-base-spec (runtime obligations), connector-filtered-events, connector-replay-queue | Every connector's obligations: source filter gate, filtered-event persistence, replay, checkpoints, metrics, b |
| Butlers, | connector-discretion | connector-base-spec#Shared Discretion Layer..Identity-Based Discretion Weight | Shared LLM discretion layer: tier, weights, fail-open rule, suppression ledger |
| Butlers, | connector-registry | connector-base-spec#Heartbeat/Liveness/operational role/settings API/catalog/response models, connector-state-aggregates, infrastructure-rel | connector_registry rows, liveness derivation, settings and catalog API |
| Butlers, | connector-gmail | connector-gmail | Gmail mapping, tiering, labels, backfill |
| Butlers, | connector-google-calendar | connector-google-calendar | Calendar sync-token ingest and starting-soon events |
| Butlers, | connector-google-drive | connector-google-drive | Drive changes.list ingest |
| Butlers, | connector-google-health | connector-google-health | Wellness polling and envelope construction |
| Butlers, | connector-home-assistant | connector-home-assistant | HA WebSocket/REST ingest and wellness promotion |
| Butlers, | connector-live-listener | connector-live-listener | Ambient audio capture, VAD, transcription |
| Butlers, | connector-owntracks | connector-owntracks | Location webhook ingest and retention |
| Butlers, | connector-spotify | connector-spotify | Playback polling, sessions, PKCE |
| Butlers, | connector-steam | connector-steam | Steam polling and delta detection |
| Butlers, | connector-telegram-bot | connector-telegram-bot | Bot update ingest and lifecycle reactions |
| Butlers, | connector-telegram-user-client | connector-telegram-user-client, telegram-user-client-conversation-history | Readonly MTProto ingest with batching and history |
| Butlers, | connector-activitywatch | connector-activitywatch | Desktop activity ingest with privacy tiers |
| Butlers, | account-registries | google-account-registry, steam-account-registry, google-multi-account-oauth, module-calendar#Google OAuth and Rate Limiting, module-google-d | External account registries, companion-entity credentials, Google OAuth and shared multi-account loop |
| Butlers, | module-approvals | module-approvals | Gate, queue, rules, executor, audit |
| Butlers, | module-calendar | module-calendar (tools, model, reminders, search, free/busy) | Calendar event and reminder tools |
| Butlers, | calendar-sync | module-calendar (sync, home-calendar resolution, projection, dual-lane, unified view, provenance, force-sync) | Calendar sync and projection lanes |
| Butlers, | module-contacts | module-contacts | Address-book sync into entity graph |
| Butlers, | module-email | module-email | IMAP/SMTP tools |
| Butlers, | module-telegram | module-telegram | Output-only Telegram tools |
| Butlers, | module-memory | module-memory | Tiered memory store and consolidation |
| Butlers, | module-metrics | module-metrics | LLM-facing Prometheus metrics tools (pending owner keep/retire) |
| Butlers, | module-pipeline | module-pipeline | Switchboard LLM routing and decomposition |
| Butlers, | module-spotify | module-spotify | Spotify control tools |
| Butlers, | module-steam | module-steam | Steam Web API read tools |
| Butlers, | module-google-drive | module-google-drive | Drive file tools and folder hierarchy |
| Butlers, | module-google-health | module-google-health | Wellness fact query tools |
| Butlers, | module-home-assistant | module-home-assistant | HA query/control tools, actuation gate and receipts |
| Butlers, | module-document-renderer | module-document-renderer | Pure render-to-blob (pending owner keep/retire) |
| Butlers, | database-security | database-security, deployment-hardening#Strict DB-Role Enforcement Under Hardened Posture | Role enforcement with posture-dependent fallback |
| Butlers, | deployment-hardening | deployment-hardening | Posture profiles, credential indirection, backup verification |
| Butlers, | deployment-and-drift | deployment-and-drift | Migration drift sentinel and deploy verb |
| Butlers, | infrastructure-reliability | infrastructure-reliability | Infrastructure condition episodes and supervised loops |
| Butlers, | e2e-test-harness | e2e-ecosystem-staging, ingress-injection | Disposable e2e ecosystem and ingress scenario corpus |
| Domain | finance-ledger | finance-transaction-schema, finance-supporting-tables, finance-crud-operations | The finance tables, their invariants, one dedup key set, and the transaction write/correct surface. |
| Domain | finance-ingestion | finance-data-import, bulk-transaction-ingestion, finance-simplefin-bridge | Every path into the ledger (CSV, bulk API, SimpleFIN) with one result shape and one idempotency contract. |
| Domain | finance-intelligence | finance-pattern-recognition, finance-anomaly-detection, finance-budgets, finance-overview, finance-alerts | Derived analytics and proactive finance output, citing expected-signals for absence. |
| Domain | finance-cost-claims |: | Unchanged. |
| Domain | memory-discovery-catalog | memory-catalog-schema | Cross-butler discovery index invariants, write-behind, disownment, backfill. |
| Domain | memory-lifecycle | memory-retention-policy, memory-graph-health, module-memory#Memory Retention Policies API | One retention model (class and kind layering stated once), cleanup, decay, and its read-only health observatio |
| Domain | education-curriculum-graph | module-education-mind-map, module-education-curriculum, education-source-grounding | Mind-map DAG, planning, sequencing, sources, and the single map lifecycle including staleness. |
| Domain | education-learning-state | module-education-mastery, module-education-diagnostic, module-education-spaced-repetition | One mastery_status transition table and its three writers (diagnostic seed, quiz/mastery, SM-2). |
| Domain | module-education-teaching-flows |: | Flow state machine only; references the two specs above. |
| Domain | module-education-analytics |: | Unchanged. |
| Domain | entity-identity | contacts-identity | public.entities, roles, owner singleton, resolution, transitory entities, contact typeahead; points to relatio |
| Domain | proactive-insight-engine | insight-delivery | Testable broker/delivery obligations only; conventions stay in RFC 0011. |
| Domain | autonomy-promotion | autonomy-tracker, autonomy-suggestions | Fingerprint tracking through promotion/demotion suggestions as one loop. |
| Domain | routing-benchmark | routing-scorecard, scorecard-reporting | Benchmark scoring contract, or retire both into craft if the benchmark is not maintained. |
| Domain | model-catalog (C1) | complexity-classification, dispatch-intent | Model routing inputs and fit live with the catalog that consumes them. |

## T2 Dispositions (non-keep)

| Cluster | Spec / RFC | KB | Verdict | Target | Label | Reason |
|---|---|---|---|---|---|---|
| A | dashboard-api | 163.4 | split | dashboard-api (core only) + dashboard-credentials + dashboard-calendar | Observed | 163KB/60 reqs; ~110KB is domain contracts (calendar ~50KB across 20 reqs, secrets/OAuth ~35KB, memory ~15KB, issues ~11K |
| A | dashboard-api | 163.4 | rewrite-altitude | dashboard-api core | Observed | 'Cross-Domain API Route Rules' (9.4KB) is a grab-bag hiding notification retry/escalate and approvals-degraded-pool cont |
| A | dashboard-domain-pages | 82.6 | split | dashboard-health + dashboard-calendar + dashboard-memory + dashboard-e | Observed | 82.6KB container of unrelated per-butler pages; every requirement has a natural domain home and the name carries no capa |
| A | dashboard-visibility | 55.9 | split | dashboard-sessions + dashboard-timeline + dashboard-notifications + da | Observed | 55.9KB umbrella; 'visibility' is not a capability. Pagination Consistency and Loading and Error States are cross-cutting |
| A | dashboard-butler-management | 61.4 | split | dashboard-butler-fleet + dashboard-butler-detail | Observed | 61.4KB; Butler List Page alone is 12.9KB plus status-board reqs; detail tabs are a separate surface with its own tab-set |
| A | dashboard-butler-management | 61.4 | rewrite-altitude | dashboard-butler-detail | Observed | 'Butler Detail Page: Dispatch Fold-In' is change-history language and names a 'Configuration' tab that the Tab Set requ |
| A | dashboard-admin-gateway | 25.1 | merge-into | dashboard-credentials (rename; absorbs api secrets/OAuth, google-accou | Inferred | Owns the /secrets surface while dashboard-api owns the /api/secrets and /api/oauth wire contracts for the same page; OAu |
| A | dashboard-google-accounts | 10.3 | merge-into | dashboard-credentials (API/UI) with scope and leak-prevention rules de | Inferred | 10KB; account API is the credentials surface; Per-Account Scope Set Picker / Multi-Account Leak Prevention / Test-Mode w |
| A | dashboard-spotify-setup | 11.9 | merge-into | dashboard-credentials (Passport projection) + connector-spotify (endpo | Inferred | The connector-owned PKCE flow and endpoints belong to connector-spotify; the dashboard part is one content-blind project |
| A | dashboard-steam | 8.5 | retire | dashboard-credentials (accounts API, passport) + connector-steam (Play | Inferred | 8.5KB mixing SQL DDL for connectors.steam_play_history and connector config with a dashboard API; no dashboard capabilit |
| A | dashboard-connector-batch-settings | 2.5 | merge-into | dashboard-ingestion (Connector Detail) | Observed | 2.5KB, two requirements, both a card on the connector detail page that dashboard-ingestion-dispatch-console already owns |
| A | dashboard-ingestion-dispatch-console | 45.6 | rewrite-altitude | dashboard-ingestion (rename) | Observed | Delete 'Dispatch Visual Language' (restates design-language) and 'Visual and Route Verification' (acceptance-evidence pr |
| A | dashboard-bead-detail | 3.2 | merge-into | dashboard-decisions | Observed | 3.2KB with 'Purpose: TBD'; its reader contract duplicates dashboard-api 'Snapshot-backed Bead detail endpoint' and the / |
| A | dashboard-education-api | 19.4 | merge-into | dashboard-education | Observed | API/UI layer split of one butler page (19.4+25.7KB); the same active change edits both halves in lockstep. |
| A | dashboard-education-ui | 25.7 | merge-into | dashboard-education | Observed | See dashboard-education-api. |
| A | dashboard-briefing | 19.1 | merge-into | dashboard-overview | Observed | Briefing is the overview's first surface and both specs enumerate the same composed attention model; a shared-fixture te |
| A | dashboard-model-settings | 16.1 | merge-into | dashboard-settings (UI/API) + model-catalog (Routing Selection Contrac | Observed | Routing Selection Contract restates model-catalog Model Resolution incompletely, omitting the breaker gate and overrides |
| A | dashboard-settings-console | 10.3 | merge-into | dashboard-settings | Inferred | 10.3KB, 3 reqs; the /settings shell whose panels are permissions and models. |
| A | dashboard-permissions | 13.8 | merge-into | dashboard-settings | Inferred | 13.8KB, 5 reqs; /settings/permissions panel of the same console. |
| A | dashboard-audit-log | 31.3 | rewrite-altitude | audit-log (rename, leave dashboard family; absorbs api Audit Log Crede | Observed | Its own Purpose says 'not a dashboard capability per se'; it owns public.audit_log, audit.append() and retention, which  |
| A | dashboard-relationship | 75.4 | rewrite-altitude | dashboard-entities (rename; Finder moves to dashboard-shell) | Observed | 75KB; the entity views dominate (index/plex/concentration/detail). App-wide Cmd-K Finder is a shell concern now implemen |
| A | dashboard-shell | 36.2 | rewrite-altitude | dashboard-shell | Observed | Purpose restates docs/frontend/purpose-and-single-pane.md nearly verbatim plus a tech-stack list (topology). Command Pal |
| B | core-credentials | 45.3 | rewrite-altitude | core-credentials (slim) + google-multi-account-oauth + module-spotify  | Observed | 46KB grab-bag: generic CredentialStore is core, but Google OAuth lifecycle, Spotify token storage and Codex device-auth  |
| B | core-daemon | 18.7 | rewrite-altitude | core-daemon (slim); runtime_seed reqs -> runtime-config; blob reqs ->  | Observed | Carries other capabilities' requirements, a stale blob startup contract, and garbled double-negated runtime_seed scenari |
| B | core-notify | 49.5 | rewrite-altitude | core-notify; Messenger route.execute gate -> messenger/module-approval | Observed | 50KB, 28 reqs, 9 active deltas; [TARGET-STATE] tags are stale for implemented Switchboard routing; Messenger-side defens |
| B | core-spawner | 52.4 | rewrite-altitude | core-spawner (slim); adapter matrix + Codex auth -> runtime-adapters;  | Observed | 54KB with two internal restatements of catalog resolution and failover rules owned by model-failover. |
| B | core-telemetry | 10.9 | rewrite-altitude | core-telemetry (keep behavior; replace instrument catalog with referen | Observed | Metric instrument lists duplicate RFC 0005's Metrics Catalog; also contradicts staffer-qa metric names. |
| B | cross-butler-briefing-aggregation | 6.0 | merge-into | cross-butler-briefing | Observed | Aggregation and contribution are one producer/consumer contract sharing schema and the staffer exclusion. |
| B | cross-butler-briefing-contribution | 12.3 | merge-into | cross-butler-briefing | Observed | Same as above; per-butler contribution content (Health/Finance/... L46-L146) could alternatively move to each butler's s |
| B | docs-information-architecture | 15.7 | retire | about/craft-and-care/review-and-documentation.md §Document Lifecycle + | Observed | A documentation standard, not product behavior; its target docs tree has already drifted (docs/ has plans/, redesigns/,  |
| B | domain-event-delivery-recovery | 2.3 | merge-into | domain-event-bus | Observed | Its replay requirement is a near-verbatim duplicate of domain-event-bus; only the panel-visibility scenario is new. |
| B | healing-anonymizer | 6.5 | merge-into | qa-investigation | Observed | Anonymizer exists only to gate QA/healing PR egress; QA dispatch already restates its contract. |
| B | healing-model-tier | 4.0 | merge-into | model-catalog (one scenario) + qa-investigation (resolution rule) | Observed | Change-shaped spec ("API Validation Update", "Seed Data") whose enum content is owned by complexity-classification. |
| B | healing-session-tracking | 16.8 | merge-into | qa-investigation | Observed | healing_attempts is the QA investigation ledger (RFC 0015 D5); attempt state machine and API are one capability with dis |
| B | healing-worktree | 7.4 | merge-into | qa-investigation | Observed | Worktree lifecycle is restated in qa-investigation-dispatch#Worktree-Based Investigation. |
| B | model-benchmark-harness | 4.8 | merge-into | e2e-benchmark-harness | Observed | Test-harness contract implemented in tests/e2e; belongs with the E2E harness from testing and with tool-call-scorecard. |
| B | owner-condition-ledger | 11.5 | rewrite-altitude | owner-condition-ledger (fix Purpose; Finance req -> finance butler spe | Observed | Purpose is the archive TBD; a Finance-butler job requirement sits in a cross-cutting ledger spec; shares one engine with |
| B | qa-investigation-dispatch | 30.9 | merge-into | qa-investigation | Observed | Becomes the survivor that absorbs healing-* and core-spawner healing reqs. |
| B | qa-log-scanner | 12.2 | merge-into | qa-staffer | Observed | One discovery source; staffer-qa#V1 Discovery Sources already specifies it, with RFC 0015 D1 a third copy. |
| B | qa-triage | 8.7 | merge-into | qa-staffer | Observed | Triage is a patrol stage; its three-source dedup is copied from RFC 0015 D2. |
| B | runtime-config-api | 5.5 | merge-into | runtime-config | Observed | Three 4-6KB specs over one table plus core-daemon's seeding reqs describe one capability. |
| B | runtime-config-dashboard-ui | 3.8 | merge-into | runtime-config (or A-dashboard butler-detail spec) | Observed | Self-contradictory on which tab hosts the editor. |
| B | runtime-config-table | 6.6 | merge-into | runtime-config | Observed | See runtime-config-api. |
| B | runtime-opencode | 9.0 | merge-into | runtime-adapters | Observed | Only adapter with its own spec; claude/codex/gemini live inside core-spawner#Multi-Runtime Adapter Support. One adapters |
| B | session-process-logs | 6.5 | merge-into | core-sessions | Observed | Per-session child table of sessions; no independent capability. |
| B | staffer-archetype | 9.1 | retire | about/heart-and-soul/architecture.md §The Staffer Archetype (already t | Observed | Restates doctrine nearly line for line; the testable parts (type parse, briefing skip, switchboard registration) are alr |
| B | staffer-qa | 25.0 | merge-into | qa-staffer | Observed | Survivor for patrol/discovery/triage; drop Future Discovery Source Catalog (roadmap, already in RFC 0015 D1) and MANIFES |
| B | testing | 60.1 | retire | docs/testing/ (Quick Start, Debugging E2E); about/craft-and-care/testi | Observed | 63KB mixing run commands, debugging how-to and engineering rules; none is product capability. Pytest verdict rule alread |
| B | tool-call-scorecard | 2.3 | merge-into | e2e-benchmark-harness | Observed | 2KB, 4 reqs; is the scoring half of the benchmark harness. |
| C1 | butler-base-spec | 39.9 | rewrite-altitude | butler-contract | Observed | Core primitive is spec, but roster inventories, port table and AGENTS.md/CLAUDE.md/MANIFESTO authoring rules are topolog |
| C1 | butler-general | 4.8 | rewrite-altitude | butler-general (EOD briefing format only) | Observed | Identity/Runtime + Module profile + Tool/Schedule/Skill inventory + Memory Taxonomy reqs mirror roster/<b>/butler.toml,  |
| C1 | butler-health | 39.0 | rewrite-altitude | butler-health | Observed | Keep wellness/medication/route behavior; strip config inventories which have drifted |
| C1 | butler-relationship | 18.5 | rewrite-altitude | butler-relationship (absorbs passive-interaction-sync) | Inferred | Strip inventories; absorb the relationship-only passive interaction job |
| C1 | butler-finance | 20.3 | rewrite-altitude | butler-finance | Inferred | Strip inventories and prompt 'Behavioral Guidelines'; keep reconciliation/settlement contracts |
| C1 | butler-education | 20.2 | rewrite-altitude | butler-education | Observed | Spec dictates MANIFESTO.md and CLAUDE.md content and roster compliance: manifesto carrying requirements inverted |
| C1 | butler-travel | 16.1 | rewrite-altitude | butler-travel | Inferred | Strip inventories; keep trip model, booking identity, journey integrity, medication snapshot consumer, degraded disclosu |
| C1 | butler-home | 11.6 | rewrite-altitude | butler-home | Observed | Strip inventories and 'Personality'; physical-consequence scenario duplicates module-home-assistant |
| C1 | butler-lifestyle | 11.9 | rewrite-altitude | butler-lifestyle | Observed | Domain Scope Boundary is a legitimate routing invariant; the Manifesto req is a pointer with no testable content |
| C1 | butler-chronicler | 29.6 | rewrite-altitude | butler-chronicler | Observed | Scope/doctrine paragraph is stated four times (v1.md, RFC 0014 D8, spec, manifesto); keep storage/attribution/projection |
| C1 | butler-secrets | 36.0 | merge-into | dashboard-secrets (rename; move to dashboard cluster) | Observed | It is a dashboard page spec (/secrets passport book), not a butler |
| C1 | connector-base-spec | 59.5 | split | connector-contract + connector-discretion + connector-registry | Observed | 59.5KB, three concerns, and ingest.v1/request-context/dedup/triage restate RFC 0003 |
| C1 | connector-filtered-events | 13.5 | merge-into | connector-contract | Observed | Batch-flush obligation stated in base, here, and per connector |
| C1 | connector-replay-queue | 6.6 | merge-into | connector-contract | Observed | Drain loop stated three times with near-identical scenarios |
| C1 | connector-state-aggregates | 4.1 | merge-into | connector-registry | Inferred | Two reqs about ingestion console aggregate honesty; belongs with connector registry/read model |
| C1 | connector-discord | 4.8 | retire | RFC 0018 deferral note (keep a TARGET-STATE line in connector-contract | Observed | Spec claims an AS-BUILT shipped connector; code is a self-declared not-production-ready draft with no compose service |
| C1 | telegram-user-client-conversation-history | 7.3 | merge-into | connector-telegram-user-client | Observed | Extension of one connector kept as a separate capability |
| C1 | connector-telegram-user-client | 23.1 | rewrite-altitude | connector-telegram-user-client | Observed | Absorb conversation-history; drop stale 'Implementation Status' and topology 'Deployment Model'; fix discretion tier/fil |
| C1 | google-account-registry | 7.9 | merge-into | account-registries | Observed | Companion-entity credential pattern duplicated with steam registry |
| C1 | steam-account-registry | 6.9 | merge-into | account-registries | Observed | Same pattern as Google registry |
| C1 | google-multi-account-oauth | 9.4 | merge-into | account-registries | Inferred | OAuth flow for the registry; also the natural home for shared Google token refresh/retry/redaction |
| C1 | module-calendar | 57.3 | split | module-calendar + calendar-sync | Observed | 57.3KB (>40KB); sync/projection/dual-lane/force-sync is a separate capability from event/reminder tools |
| C1 | module-dashboard-read | 4.8 | merge-into | butler-concierge | Observed | Single consumer; duplicated source-envelope rule |
| C1 | module-email | 5.1 | rewrite-altitude | module-email | Observed | Remove tombstone '(Removed)' requirement; egress ownership lives in butler-messenger |
| C1 | module-google-drive | 16.3 | rewrite-altitude | module-google-drive | Observed | Drop module boilerplate reqs (identity/registration/HTTP client/migration) and duplicated Google OAuth req |
| C1 | module-home-assistant | 25.5 | rewrite-altitude | module-home-assistant | Inferred | Keep actuation/receipts (RFC 0028 at WHAT level); drop lifecycle/registration boilerplate |
| C1 | module-spotify | 12.0 | rewrite-altitude | module-spotify | Inferred | Drop Shutdown/identity boilerplate; keep tools + sensitivity |
| C1 | passive-interaction-sync | 16.7 | merge-into | butler-relationship | Inferred | Relationship-only scheduled job |
| C1 | csv-extraction-skill | 7.5 | retire | roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md | Inferred | Spec restates a skill procedure (prompt content), not a capability contract |
| C1 | build-reproducibility | 5.7 | rewrite-altitude | about/craft-and-care/interfaces-and-dependencies.md | Inferred | Lockfile/pin rules are an engineering standard; craft-and-care has none today |
| C1 | adapter-integration-testing | 7.6 | merge-into | core-spawner (runtime adapter contract; other cluster) | Inferred | Test contract for runtime adapters belongs with the adapter spec or craft testing standard |
| C1 | e2e-ecosystem-staging | 3.2 | merge-into | e2e-test-harness | Observed | Two tiny test-harness specs for one e2e suite |
| C1 | ingress-injection | 3.6 | merge-into | e2e-test-harness | Observed | Same suite; factories exist |
| C2 | finance-transaction-schema | 10.6 | merge-into | finance-ledger | Observed | Ledger table, dedup indexes and SPO backfill/retirement are one data model told again in supporting-tables, crud-operati |
| C2 | finance-supporting-tables | 12.6 | merge-into | finance-ledger | Observed | Table-per-requirement restatement; every table is re-specified by the capability spec that uses it (budgets, balance sna |
| C2 | finance-crud-operations | 10.3 | merge-into | finance-ledger | Observed | Write surface of the same ledger; its bulk-import requirement is a verbatim twin of supporting-tables and overlaps data- |
| C2 | finance-data-import | 6.9 | merge-into | finance-ingestion | Observed | CSV import path; dedup and result shape restated from transaction-schema and crud-operations. |
| C2 | bulk-transaction-ingestion | 7.1 | merge-into | finance-ingestion | Observed | Second bulk path that writes SPO facts with its own sha256 composite key, while the ledger spec targets retiring the SPO |
| C2 | finance-simplefin-bridge | 9.5 | merge-into | finance-ingestion | Inferred | Provider ingestion path into the same ledger and dedup contract; keep its credential/boundary requirements intact as a s |
| C2 | finance-pattern-recognition | 5.7 | merge-into | finance-intelligence | Observed | Recurring detection, bill prediction and price-change flag are re-specified in finance-alerts and finance-overview. |
| C2 | finance-anomaly-detection | 6.0 | merge-into | finance-intelligence | Inferred | Analytic over the ledger; Duplicate Transaction Detection overlaps crud Duplicate merge and ledger dedup. |
| C2 | finance-budgets | 6.0 | merge-into | finance-intelligence | Observed | Small analytic slice; its period enum contradicts supporting-tables and the migration. |
| C2 | finance-overview | 8.0 | merge-into | finance-intelligence | Observed | Net worth, cash flow, subscription audit and tax flags are read projections over the same ledger. |
| C2 | finance-alerts | 17.5 | merge-into | finance-intelligence | Observed | Proactive output of the analytics above; the recurrence-absence rule is stated here, in supporting-tables and in butler- |
| C2 | memory-catalog-schema | 2.2 | merge-into | memory-discovery-catalog | Observed | One-requirement duplicate of the catalog table that contradicts discovery-catalog and the actual migration. |
| C2 | memory-discovery-catalog | 24.7 | rewrite-altitude | memory-discovery-catalog | Observed | Survives as the single catalog spec but its column list is wrong against code and should state invariants rather than a  |
| C2 | memory-retention-policy | 21.4 | rewrite-altitude | memory-lifecycle | Observed | Calls memory_policies 'the authoritative source' while module-memory specifies a second, live per-kind retention table a |
| C2 | memory-graph-health | 3.5 | merge-into | memory-lifecycle | Observed | Its cleanup-population contract is already restated as REQ-memory-retention-policy-009. |
| C2 | memory-events-enrichment | 2.5 | merge-into | module-memory | Inferred | Two-requirement column addition to memory_events; belongs with the storage-layer requirements in module-memory. |
| C2 | memory-migration-integration-tests | 2.6 | retire | craft (test standard) or tests/ docstring | Observed | A test plan, not capability behavior; last touched 2026-03-15. |
| C2 | module-education-mind-map | 26.7 | merge-into | education-curriculum-graph | Observed | Graph CRUD, DAG and frontier are one model with curriculum; its mastery state machine and lifecycle clauses are restated |
| C2 | module-education-curriculum | 21.5 | merge-into | education-curriculum-graph | Observed | Syllabus lifecycle duplicates mind-map status transitions; same nodes and edges. |
| C2 | education-source-grounding | 6.0 | merge-into | education-curriculum-graph | Inferred | Three requirements that annotate mind-map nodes; too thin to stand alone. |
| C2 | module-education-mastery | 21.7 | merge-into | education-learning-state | Observed | Mastery, diagnostic seeding and SM-2 all write mastery_status; one spec should own the state machine and its writers. |
| C2 | module-education-diagnostic | 15.2 | merge-into | education-learning-state | Observed | Self-correction requirement duplicates mastery and contradicts the mind-map transition table. |
| C2 | module-education-spaced-repetition | 19.1 | merge-into | education-learning-state | Observed | Its transitions requirement explicitly defers forward promotions to mastery; schedule cleanup duplicates teaching-flows  |
| C2 | entity-identity | 46.3 | rewrite-altitude | entity-identity | Observed | 46KB spec still specifies memory edge facts for knows/works_at that module-memory and relationship-facts now reject, and |
| C2 | contacts-identity | 3.9 | merge-into | entity-identity | Observed | One-requirement typeahead endpoint whose own Purpose defers to entity-identity and relationship-facts. |
| C2 | user-preferences | 11.1 | rewrite | user-preferences | Observed | Resolves the owner via the dropped public.contacts table and stores preferences as memory facts, while relationship-fact |
| C2 | home-assistant-person-mapping | 24.7 | rewrite-altitude | home-assistant-person-mapping | Inferred | About a third is review-gating process and a test plan; a router already exists, so the 'implementation remains separate |
| C2 | proactive-insight-engine | 51.9 | rewrite-altitude | proactive-insight-engine | Observed | 52KB, 29 requirements; priority bands, verbosity presets, cooldown and digest format restate RFC 0011 nearly verbatim. |
| C2 | insight-delivery | 6.6 | merge-into | proactive-insight-engine | Observed | Digest/standalone/engagement/notify-intent are the delivery phase of the engine and already in RFC 0011; the engine also |
| C2 | commitment-lifecycle | 10.0 | rewrite | commitment-lifecycle | Observed | Purpose is still the archive placeholder 'TBD'; requirements themselves carry IDs and RFC 0026 sources. |
| C2 | autonomy-tracker | 8.1 | merge-into | autonomy-promotion | Inferred | Fingerprint tracking and promotion suggestions are two halves of one loop; neither is meaningful alone. |
| C2 | autonomy-suggestions | 9.5 | merge-into | autonomy-promotion | Inferred | See autonomy-tracker. |
| C2 | routing-scorecard | 2.1 | merge-into | routing-benchmark (or craft) | Inferred | Benchmark harness output, not runtime capability; stale since 2026-03-15. |
| C2 | scorecard-reporting | 2.7 | merge-into | routing-benchmark (or craft) | Inferred | Output-format half of the same benchmark; no implementation of summary table or cost summary found by grep. |
| C2 | complexity-classification | 5.7 | merge-into | model-catalog (cluster C1) | Inferred | Complexity tiers are a model-routing input already named in model-catalog; misclustered here. |
| C2 | dispatch-intent | 6.8 | merge-into | model-catalog (cluster C1) | Inferred | Capability descriptors and hard fit overlap model-catalog#Model Catalog Capability Envelope and #Fit Before Ranking. |
| C2 | error-recovery-corrections | 18.9 | rewrite-altitude | error-recovery-corrections | Inferred | Canonical tool-description text and failure-message dictionary are prompt copy, not behavior. |
| D | 0002-mcp-tool-surface-and-modules.md |  | rewrite-altitude | 0027-runtime-tool-surface-discovery.md (absorbs Amendments 1-2) | Observed | Amendments 1-2 restate RFC 0027 and still say 'effective when merged'; core tool catalog and tool-budget sections restat |
| D | 0005-observability-and-telemetry.md |  | rewrite-altitude | keep trace-propagation + tool_span + cardinality decisions; move §Metr | Inferred | A metrics catalog is an enumerated requirement/reference list, not a design decision; only 1 code file and 2 specs cite  |
| D | 0007-dashboard-and-api-surface.md |  | rewrite-altitude | keep architecture, response envelope, route discovery, Amendments 1-3; | Observed | Route maps and shell UX are WHAT/docs, already carried by dashboard-shell and dashboard-api specs and docs/api_and_proto |
| D | 0008-deployment-network-security.md |  | rewrite-altitude | keep network-isolation decision + invariants; move '## Operational Com | Inferred | Operational commands are topology/runbook, not contract; no spec cites RFC 0008; doctrine security.md:288-299 links to i |
| D | 0009-situational-context-bus.md |  | condense-to-decision-record | context-bus spec (after change canonical-dnd-generation-guard archives | Inferred | context-bus spec (15 requirements, cites RFC 0009) carries table, vocabulary, permissions, TTL; the 215-line DND Generat |
| D | 0010-cross-butler-briefing-exception.md |  | merge-into | new 'cross-schema read exceptions' register RFC (absorbs 0010, 0020, 0 | Observed | Three RFCs restate the same MAY/MUST-NOT criteria with per-instance deltas; 0010 also hosts an unrelated QA-only local s |
| D | 0011-proactive-insight-delivery.md |  | condense-to-decision-record | proactive-insight-engine spec | Inferred | proactive-insight-engine spec (53KB, 29 requirements, cites RFC 0011) carries the pipeline, candidate schema, budgets an |
| D | 0012-finance-transaction-data-model.md |  | condense-to-decision-record | finance-transaction-schema + finance-supporting-tables + finance-crud- | Observed | Implemented; table, indexing, tiered dedup and supporting tables are carried by finance specs; keep 'Why not expression  |
| D | 0013-dunbar-group-aware-interaction-scoring.md |  | condense-to-decision-record | dunbar-tier-scoring spec | Observed | Direction weighting, group-size dilution and reciprocal gating are implemented and specified (27 term hits in dunbar-tie |
| D | 0014-chronicler-time-butler.md |  | condense-to-decision-record | butler-chronicler + dashboard-chronicles specs | Observed | Open Questions are already resolved inline; spec now holds requirements but still calls the RFC 'full normative contract |
| D | 0016-s3-blob-storage-contract.md |  | condense-to-decision-record | s3-blob-storage spec | Inferred | Spec carries BlobStore protocol, s3:// scheme, credential path, startup check and session lifecycle (D4-D8); keep D1-D3  |
| D | 0017-owner-routing-safety-incident-reconciliation.md |  | rewrite-altitude | rename to 'owner-routing safety contract'; keep §2 Safety contract, dr | Observed | Incident narrative is history, not contract, and quotes the owner's personal work email address verbatim; README title d |
| D | 0018-connector-scope-and-deferral-rationale.md |  | rewrite-altitude | heart-and-soul/v1.md (#connectors decision rule) + ideas-ledger.md (de | Observed | Scope/deferral is WHY doctrine and parked-idea inventory, not a wire contract; its 'only connectors v1 ships' roster is  |
| D | 0019-proactive-egress-and-automation-parked.md |  | retire | ideas-ledger.md §Egress (already holds the five egress entries) + one- | Observed | Parked/rejected decisions carry no contract; the egress items already moved to the ideas ledger; the rule-engine tension |
| D | 0020-calendar-cross-domain-overlay-read-exception.md |  | merge-into | cross-schema read exceptions register RFC | Observed | Instance of the RFC 0010 pattern restating its criteria |
| D | 0026-commitment-lifecycle.md |  | condense-to-decision-record | commitment-lifecycle + owner-condition-ledger specs | Observed | Status says Draft but spec (8 requirements) and code exist; references a change name as if it were an RFC |
| D | 0030-system-plane-read-exception.md |  | merge-into | cross-schema read exceptions register RFC | Observed | Instance of RFC 0010 pattern with a deliberate departure (on-demand reads) that contradicts doctrine |
| D | 0036-models-exact-path-vision-proof.md |  | rewrite-altitude | restructure to RFC template (Summary/Decision/Trade-offs); move UI cop | Observed | No Date, non-bold non-vocabulary status, opens with dense UI and endpoint prose; carries scenarios (spec altitude) and a |
| D | 0037-general-capture-and-private-source-boundaries.md |  | rewrite-altitude | restructure to RFC template; drop manifest hashes, PR head SHAs and ba | Observed | Process provenance (commit SHAs, manifest digests) is not design; non-vocabulary status, no Date |

## T3 Contradictions

| Cluster | Members | Contradiction | Evidence |
|---|---|---|---|
| A | dashboard-shell#Sidebar Navigation (56px Icon Rail), dashboard-shell#S | Requirement text and design-language say a fixed 56px icon rail. Scenarios say 240px expanded by default and collapsible to 56px. Code defaults to 240px. Label: Observed. | dashboard-shell/spec.md:164 vs :168-169 and :122; dashboard-design-language/spec.md:265; f |
| A | dashboard-shell#Command Palette (Global Search), dashboard-relationshi | The shell describes the retired CommandPalette with a search icon, 20%-from-top placement and recent searches. Relationship says the Finder hits exactly one endpoint. Code has one unified menu that al | dashboard-shell/spec.md:294-312; dashboard-relationship/spec.md:898-905; frontend/src/comp |
| A | dashboard-api#Standard Response Envelopes, dashboard-chat-ui#Cross-But | The api lists unwrapped exceptions as a closed set but omits GET /api/conversations/{id}, which chat-ui says returns a raw object. The list still names /api/relationship/contacts/{contactId} as a prim | dashboard-api/spec.md:49-56; dashboard-chat-ui/spec.md:420; dashboard-relationship/spec.md |
| A | about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md#Butler D | The RFC tab list is stale against the spec's six base tabs. The RFC also says 'sub-views are first-class child routes, not page-level ?tab= state', while the spec mandates URL-driven ?tab=. The RFC ci | rfcs/0007:222,230-241; dashboard-butler-management/spec.md:983-1000; rg 'Canonical Route M |
| A | dashboard-model-settings#Routing Selection Contract, model-catalog#Mod | model-settings defines selection without the circuit-breaker gate, butler overrides or evidence tie-break. Read alone, it states a different resolver than the one model-catalog and the code implement. | dashboard-model-settings/spec.md:192-207; model-catalog/spec.md:117-120,161-173; src/butle |
| A | dashboard-approvals#Approvals Page in Dispatch Language, dashboard-app | One requirement mandates a three-pane body with no cards. The other places suggestions above a 'two-pane approvals body' and requires 'a card for each suggestion'. Label: Observed. | dashboard-approvals/spec.md:106-111 vs :590,:596 |
| A | about/heart-and-soul/design-language.md#What the Dashboard Is / Is Not | Doctrine says the dashboard is a 'read-mostly observability surface' and 'Not a chat app'. The shell says it is 'not a secondary monitoring view -- it IS the control plane', with everything accessible | about/heart-and-soul/design-language.md:24,47; dashboard-shell/spec.md:5; dashboard-admin- |
| A | dashboard-butler-management#Switchboard Registry Tab, dashboard-shell# | The registry tab mandates 'relative time via formatDistanceToNow', but the shell requires all time rendering through <Time>/useTimezone. In code, formatDistanceToNow appears only in the Time primitive | dashboard-butler-management/spec.md:791; dashboard-shell/spec.md:61-96; rg formatDistanceT |
| A | dashboard-butler-management#Approvals tab, dashboard-approvals#Queue R | The per-butler tab derives severity from expiry alone. The trust console ranks by expiry and blast radius, so the same action can read differently on two surfaces. Label: Inferred. | dashboard-butler-management/spec.md:720-724; dashboard-approvals/spec.md:164 |
| B | core-daemon#Blob storage initialization at startup phase 8c, s3-blob-s | core-daemon says an unreachable endpoint or missing bucket fails startup; s3-blob-storage says both disable blob storage non-fatally. Code follows s3-blob-storage, so core-daemon is stale. | core-daemon 'head_bucket startup check: ... SHALL fail startup with a clear error' vs s3-b |
| B | model-catalog#Adapter Token Reporting Contract, core-spawner#Logical S | model-catalog says an adapter without token counts gets no ledger row; spawner and token-limits require one linked usage_source='unmeasurable' row per attempt. | model-catalog L298-L301 'the ledger does not record a row for that invocation' vs core-spa |
| B | core-modules#Load-All Module Loading, core-spawner#Healing Configurati | core-modules instantiates every registered module even without config; core-spawner says self-healing is not loaded and the spawner fallback is disabled when [modules.self_healing] is absent. No roste | core-modules L125-L131; core-spawner L557-L560; src/butlers/lifecycle.py:99 load_all; rg s |
| B | core-telemetry#Metric Namespace Convention, staffer-qa#Observability ( | core-telemetry requires every instrument to carry the `butlers.` prefix; staffer-qa mandates `qa_patrol_total`, `qa_findings_total` etc. without it. | core-telemetry L168 'All metric instruments SHALL use the `butlers.` namespace prefix' vs  |
| B | runtime-config-dashboard-ui#Purpose, runtime-config-dashboard-ui#Confi | Purpose says the editor lives in the Management tab, not the Config tab; the requirement says the Config tab edits. | runtime-config-dashboard-ui L4 vs L31 (Observed) |
| B | core-daemon#Config loading parses runtime_seed section | Scenarios titled 'Reject old [butler.runtime] section' assert the section is accepted and ignored; first scenario says a RuntimeSeedConfig 'SHALL NOT be returned with fields' then lists a different fi | core-daemon L91-L120 (Observed) |
| B | core-notify#[TARGET-STATE] Messenger Routing via Switchboard | Tagged target-state but implemented; target tag misleads readers on what is shipped. | core-notify L500 vs src/butlers/core_tools/_notifications.py:543 (switchboard_client), :12 |
| C1 | connector-base-spec#Replay Queue Drain Loop, connector-google-health#R | Base requires drain after the poll cycle; google-health requires drain first, at cycle start (code drains at top). | base:48 'after each poll cycle'; google-health:243 'When a poll cycle begins ... first dra |
| C1 | connector-base-spec#Shared Discretion Layer, connector-live-listener#D | Catalog tier for discretion: base says 'specialty', the two connector specs say 'discretion'. Code uses SPECIALTY. | base:488 complexity_tier='specialty'; live-listener:150 complexity_tier='discretion'; tg-u |
| C1 | connector-filtered-events#Filtered Event Persistence (Batch Flush), co | filtered-events forbids bare 'discretion:IGNORE'; tg user client spec mandates exactly that reason. | filtered-events:49 'a bare discretion:IGNORE reason SHALL NOT be used'; tg-uc:165 'filter_ |
| C1 | about/lay-and-land/components.md#Shared connector infrastructure, conn | Topology says discretion is fail-open; spec says fail-open only for weight >= threshold, else fail-closed. | components.md:125 'Fail-open. Owner messages bypass.'; base:488 'weight < threshold → IGNO |
| C1 | butler-switchboard#Wellness Ingest Event Shape, connector-google-healt | Endpoint identity/idempotency key format (google_user_id vs email, account-scoped vs not) and dispatch mechanism (ingest-handler registration vs no per-butler handler registry) disagree. | switchboard:133 'google_health:user:<google_user_id>', key 'google_health:<resource>:<reco |
| C1 | connector-discord#[AS-BUILT] Shipped Bot-Token Gateway Connector, abou | Spec and doctrine present Discord as a shipped connector; code is self-declared target-state, not deployed. | connector-discord:8; src/butlers/connectors/discord_user.py:3; no discord service in docke |
| C1 | about/heart-and-soul/v1.md#Modules, module-pipeline, module-telegram#O | v1 inventory describes Pipeline as multi-step workflows, Telegram module as interactive messaging, Self-healing as crash recovery/restart, Gmail as IMAP; specs/code say LLM router, output-only, report | v1.md:62-113; module-telegram:21; self_healing/__init__.py:1-15; connector-gmail:39 |
| C1 | connector-telegram-user-client#Implementation Status, connector-telegr | Status req says per-chat filtering and redaction are unimplemented while later reqs specify them as live behavior. | tg-uc:284 vs :127, :165, :318 |
| C1 | butler-base-spec#Staffers vs Domain Butlers, butler-base-spec#Port Ass | Base spec enumerates 2 staffers and 5 domain butlers; roster has 4 staffers (switchboard, messenger, qa, concierge) and 9 domain butlers. | butler-base-spec:200,:413; ls roster/ |
| C1 | database-security#Graceful Fallback Policy, deployment-hardening#Stric | database-security describes graceful fallback without posture; hardened posture fails closed. Reader of database-security alone gets the wrong production behavior. | database-security:129; deployment-hardening:135 |
| C2 | finance-budgets#Budget Target Management, finance-supporting-tables#Ca | Budget period enum disagrees, and the code disagrees with itself: the tool accepts quarterly, which the migration CHECK constraint rejects. | finance-budgets L13 (weekly/monthly/quarterly); finance-supporting-tables L121 (weekly/mon |
| C2 | bulk-transaction-ingestion#Bulk transaction ingestion HTTP endpoint, f | The bulk endpoint is specified to persist via record_transaction_fact into SPO facts, while the ledger spec targets removing record_transaction_fact; two bulk_record_transactions implementations exist | bulk-transaction-ingestion L13; finance-transaction-schema L158-170; roster/finance/tools/ |
| C2 | finance-transaction-schema#Tiered deduplication indexes, finance-trans | Three different composite dedup keys: index (account_id, posted_at, amount, merchant); backfill (posted_at, merchant, amount, currency); bulk sha256(posted_at/amount/merchant/account_id) with lowercas | finance-transaction-schema L136, L148; bulk-transaction-ingestion L24-29. Label: Observed. |
| C2 | memory-catalog-schema, memory-discovery-catalog, alembic/versions/core | Catalog column set, tenant default and owning migration disagree across both specs and code. | memory-catalog-schema: summary, default 'shared', memory-module migration; memory-discover |
| C2 | memory-retention-policy#Memory retention policy table, module-memory#M | Two retention authorities: per-schema memory_policies keyed by retention_class ('the authoritative source') and public.memory_retention_policies keyed by kind with ttl_days/max_rows, both consumed by  | memory-retention-policy L9; module-memory L360-378; src/butlers/scheduled_jobs.py:461,514, |
| C2 | entity-identity#Typed relationships between entities via edge facts, m | entity-identity requires store_fact(predicate='works_at'/'knows') to create a memory edge fact; module-memory requires the same call to raise ValueError. | entity-identity L395-404; module-memory L516-526. Label: Observed. |
| C2 | entity-identity#Entity info table for per-entity properties and creden | Credentials are specified to live in public.entity_info (secured=true) and also in relationship.credentials; both are live (Google refresh tokens read from public.entity_info). | entity-identity L457; relationship-facts L208-214; src/butlers/google_account_registry.py: |
| C2 | module-education-diagnostic#Self-Correction of Diagnosed Mastery, modu | Diagnostic allows a passing diagnosed node to be 'promoted toward reviewing'; the mind-map table has no diagnosed→reviewing transition and says any other transition MUST be rejected. | module-education-diagnostic L266; module-education-mind-map L470-480. Label: Observed. |
| C2 | module-education-mind-map#Mind map lifecycle: staleness abandonment,  | Two 30-day staleness clocks on different fields (node updated_at vs flow last_session_at) drive the same abandoned status. | mind-map L447; teaching-flows L403; active change education-mind-map-lifecycle-integrity p |
| C2 | proactive-insight-engine#Owner Attention Policy Is the Only Quiet-Hour | Engine says the global Owner Attention Policy is the only quiet-hours authority for insights; time-aware-delivery applies a per-butler delivery_preferences quiet-hours gate to `insight` notifications  | proactive-insight-engine L298; time-aware-delivery L4-13, L33. Label: Inferred (time-aware |
| C2 | user-preferences#Preference predicate namespace convention, contacts-i | user-preferences resolves the owner from public.contacts, which contacts-identity records as dropped. | user-preferences L20; contacts-identity L5. Label: Observed. |
| D | heart-and-soul/architecture.md#The exception mechanism, 0030-system-pl | Doctrine requires every cross-schema read exception to be batch-oriented and says interactive queries must always go through the Switchboard; RFC 0030 is an accepted on-demand exception used during LL | architecture.md:56-61 'Each exception must be read-only ..., batch-oriented ... interactiv |
| D | 0018-connector-scope-and-deferral-rationale.md, ideas-ledger.md#New co | RFC 0018 says its roster is 'the only connectors v1 ships' and omits ActivityWatch; the ideas ledger parks ActivityWatch and SimpleFIN; both are built and specified. Label: Observed | RFC 0018 lines 45-50; ideas-ledger.md:52-64; openspec/specs/connector-activitywatch, opens |
| D | 0025-tracker-host-beads-projection-exporter.md, deploy/helm/butlers/fi | RFC 0025 restricts the tracker reader to a tracker-host exporter publishing to PostgreSQL, with JSONL only as rollback; the shipped k3s CronJob runs bd export in-cluster to a PVC JSONL that the decisi | RFC 0025 §Decision 1 lines 41-48; commit ccdaa3946 'optional beads export CronJob for k3s' |
| D | openspec/specs/butler-chronicler, 0014-chronicler-time-butler.md | Authority is inverted: the spec defers to the RFC as the 'full normative contract' instead of the RFC deferring to the spec for WHAT. Label: Observed | openspec/specs/butler-chronicler/spec.md:633 |
| D | 0010-cross-butler-briefing-exception.md#QA-only local scheduler policy | The QA scheduler exception reads a Switchboard-owned function on every local tick, not a migration-tracked batch view; it sits inside the RFC whose own criteria exclude function-mediated on-demand rea | RFC 0010 lines 160-175 vs lines 121-140 |

## T4 Wrong altitude

| Cluster | Spec / RFC | Section | Belongs in |
|---|---|---|---|
| A | dashboard-shell | Purpose (single-pane rationale, Detect/Diagnose/Act, scope b | doctrine |
| A | dashboard-design-language | Purpose North-Star narrative and Requirement: Composure Doct | doctrine |
| A | dashboard-api | Standard Response Envelopes; Butler-Specific Route Auto-Disc | rfc |
| A | dashboard-api | API Client (Frontend); dashboard-visibility Data Model Contr | docs |
| A | dashboard-ingestion-dispatch-console | Visual and Route Verification | craft |
| A | dashboard-approvals | Approvals Page in Dispatch Language › Legacy page deleted in | craft |
| A | dashboard-butler-management | Butler Detail Page: Dispatch Fold-In | docs |
| A | dashboard-relationship | App-wide Cmd-K Finder ranking sources ('prompts/07-finder.md | docs |
| A | dashboard-briefing | LLM Elaboration (prompt text and parameters) | docs |
| A | about/legends-and-lore/rfcs/0007-dashboard-and-api | Butler Detail Tabs; Data Access and Refresh table; Command P | docs |
| B | staffer-archetype | whole spec (Architectural Primitive, Infrastructure Contract | doctrine |
| B | staffer-qa | Infrastructure Contract (MANIFESTO.md) | doctrine |
| B | staffer-qa | Future Discovery Source Catalog | rfc |
| B | staffer-qa / qa-triage / qa-log-scanner | Pluggable Discovery Source Architecture, V1 Discovery Source | rfc |
| B | core-telemetry | Spawner/Route/Buffer/Additional Domain Metric Instruments | rfc |
| B | s3-blob-storage | BlobRef uses s3:// scheme exclusively; S3 storage configurat | rfc |
| B | core-credentials | Spotify OAuth Token Storage (Tier 2 framing) | doctrine |
| B | testing | Quick Start; Debugging E2E Tests | docs |
| B | testing | Test Framework Configuration, Markers, Directory Structure,  | craft |
| B | docs-information-architecture | whole spec | craft |
| B | core-modules | MCP Tools Raise on Invalid Input (motivation paragraphs) | craft |
| B | ingestion-policy | Data migration from legacy tables | docs |
| B | s3-blob-storage | Blob ref migration tooling; LocalBlobStore and local:// cruf | docs |
| C1 | butler-base-spec | Port Assignment Convention; Roster Directory Structure Conve | topology |
| C1 | butler-base-spec | MANIFESTO.md as Public Identity; CLAUDE.md as System Prompt  | craft |
| C1 | butler-education | MANIFESTO.md Content; CLAUDE.md System Prompt; High-Tier Com | docs |
| C1 | butler-concierge | Infrastructure Contract (MANIFESTO.md) | docs |
| C1 | butler-chronicler | Retrospective-Only Scope (Amendment paragraph) | doctrine |
| C1 | butler-* (general, health, relationship, finance,  | Identity and Runtime / Module profile / Tool, Schedule, Skil | docs |
| C1 | butler-finance | Finance Butler Intelligence Behavioral Guidelines | docs |
| C1 | connector-base-spec | ingest.v1 Envelope Schema; Request Context Assignment; Dedup | rfc |
| C1 | connector-base-spec | [TARGET-STATE] Horizontal Scaling Patterns; Pydantic Respons | rfc |
| C1 | connector-* (all per-connector) | Environment Variables | docs |
| C1 | connector-owntracks / connector-telegram-user-clie | Docker Compose Integration / Deployment Model | topology |
| C1 | module-google-drive / module-home-assistant / modu | Module Identity and Dependencies; Tool Registration; HTTP Cl | rfc |
| C1 | csv-extraction-skill | whole spec | docs |
| C1 | build-reproducibility | whole spec | craft |
| C1 | docs/connectors/heartbeat.md, docs/connectors/gmai | normative MUST lines | docs |
| C1 | about/heart-and-soul/v1.md | Modules / Connectors inventory | topology |
| C2 | proactive-insight-engine | Priority Scoring Convention; Verbosity Presets; Cooldown Tra | rfc |
| C2 | memory-migration-integration-tests | whole spec | craft |
| C2 | home-assistant-person-mapping | Implementation and use remain separately gated | craft |
| C2 | entity-identity | Entity info type registry (frontend ↔ backend coupling) | docs |
| C2 | error-recovery-corrections | Canonical Tool Description Text; Failure Message Dictionary; | docs |
| C2 | routing-scorecard / scorecard-reporting | whole specs | craft |
| C2 | memory-discovery-catalog / memory-catalog-schema / | column-by-column DDL listings | docs |
| D | 0018-connector-scope-and-deferral-rationale.md | whole RFC (roster, deferral catalogue, ## Doctrine) | doctrine |
| D | 0019-proactive-egress-and-automation-parked.md | whole RFC | doctrine |
| D | 0007-dashboard-and-api-surface.md | Frontend Route Map, Butler Detail Tabs, Command Palette, Glo | docs |
| D | 0005-observability-and-telemetry.md | Metrics Catalog | docs |
| D | 0008-deployment-network-security.md | Operational Commands | topology |
| D | 0017-owner-routing-safety-incident-reconciliation. | 1. Incident summary | docs |
| D | 0036-models-exact-path-vision-proof.md | Specification scenarios for review; UI copy preamble | rfc |
| D | 0037-general-capture-and-private-source-boundaries | Authority and source precedence (commit SHAs, manifest diges | docs |
| D | 0034-messenger-voice-egress.md | Requirement traceability | rfc |

## T5 Owner questions

1. [A] Doctrine says the dashboard is 'read-mostly observability' and 'not a chat app', while the shell calls it the exclusive control plane and chat-ui ships global chat postures. Which framing wins? Doctrine should be amended or the shell and chat-ui purposes reframed.
2. [A] Should bu-60pwv6.6 adopt this target list, with new domain homes dashboard-calendar, dashboard-memory, dashboard-health, dashboard-sessions, dashboard-timeline, dashboard-notifications, dashboard-issues, dashboard-butler-fleet and dashboard-butler-detail? Or should it relocate into existing non-dashboard specs (module-calendar, module-memory, butler-health), at the cost of mixing UI contracts into module specs?
3. [A] Is a 60KB dashboard-credentials spec acceptable? The alternative keeps Google accounts separate, but OAuth state and scope rules then stay split across three specs.
4. [A] dashboard-entities (about 70KB) could split into entity views and the contact/entity API, which would be a layer split. Prefer the single spec or the split?
5. [A] The RFC 0007 sections Butler Detail Tabs, Data Access and Refresh, Command Palette and Global Shell are stale against the specs. Should they be deleted from the RFC and replaced with pointers? That is an RFC amendment outside this spec-only audit.
6. [A] The admin-gateway 'Secrets and Credentials Management' requirement describes a target selector and category-priority grouping that the passport redesign may have removed. Unknown. Reading frontend/src/pages/SecretsPage.tsx and passport/pages.tsx against :9-40 would resolve it.
7. [A] The relationship Finder cites /api/relationship/entities/search, but the frontend calls /api/butlers/relationship/entities/search. Unknown whether both mounts exist. Checking router_discovery's mount prefix would resolve it.
8. [B] Is the per-butler self-healing direct-dispatch fallback still wanted? It has no spec of its own (module docstring cites archived change paths), no roster butler configures it, yet load_all likely enables it everywhere. Resolve by deciding keep-and-specify inside qa-investigation or retire the fallback.
9. [B] Should RFC 0015 or the QA specs hold the discovery-source table and dedup order? Recommendation: spec holds the SHALLs, RFC keeps rationale and protocol shape only.
10. [B] Should dashboard-page specs in this cluster (qa-dashboard, system-overview-page, owner-timezone-context, runtime-config-dashboard-ui) move to the A-dashboard cluster under bu-60pwv6.6?
11. [B] Briefing dates are hard-coded to SGT while the dashboard uses the owner's configured timezone. Is SGT intentional for briefing or should it follow owner timezone? Unknown; resolve by owner decision.
12. [B] Fold switchboard-identity into butler-switchboard (cluster C), and owner-condition-ledger with infrastructure-reliability's infra ledger into one condition-ledger spec? Both share an engine or pipeline.
13. [B] core-notify has 9 active change deltas; should the attention-ledger/quiet-hours split wait until they archive? Recommendation: yes, to avoid rebasing 9 deltas.
14. [C1] Are per-butler inventories (port, modules, tools, schedules, skills) meant to be spec-enforced against butler.toml, or is butler.toml plus the skills dir the sole source? The rewrite of 8 butler specs depends on this.
15. [C1] Discord: retire connector-discord into an RFC 0018 deferral note, or keep a TARGET-STATE stub spec? Code is a non-production draft with no deployment.
16. [C1] metrics and document_renderer modules are loaded by no roster butler.toml. Keep their specs, or retire spec and code per v1.md 'no modules no butler uses'? (Unknown whether they are enabled via runtime_config; a DB check would resolve it.)
17. [C1] Wellness envelope identity: is endpoint_identity/idempotency keyed by Google user id or account email, and is dispatch via handler registration or the generic route-execute path?
18. [C1] WhatsApp has a connector, a module, docs/connectors/whatsapp.md and a doctrine entry but no capability spec in this cluster. Add connector-whatsapp/module-whatsapp specs, or is it intentionally docs-only?
19. [C1] Should about/heart-and-soul/v1.md drop its module/connector inventory entirely and link to about/lay-and-land/components.md?
20. [C1] Sequencing: butler-switchboard (6), database-security (9), connector-telegram-bot (4), module-telegram (4), butler-messenger (4) have active changes. Consolidate after those land, or fold the consolidation into them?
21. [C2] Are the per-schema memory_policies (retention_class) and public.memory_retention_policies (kind, ttl_days, max_rows) meant to coexist? If yes, which one governs an episode that both match; if no, which is retired? (Unknown: resolved by owner decision.)
22. [C2] Which bulk finance path is canonical: SPO facts (facts.py bulk_record_transactions, /transactions/bulk) or finance.transactions (transactions.py bulk_record_transactions)? This decides whether bulk-transaction-ingestion is rewritten or retired with the SPO mirror.
23. [C2] Budget periods: is quarterly a real requirement? The tool accepts it but the migration constraint rejects it, so either the constraint or the tool is a live bug.
24. [C2] Credentials: should public.entity_info secured rows (Google refresh tokens) migrate into relationship.credentials, or is entity_info the sanctioned exception for module credentials?
25. [C2] Should consolidation of the education specs wait for, or be folded into, active change education-mind-map-lifecycle-integrity, which already rewrites the map lifecycle in three of them?
26. [C2] Is home-assistant-person-mapping implemented and approved (a router exists), making its gating requirement stale, or is the router itself ahead of the gate?
27. [C2] Is the switchboard routing benchmark still run? If not, routing-scorecard and scorecard-reporting retire instead of merging.
28. [C2] Should preferences be memory facts (user-preferences) or relationship entity triples like prefers-channel?
29. [D] Amend doctrine architecture.md to admit on-demand, column-allowlisted read exceptions (RFC 0030), or narrow RFC 0030 back to batch?
30. [D] Is the k3s beads-export CronJob an interim rollback path under RFC 0025, or does it supersede RFC 0025's tracker-host PostgreSQL projection?
31. [D] Should implemented RFCs with no spec home (0022 fleet-event bridge, 0031 entity graph, 0032 fleet case file, 0023 delivery intent) get capability specs so the RFCs can condense, or stay the WHAT of record?
32. [D] Move connector scope (0018) into heart-and-soul/v1.md and update its roster to include ActivityWatch and SimpleFIN?
33. [D] Strip the incident narrative and quoted personal email from RFC 0017?
