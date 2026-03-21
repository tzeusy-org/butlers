## 1. Scaffold Target Directory Structure

- [x] 1.1 Create all target directories under `docs/`: `overview/`, `getting_started/`, `concepts/`, `architecture/`, `runtime/`, `butlers/`, `modules/`, `connectors/`, `frontend/`, `data_and_storage/`, `identity_and_secrets/`, `api_and_protocols/`, `operations/`, `testing/`, `roadmap/`, `diagrams/` (with subdirectories: `architecture/`, `runtime/`, `butlers/`, `modules/`, `connectors/`, `frontend/`, `identity/`, `operations/`, `testing/`), `archive/`
- [x] 1.2 Create `docs/index.md` with the linear reading path and grouped topic lookup table, linking to all category index pages (pages will be stubs initially)
- [x] 1.3 Create stub `index.md` for each top-level category with scope statement (what belongs, what does not) per the spec

## 2. Write New Core Content — Overview, Getting Started, Concepts

- [x] 2.1 Write `docs/overview/what-is-butlers.md` — system overview for newcomers, extracted and rewritten from README.md introduction/architecture sections. Generate system overview diagram via `/excalidraw-diagram` (dark mode, SVG).
- [x] 2.2 Write `docs/overview/project-goals.md` — motivation, design philosophy, status, link to roadmap
- [x] 2.3 Write `docs/getting_started/prerequisites.md` — system dependencies, LLM runtime CLIs, credential requirements, extracted from README Prerequisites section
- [x] 2.4 Write `docs/getting_started/dev-environment.md` — step-by-step dev setup using `uv sync`, `docker compose`, `dev.sh`, extracted from README Getting Started section
- [x] 2.5 Write `docs/getting_started/first-butler-launch.md` — launch a butler, verify it works, trigger it, view session log
- [x] 2.6 Write `docs/getting_started/dashboard-access.md` — start dashboard API + frontend, navigate the UI
- [x] 2.7 Write `docs/concepts/butler-lifecycle.md` — butler states, daemon model, trigger→spawn→session→idle cycle. Generate butler lifecycle diagram.
- [x] 2.8 Write `docs/concepts/modules-and-connectors.md` — what modules are vs what connectors are, how they relate, when to use each. Generate relationship diagram.
- [x] 2.9 Write `docs/concepts/switchboard-routing.md` — how messages enter the system and get routed. Generate trigger flow diagram.
- [x] 2.10 Write `docs/concepts/trigger-flow.md` — external MCP call vs scheduler trigger, request context, session creation
- [x] 2.11 Write `docs/concepts/identity-model.md` — owner, contacts, contact_info, tenant model, shared schema
- [x] 2.12 Write `docs/concepts/mcp-model.md` — what MCP is in the Butlers context, how tools are registered, how Claude interacts

## 3. Migrate and Rewrite Architecture Docs

- [x] 3.1 Rewrite `docs/roles/base_butler.md` → `docs/architecture/butler-daemon.md` — reframe as architecture doc covering daemon internals, startup sequence, core components. Generate startup sequence diagram.
- [x] 3.2 Split `docs/roles/switchboard_butler.md` → `docs/architecture/routing.md` (routing architecture) + `docs/butlers/switchboard.md` (butler profile). Generate switchboard routing flow diagram.
- [x] 3.3 Write `docs/architecture/system-topology.md` — overall system topology, service ports, inter-service communication. Generate topology diagram.
- [x] 3.4 Write `docs/architecture/database-design.md` — shared schema, per-butler schemas, JSONB patterns, multi-schema isolation. Generate database schema topology diagram.
- [x] 3.5 Move `docs/switchboard/email_priority_queuing.md` → `docs/architecture/email-priority-queuing.md`, apply page template
- [x] 3.6 Move `docs/switchboard/pre_classification_triage.md` → `docs/architecture/pre-classification-triage.md`, apply page template
- [x] 3.7 Move `docs/switchboard/thread_affinity_routing.md` → `docs/architecture/thread-affinity-routing.md`, apply page template
- [x] 3.8 Write `docs/architecture/observability.md` — OpenTelemetry, Grafana, Tempo/Loki setup, trace propagation via `traceparent`

## 4. Write Runtime Docs

- [x] 4.1 Write `docs/runtime/spawner.md` — LLM CLI spawner flow: lock acquisition, session record, MCP config generation, SDK invocation, result parsing. Generate spawner execution flow diagram.
- [x] 4.2 Split `docs/core/scheduler.md` → `docs/runtime/scheduler-execution.md` (runtime behavior: tick loop, dispatch, staggering). Generate scheduler tick/dispatch diagram.
- [x] 4.3 Write `docs/runtime/session-lifecycle.md` — session creation, logging, tool call capture, completion. Generate session lifecycle diagram.
- [x] 4.4 Write `docs/runtime/model-routing.md` — model catalog, model selection, complexity classification, token limits
- [x] 4.5 Write `docs/runtime/tool-call-capture.md` — how tool calls are intercepted, logged, and analyzed

## 5. Migrate Butler Role Docs

- [x] 5.1 Rewrite `docs/roles/relationship_butler.md` → `docs/butlers/relationship.md` — butler profile format (purpose, port, modules, tools summary, schedule, interaction patterns)
- [x] 5.2 Rewrite `docs/roles/messenger_butler.md` → `docs/butlers/messenger.md`
- [x] 5.3 Rewrite `docs/roles/finance_butler.md` → `docs/butlers/finance.md`
- [x] 5.4 Rewrite `docs/roles/travel_butler.md` → `docs/butlers/travel.md`
- [x] 5.5 Write `docs/butlers/general.md` — general butler profile (no existing role doc)
- [x] 5.6 Write `docs/butlers/health.md` — health butler profile (no existing role doc, infer from roster config)
- [x] 5.7 Write `docs/butlers/education.md` — education butler profile
- [x] 5.8 Write `docs/butlers/home.md` — home butler profile

## 6. Migrate and Rewrite Module Docs

- [x] 6.1 Write `docs/modules/module-system.md` — module ABC, lifecycle hooks, dependency resolution (topological sort), migration branching, tool registration. Generate module dependency diagram.
- [x] 6.2 Rewrite `docs/modules/memory.md` → `docs/modules/memory.md` — apply page template, preserve strong content, add diagram
- [x] 6.3 Rewrite `docs/modules/calendar.md` → `docs/modules/calendar.md` — apply page template
- [x] 6.4 Rewrite `docs/modules/contacts.md` → `docs/modules/contacts.md` — apply page template
- [x] 6.5 Rewrite `docs/modules/approval.md` → `docs/modules/approvals.md` — apply page template. Relocate `docs/modules/approval-flow.excalidraw` to `docs/diagrams/modules/`.
- [x] 6.6 Rewrite `docs/modules/knowledge_base/` → `docs/modules/knowledge-base.md` — flatten to single page, apply page template. Relocate diagrams.
- [x] 6.7 Write `docs/modules/email.md` — email module profile (exists in code but lacks dedicated doc)
- [x] 6.8 Write `docs/modules/telegram.md` — telegram module profile
- [x] 6.9 Write `docs/modules/mailbox.md` — mailbox module profile
- [x] 6.10 Write `docs/modules/metrics.md` — metrics/prometheus module profile
- [x] 6.11 Write `docs/modules/pipeline.md` — pipeline module profile

## 7. Migrate and Rewrite Connector Docs

- [x] 7.1 Split `docs/connectors/interface.md` → `docs/connectors/overview.md` (connector architecture) + `docs/api_and_protocols/ingestion-envelope.md` (ingest.v1 protocol). Generate connector→switchboard ingestion pipeline diagram.
- [x] 7.2 Rewrite `docs/connectors/telegram_bot.md` → `docs/connectors/telegram-bot.md` — apply page template
- [x] 7.3 Rewrite + merge `docs/connectors/telegram_user_client.md` + `docs/connectors/telegram_user_client_deployment.md` → `docs/connectors/telegram-user-client.md`
- [x] 7.4 Rewrite + merge `docs/connectors/gmail.md` + `docs/connectors/email_backfill.md` → `docs/connectors/gmail.md`
- [x] 7.5 Move `docs/connectors/email_ingestion_policy.md` → `docs/connectors/gmail-ingestion-policy.md`, apply page template
- [x] 7.6 Move `docs/connectors/heartbeat.md` → `docs/connectors/heartbeat.md`, apply page template
- [x] 7.7 Write `docs/connectors/live-listener.md` — live listener connector profile (exists in code, no doc)
- [x] 7.8 Move `docs/connectors/attachment_handling.md` → `docs/connectors/attachment-handling.md`, apply page template
- [x] 7.9 Move `docs/connectors/statistics.md` → `docs/connectors/metrics.md`, apply page template
- [x] 7.10 Move `docs/connectors/horizontal_scaling.md` → `docs/operations/connector-scaling.md`

## 8. Migrate Frontend Docs

- [x] 8.1 Move `docs/frontend/*.md` → `docs/frontend/` (preserve existing structure), add page template headers to each file
- [x] 8.2 Create `docs/frontend/index.md` (if not already serving as README.md equivalent)

## 9. Write Data, Storage, and Identity Docs

- [x] 9.1 Write `docs/data_and_storage/schema-topology.md` — shared schema vs per-butler schemas, PostgreSQL setup, JSONB patterns. Generate schema topology diagram.
- [x] 9.2 Write `docs/data_and_storage/migration-patterns.md` — Alembic conventions, module migration branching, migration ordering
- [x] 9.3 Write `docs/data_and_storage/state-store.md` — KV JSONB state store design, usage patterns
- [x] 9.4 Write `docs/data_and_storage/blob-storage.md` — blob/attachment storage
- [x] 9.5 Write `docs/data_and_storage/credential-store.md` — how credentials are persisted, CLI auth token store
- [x] 9.6 Write `docs/identity_and_secrets/owner-identity.md` — owner contact bootstrap, contact_info identifiers, dashboard setup flow. Generate owner identity bootstrap diagram.
- [x] 9.7 Write `docs/identity_and_secrets/contact-system.md` — contacts, contact_info, roles, identity resolution for routing
- [x] 9.8 Write `docs/identity_and_secrets/oauth-flows.md` — Google OAuth device-code flow, calendar/contacts/gmail grant. Generate OAuth flow diagram.
- [x] 9.9 Write `docs/identity_and_secrets/cli-runtime-auth.md` — CLI runtime authentication (Claude, Codex, Gemini), dashboard Settings page, health probes
- [x] 9.10 Write `docs/identity_and_secrets/environment-variables.md` — comprehensive env var reference (global, butler-specific, module-specific), extracted from README

## 10. Write API and Protocol Docs

- [x] 10.1 Write `docs/api_and_protocols/mcp-tools.md` — MCP tool registration patterns, how tools are exposed, tool naming conventions
- [x] 10.2 Write `docs/api_and_protocols/dashboard-api.md` — dashboard REST API overview, router discovery, auth, SSE events
- [x] 10.3 Write `docs/api_and_protocols/inter-butler-communication.md` — how butlers communicate via Switchboard MCP, no direct DB access

## 11. Write Operations Docs

- [x] 11.1 Write `docs/operations/docker-deployment.md` — Docker/docker-compose setup, production deployment, service ports, container architecture. Generate deployment topology diagram.
- [x] 11.2 Write `docs/operations/environment-config.md` — complete environment configuration reference, secrets directory structure, `.env.example`
- [x] 11.3 Write `docs/operations/grafana-monitoring.md` — Grafana dashboards, Tempo tracing, Loki logging, OTLP endpoint config
- [x] 11.4 Write `docs/operations/troubleshooting.md` — common issues, debugging checklist, log locations, health checks

## 12. Migrate Testing Docs

- [x] 12.1 Write `docs/testing/testing-strategy.md` — test pyramid, unit vs integration vs E2E, marker-based runs, quality gates. Generate test pyramid diagram.
- [x] 12.2 Write `docs/testing/markers-and-fixtures.md` — pytest markers, shared fixtures, testcontainers usage, parallel test execution
- [x] 12.3 Move `docs/tests/e2e/*.md` → `docs/testing/e2e/`, apply page template headers
- [x] 12.4 Move `docs/tests/benchmark-report.md` → `docs/testing/benchmark-report.md`, apply page template
- [x] 12.5 Move `docs/tests/test-audit-report.md` → `docs/testing/test-audit-report.md`, apply page template

## 13. Write Roadmap Docs

- [x] 13.1 Write `docs/roadmap/project-plan.md` — link to PROJECT_PLAN.md, milestone overview, current status
- [x] 13.2 Write `docs/roadmap/openspec-overview.md` — what OpenSpec is, how it's used in this project, link to `openspec/` directory

## 14. Reorganize Diagrams

- [x] 14.1 Move existing `.excalidraw` source files from `docs/diagrams/` flat folder to topic-based subdirectories in `docs/diagrams/<category>/` (architecture, runtime, butlers, modules, connectors, frontend, identity, operations, testing)
- [x] 14.2 Redistribute existing `.svg` exports to co-locate with their referencing markdown pages
- [x] 14.3 Remove the flat `docs/diagrams/` numbering scheme and reconciliation report file

## 15. Archive Stale Content

- [x] 15.1 Move `docs/modules/health_wearable_draft.md` → `docs/archive/health-wearable-draft.md` with archival notice
- [x] 15.2 Move `docs/modules/home_assistant_draft.md` → `docs/archive/home-assistant-draft.md` with archival notice
- [x] 15.3 Move `docs/modules/photos_screenshots_draft.md` → `docs/archive/photos-screenshots-draft.md` with archival notice
- [x] 15.4 Move `docs/modules/voice_draft.md` → `docs/archive/voice-draft.md` with archival notice
- [x] 15.5 Move `docs/modules/whatsapp_draft.md` → `docs/archive/whatsapp-draft.md` with archival notice
- [x] 15.6 Move `docs/modules/memory_improvements.md` + `memory_improvements_pt2.md` → `docs/archive/` with archival notice
- [x] 15.7 Move `docs/reconciliation/*.md` → `docs/archive/reconciliation/` with archival notices
- [x] 15.8 Move `docs/superpowers/*.md` → `docs/archive/superpowers/` with archival notice
- [x] 15.9 Move `docs/connectors/connector_ingestion_migration_delta_matrix.md` → `docs/archive/` with archival notice
- [x] 15.10 Move `docs/connectors/draft_discord.md` → `docs/archive/draft-discord.md` with archival notice
- [x] 15.11 Move `docs/archive/2026-02-18_switchboard_*.md` files — rename to kebab-case, add archival notices if missing

## 16. Thin README.md

- [x] 16.1 Reduce README.md Architecture section to a brief summary paragraph + link to `docs/architecture/system-topology.md`
- [x] 16.2 Reduce README.md Prerequisites/Getting Started sections to a brief summary + link to `docs/getting_started/`
- [x] 16.3 Reduce README.md Environment Variables section to a brief summary + link to `docs/identity_and_secrets/environment-variables.md` and `docs/operations/environment-config.md`
- [x] 16.4 Reduce README.md Testing/E2E sections to a brief summary + link to `docs/testing/`
- [x] 16.5 Add prominent link to `docs/index.md` near the top of README.md

## 17. Validate and Finalize

- [x] 17.1 Validate all internal cross-references — scan for broken relative links across all `docs/` markdown files
- [x] 17.2 Verify every old file under `docs/` has been migrated — no file remains in its original location without explicit disposition
- [x] 17.3 Verify `docs/index.md` covers all categories and all pages are reachable from the index
- [x] 17.4 Verify every page that describes architecture, flows, or lifecycle has at least one diagram
- [x] 17.5 Verify every page follows the page template standard (purpose, audience, prerequisites where applicable)
- [x] 17.6 Remove empty old directories after all content has been migrated
- [x] 17.7 Update any OpenSpec spec files that reference old `docs/roles/*.md` paths to use new `docs/` paths
