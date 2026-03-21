## Context

The Butlers project has outgrown its documentation structure. The codebase contains 10 butler roles, 8+ modules (memory, email, telegram, calendar, contacts, approvals, mailbox, metrics, self-healing, pipeline), 6+ connectors (telegram_bot, telegram_user_client, gmail, discord_user, live_listener, heartbeat), a React dashboard frontend, a full E2E benchmarking system, an OpenSpec planning layer, and infrastructure for Docker, Grafana, and Alembic migrations. The docs tree has not kept pace.

### Current docs structure (11 directories, ~90 files)

```
docs/
  archive/          # 2 files — switchboard migration artifacts
  connectors/       # 14 files — mix of specs, drafts, and policy docs
  core/             # 1 file — scheduler spec only
  diagrams/         # 20+ excalidraw/svg pairs, flat numbered folder
  frontend/         # 6 files — well-structured spec set
  modules/          # 12 files — mix of normative specs, drafts, improvements
  reconciliation/   # 2 files — gen-1 self-healing notes
  roles/            # 6 files — normative butler role definitions
  superpowers/      # 1 file — discretion layer migration plan
  switchboard/      # 3 files — switchboard-specific routing docs
  tests/            # 3 files + e2e/ subfolder (12 files)
```

### Problems with current structure

1. **Implementation-shaped taxonomy.** Categories mirror code directories (`core/`, `modules/`, `connectors/`) rather than how someone learns the system. There is no "what is this?", no "how do I run it?", and no clear reading order.
2. **No entry point.** No `docs/index.md`. README.md is 630 lines and tries to be the entry point, setup guide, architecture doc, CLI reference, and testing guide simultaneously.
3. **Draft/normative confusion.** `docs/modules/` contains both `memory.md` (normative target-state spec) and `health_wearable_draft.md` (speculative draft) side by side with no status indicator in the filename or directory structure.
4. **Diagrams disconnected from content.** 20+ diagrams in `docs/diagrams/` with numeric prefixes (`01-system-architecture.svg`, `06b-scheduler.svg`) but no inline references from the pages they explain. A reader must guess which diagram belongs to which concept.
5. **Missing critical content.** No system overview for newcomers. No concepts glossary. No identity/secrets setup guide. No storage topology explanation. No operations runbook. No testing strategy overview. No getting-started guide beyond README fragments.
6. **Scattered operational knowledge.** Credential setup is split between README (env vars section), `docs/connectors/` (per-connector secrets), and dashboard references. Deployment info is in README + docker-compose + scattered references.
7. **Orphaned content.** `docs/reconciliation/` and `docs/superpowers/` contain one-off documents that don't belong in a permanent taxonomy.
8. **Redundancy between docs/ and openspec/specs/.** The `docs/roles/` directory contains normative specs for each butler, while `openspec/specs/` contains separate spec files. These serve different purposes (human-readable docs vs. change-tracking specs) but the boundary is unclear to newcomers.

### Stakeholders

- **New contributors** — need onboarding path and mental model
- **Existing developers** — need reference lookup and operational guidance
- **Operators** — need setup, deployment, credential, and monitoring docs
- **AI agents** — need clear specs to work against (served by OpenSpec, not docs/)

## Goals / Non-Goals

**Goals:**
- Establish a contributor-mental-model-oriented docs taxonomy that a new engineer can follow sequentially
- Create a docs index that supports both linear reading and targeted lookup
- Co-locate diagrams with their content rather than in a separate flat folder
- Clearly separate normative specs from drafts and archived material
- Fill content gaps: overview, getting-started, concepts, identity/secrets, storage, operations, testing strategy
- Define page-level and diagram-level standards that every docs page follows
- Provide a concrete migration plan for every existing docs file
- Establish a maintenance contract that prevents future docs drift
- Thin README.md to a concise entry point that links into docs/

**Non-Goals:**
- Rewriting OpenSpec spec files (`openspec/specs/`). These are a separate system with different purposes and audiences.
- Modifying butler-roster config files (`roster/*/CLAUDE.md`, `MANIFESTO.md`). These are butler personality/prompt files, not documentation.
- Writing user-facing product docs or marketing material. The audience is developers and operators.
- Building a docs site generator (MkDocs, Docusaurus, etc.). Markdown files in `docs/` are the deliverable; site generation is a future enhancement.
- Achieving 100% coverage of every implementation detail. The docs should explain the system, not exhaustively catalog every function.

## Decisions

### 1. Target information architecture

The new `docs/` taxonomy has 17 top-level categories organized by how a contributor learns the system, not by how the code is laid out.

| Category | Purpose | What belongs | What does NOT belong | Current docs migrating here |
|---|---|---|---|---|
| `overview/` | What is Butlers? High-level system description for newcomers | System overview, project goals, product shape, key terminology at a glance | Deep architecture, setup instructions, specs | *New content* (thin from README) |
| `getting_started/` | How do I run this? First-time setup | Prerequisites, installation, dev environment setup, first butler launch, dashboard access | Production deployment, module development, debugging | *New content* (extract from README) |
| `concepts/` | Core mental model before diving into details | Butler lifecycle, modules vs connectors, switchboard routing model, MCP model, trigger flow, tenant/identity model, state store model | Implementation details, API schemas, deployment procedures | *New content* (synthesized from README + existing docs) |
| `architecture/` | System-level design — the big picture | System topology, database schema design, daemon internals, startup sequence, MCP protocol, multi-schema isolation, observability architecture | Per-butler details, per-module details, operational procedures | Parts of `docs/roles/base_butler.md`, `docs/core/scheduler.md` (architecture sections), README architecture sections |
| `runtime/` | How the system behaves when running | Daemon lifecycle, spawner flow, session management, scheduler execution, tick loop, trigger dispatch, tool call capture, model routing | Static config, initial setup, testing | `docs/core/scheduler.md` (runtime sections), spawner docs from README |
| `butlers/` | Per-butler role descriptions | One page per butler: purpose, tools, schedule, modules used, interaction patterns | Module internals, connector details, code-level specs | `docs/roles/switchboard_butler.md`, `docs/roles/relationship_butler.md`, `docs/roles/messenger_butler.md`, `docs/roles/finance_butler.md`, `docs/roles/travel_butler.md`, `docs/roles/base_butler.md` |
| `modules/` | Pluggable capability units | Module system overview, then per-module: purpose, config, tools provided, DB tables, dependencies | Connector transport details, dashboard UI details | `docs/modules/memory.md`, `docs/modules/calendar.md`, `docs/modules/contacts.md`, `docs/modules/approval.md`, `docs/modules/knowledge_base/` |
| `connectors/` | External transport adapters | Connector architecture overview, per-connector: setup, config, ingestion flow, cursor management, heartbeat | Switchboard routing logic (that's architecture), module internals | `docs/connectors/interface.md`, `docs/connectors/telegram_bot.md`, `docs/connectors/telegram_user_client.md`, `docs/connectors/gmail.md`, `docs/connectors/heartbeat.md`, `docs/connectors/statistics.md` |
| `frontend/` | Dashboard UI | Purpose, information architecture, feature inventory, data access patterns, backend API contract | Backend implementation, module internals | `docs/frontend/*.md` (largely intact — already well-structured) |
| `data_and_storage/` | Database, migrations, blob storage | PostgreSQL topology, shared schema, per-butler schemas, Alembic migration patterns, module migration conventions, blob storage, state store internals | Module-specific table details (those go in modules/) | *New content* + extracts from architecture docs |
| `identity_and_secrets/` | Who you are and how credentials work | Owner identity setup, contact system, credential store, OAuth flows (Google, CLI runtimes), env var management, secured contact_info, dashboard secrets management | Per-connector auth details (link to connectors/) | *New content* (extract from README + `docs/connectors/` credential sections) |
| `api_and_protocols/` | MCP, ingestion, dashboard API surface | MCP tool registration patterns, ingestion envelope format (ingest.v1), dashboard REST API overview, SSE events, inter-butler communication model | Full API schema docs (those belong in code/OpenAPI) | `docs/connectors/interface.md` (protocol sections), `docs/switchboard/` docs |
| `operations/` | Running Butlers in production | Docker deployment, docker-compose topology, Grafana/Tempo setup, environment variables, health monitoring, scaling notes, troubleshooting, log management, backup/restore | First-time dev setup (that's getting_started/), module development | *New content* (extract from README + docker-compose + Grafana configs) |
| `testing/` | How testing works | Testing strategy, test pyramid, pytest markers, unit vs integration vs E2E, fixture patterns, benchmarking, scorecard system, adding tests for new features | Test implementation details, per-test-file docs | `docs/tests/benchmark-report.md`, `docs/tests/test-audit-report.md`, `docs/tests/e2e/*.md` |
| `roadmap/` | What's planned and evolving | Project plan, milestone status, experimental features, OpenSpec overview, links to active changes | Completed/archived changes, implementation details | Reference to `PROJECT_PLAN.md`, links to `openspec/` |
| `diagrams/` | Source-of-truth for all diagram source files | All `.excalidraw` source files, organized by topic. SVG exports co-located with content pages, but source files centralized here for tooling | Text content, narrative docs | `docs/diagrams/*.excalidraw` (reorganized by topic subdirectory) |
| `archive/` | Superseded, draft, and historical material | Completed migration artifacts, draft specs that were never implemented, superseded designs, one-off reconciliation docs | Anything still normative or actively referenced | `docs/archive/`, `docs/reconciliation/`, `docs/superpowers/`, draft files from `docs/modules/` |

### 2. Diagram co-location strategy

**Decision:** Diagram *source files* (`.excalidraw`) remain in `docs/diagrams/` organized by topic subdirectory. Diagram *exports* (`.svg`) are co-located next to the markdown page that references them.

**Rationale:**
- Co-locating SVG exports makes them easy to embed with relative paths (`![](./system-overview.svg)`)
- Centralizing source files in `docs/diagrams/` makes batch diagram regeneration possible (e.g., via `/update-architectural-diagrams`)
- The current flat numbering scheme (`01-`, `02-`, `03a-`) is replaced by topic-based subdirectories (`docs/diagrams/architecture/`, `docs/diagrams/runtime/`, etc.)

**Alternatives considered:**
- Fully co-locate source files alongside markdown → rejected because it clutters content directories and makes batch tooling harder
- Keep current flat folder → rejected because 20+ diagrams with numeric prefixes are hard to navigate and the numbering breaks as content reorganizes

### 3. Page template standard

Every documentation page SHALL follow this template where sections are applicable:

```markdown
# Page Title

> **Purpose:** One-sentence description of what this page explains.
> **Audience:** Who should read this (e.g., new contributors, operators, module developers).
> **Prerequisites:** What the reader should have read first (links).

## Overview

Brief orientation paragraph — what this is and why it matters.

![Diagram caption](./diagram-name.svg)

## [Content sections — varies by page]

## Operational Caveats

Anything a reader needs to be careful about in practice.

## Related Pages

- [Link to related page](../category/page.md) — brief description of relationship

## Verification

How to confirm this page is still accurate (e.g., "Run `butlers list` and verify the output matches the butler table above").
```

**Rationale:** Consistent structure makes pages scannable and sets reader expectations. The "Verification" section is unusual but critical for a fast-evolving codebase — it gives future maintainers a concrete way to check if the page has drifted.

**Adaptations:**
- Getting-started pages use a step-by-step format instead of the overview/sections pattern
- Per-butler and per-module pages use a structured profile format (name, purpose, port, modules, tools, schedule)
- Archive pages get a frontmatter-style archival notice at the top

### 4. Diagram generation standard

**Decision:** All new and refreshed diagrams SHALL be generated via `/excalidraw-diagram`, exported as SVG, and generated in dark mode.

| Aspect | Standard |
|---|---|
| Tool | `/excalidraw-diagram` |
| Export format | SVG |
| Color mode | Dark mode |
| Style | Simple, scoped, explanatory — not dense architecture posters |
| Frequency | Roughly one diagram per page where visual explanation materially helps |
| Caption | Every diagram has a descriptive caption in the markdown |
| Naming | `<topic>-<aspect>.excalidraw` / `<topic>-<aspect>.svg` (kebab-case) |
| Source location | `docs/diagrams/<category>/` matching the docs taxonomy |
| Export location | Co-located with the referencing markdown page |
| Preferred types | Flowcharts, sequence diagrams, topology sketches, state machines, data flow, lifecycle diagrams, ownership boundaries |

**Diagrams required by topic area (minimum set):**

| Page area | Diagram subject |
|---|---|
| Overview | High-level system topology (butlers, switchboard, dashboard, connectors, DB) |
| Concepts | Butler lifecycle (init → running → trigger → spawn → session → idle) |
| Concepts | Module vs connector relationship |
| Concepts | Trigger flow (external request → switchboard → routing → butler → response) |
| Architecture | Database schema topology (shared schema + per-butler schemas) |
| Architecture | Daemon startup sequence (12-step boot) |
| Architecture | MCP tool registration flow |
| Runtime | Spawner execution flow (lock → session → config gen → SDK invoke → parse → log) |
| Runtime | Scheduler tick/dispatch cycle |
| Runtime | Session lifecycle |
| Butlers | Switchboard routing flow (ingress → classify → fanout → collect → respond) |
| Modules | Module dependency resolution (topological sort) |
| Connectors | Connector → Switchboard ingestion pipeline |
| Frontend | Dashboard information architecture (route map) |
| Identity | Owner identity bootstrap flow (first start → seed contact → add identifiers) |
| Identity | OAuth device-code authentication flow |
| Data/Storage | Alembic migration branching model |
| Operations | Docker deployment topology |
| Testing | Test pyramid (unit → integration → E2E → benchmark) |

### 5. Migration strategy

Each existing docs file gets exactly one disposition:

| Disposition | Meaning | When to use |
|---|---|---|
| **Move** | Relocate to new path, minimal content changes | Content is sound but in wrong category |
| **Rewrite** | New file, informed by old content but restructured to page template | Content exists but needs restructuring for new audience/template |
| **Split** | One old file becomes multiple new files | File covers multiple distinct topics |
| **Merge** | Multiple old files become one new file | Files cover overlapping content |
| **Archive** | Move to `docs/archive/` with archival notice | Draft, superseded, or historical-only |
| **Delete** | Remove entirely | Truly obsolete, no historical value |

**Migration table (current → target):**

| Current path | Disposition | Target path(s) | Notes |
|---|---|---|---|
| `docs/core/scheduler.md` | Split | `docs/architecture/scheduler-design.md` + `docs/runtime/scheduler-execution.md` | Architecture vs runtime separation |
| `docs/roles/base_butler.md` | Rewrite | `docs/architecture/butler-daemon.md` | Reframe as architecture doc, not role contract |
| `docs/roles/switchboard_butler.md` | Split | `docs/butlers/switchboard.md` + `docs/architecture/routing.md` | Butler profile + routing architecture |
| `docs/roles/relationship_butler.md` | Rewrite | `docs/butlers/relationship.md` | Butler profile format |
| `docs/roles/messenger_butler.md` | Rewrite | `docs/butlers/messenger.md` | Butler profile format |
| `docs/roles/finance_butler.md` | Rewrite | `docs/butlers/finance.md` | Butler profile format |
| `docs/roles/travel_butler.md` | Rewrite | `docs/butlers/travel.md` | Butler profile format |
| `docs/modules/memory.md` | Rewrite | `docs/modules/memory.md` | Restructure to page template; keep strong content |
| `docs/modules/memory_improvements.md` | Archive | `docs/archive/memory_improvements.md` | Historical improvement notes |
| `docs/modules/memory_improvements_pt2.md` | Archive | `docs/archive/memory_improvements_pt2.md` | Historical |
| `docs/modules/calendar.md` | Rewrite | `docs/modules/calendar.md` | Page template |
| `docs/modules/contacts.md` | Rewrite | `docs/modules/contacts.md` | Page template |
| `docs/modules/approval.md` | Rewrite | `docs/modules/approval.md` | Page template |
| `docs/modules/knowledge_base/` | Rewrite | `docs/modules/knowledge-base.md` | Flatten to single page unless content warrants subdirectory |
| `docs/modules/health_wearable_draft.md` | Archive | `docs/archive/health_wearable_draft.md` | Draft — not implemented |
| `docs/modules/home_assistant_draft.md` | Archive | `docs/archive/home_assistant_draft.md` | Draft — not implemented |
| `docs/modules/photos_screenshots_draft.md` | Archive | `docs/archive/photos_screenshots_draft.md` | Draft — not implemented |
| `docs/modules/voice_draft.md` | Archive | `docs/archive/voice_draft.md` | Draft — not implemented |
| `docs/modules/whatsapp_draft.md` | Archive | `docs/archive/whatsapp_draft.md` | Draft — not implemented |
| `docs/connectors/interface.md` | Split | `docs/connectors/overview.md` + `docs/api_and_protocols/ingestion-envelope.md` | Connector architecture vs protocol spec |
| `docs/connectors/telegram_bot.md` | Rewrite | `docs/connectors/telegram-bot.md` | Page template |
| `docs/connectors/telegram_user_client.md` | Rewrite | `docs/connectors/telegram-user-client.md` | Page template |
| `docs/connectors/telegram_user_client_deployment.md` | Merge | `docs/connectors/telegram-user-client.md` (deployment section) | Merge into main connector page |
| `docs/connectors/gmail.md` | Rewrite | `docs/connectors/gmail.md` | Page template |
| `docs/connectors/email_backfill.md` | Merge | `docs/connectors/gmail.md` (backfill section) | Operational detail belongs with connector |
| `docs/connectors/email_ingestion_policy.md` | Move | `docs/connectors/gmail-ingestion-policy.md` | Rename for clarity |
| `docs/connectors/heartbeat.md` | Move | `docs/connectors/heartbeat.md` | Minor restructure |
| `docs/connectors/horizontal_scaling.md` | Move | `docs/operations/connector-scaling.md` | Operational concern |
| `docs/connectors/statistics.md` | Move | `docs/connectors/metrics.md` | Rename |
| `docs/connectors/attachment_handling.md` | Move | `docs/connectors/attachment-handling.md` | Rename |
| `docs/connectors/connector_ingestion_migration_delta_matrix.md` | Archive | `docs/archive/connector_ingestion_migration_delta_matrix.md` | Historical migration tracking |
| `docs/connectors/draft_discord.md` | Archive | `docs/archive/draft_discord.md` | Draft — not implemented |
| `docs/frontend/*.md` | Move | `docs/frontend/*.md` | Already well-structured — preserve, add page template headers |
| `docs/switchboard/email_priority_queuing.md` | Move | `docs/architecture/email-priority-queuing.md` | Architectural concern |
| `docs/switchboard/pre_classification_triage.md` | Move | `docs/architecture/pre-classification-triage.md` | Architectural concern |
| `docs/switchboard/thread_affinity_routing.md` | Move | `docs/architecture/thread-affinity-routing.md` | Architectural concern |
| `docs/tests/benchmark-report.md` | Move | `docs/testing/benchmark-report.md` | Minor restructure |
| `docs/tests/test-audit-report.md` | Move | `docs/testing/test-audit-report.md` | Minor restructure |
| `docs/tests/e2e/*.md` | Move | `docs/testing/e2e/*.md` | Preserve structure |
| `docs/archive/*.md` | Keep | `docs/archive/*.md` | Already archived |
| `docs/reconciliation/*.md` | Archive | `docs/archive/reconciliation/` | Historical |
| `docs/superpowers/*.md` | Archive | `docs/archive/superpowers/` | Historical |
| `docs/diagrams/*.excalidraw` | Move | `docs/diagrams/<category>/` (reorganized by topic) | Reorganize from flat to topic-based |
| `docs/diagrams/*.svg` | Move | Co-locate with referencing pages | SVG exports move to content dirs |

### 6. Navigation model

**`docs/index.md`** serves as the single entry point with two modes:

1. **Linear reading path** — a numbered sequence for newcomers:
   1. Overview → 2. Getting Started → 3. Concepts → 4. Architecture → 5. Runtime → 6. (domain areas as needed)

2. **Targeted lookup table** — grouped by topic with one-line descriptions and links to every page

**Cross-linking conventions:**
- Every page links to its logical "next" and "previous" pages for linear readers
- Every page has a "Related Pages" section at the bottom
- Relative links only (`../category/page.md`) — no absolute URLs
- Category-level index pages (`docs/modules/index.md`) list all pages in that category

### 7. Archive strategy

**Belongs in `docs/archive/`:**
- Draft specs for features that were never implemented
- Migration tracking matrices from completed migrations
- Superseded design documents
- One-off reconciliation or investigation artifacts
- Historical improvement plans that have been executed

**Should be deleted outright:**
- Duplicate content that exists nowhere else in better form (none identified currently)
- Generated artifacts that can be regenerated

**Archive format:**
```markdown
> **ARCHIVED** — This document is historical. It was archived on YYYY-MM-DD.
> **Reason:** [Draft never implemented | Superseded by X | Migration complete]
> **Successor:** [Link to replacement doc, if any]

[Original content below]
```

### 8. Maintenance contract

| Trigger | Required doc update |
|---|---|
| New butler added to roster | Add page in `docs/butlers/` |
| New module created | Add page in `docs/modules/` |
| New connector created | Add page in `docs/connectors/` |
| Startup sequence changes | Update `docs/architecture/butler-daemon.md` and startup diagram |
| Database schema changes | Update `docs/data_and_storage/` relevant page |
| Dashboard routes change | Update `docs/frontend/` (existing rule — preserve) |
| New environment variable | Update `docs/operations/environment-variables.md` |
| Ingestion protocol changes | Update `docs/api_and_protocols/ingestion-envelope.md` |

**Diagram freshness rule:** When a docs page is updated, check if its associated diagram still matches. If the diagram is stale, regenerate it via `/excalidraw-diagram` in the same change.

**Normative reference pages:** The following pages are designated as normative references (authoritative for behavior). Changes to these pages require explicit review:
- `docs/architecture/routing.md`
- `docs/api_and_protocols/ingestion-envelope.md`
- `docs/identity_and_secrets/owner-identity.md`
- `docs/data_and_storage/schema-topology.md`

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|---|---|---|
| **Heavy rewrite churn** — touching ~90 files risks introducing broken links and stale cross-references | High | Phase the work: create taxonomy + landing pages first, then migrate content category-by-category, validate links last |
| **Two competing doc systems during transition** — if migration is partial, readers face both old and new paths | High | Migrate category-by-category and redirect old paths immediately. Do NOT leave old files in place after migration. |
| **Stale diagrams** — diagrams generated now will drift as the system evolves | Medium | Maintenance contract requires diagram refresh when associated pages change. `/update-architectural-diagrams` skill exists for batch regeneration. |
| **Over-documenting unstable surfaces** — writing detailed docs for experimental features that will change | Medium | Clearly mark experimental content. Focus depth on stable core (daemon, modules, connectors, identity) and keep experimental areas thin. |
| **Under-documenting runtime behavior** — existing docs focus on static contracts; runtime flow docs are a gap | Medium | Explicit `runtime/` category and dedicated tasks for spawner, scheduler, and session lifecycle docs |
| **Redundancy with OpenSpec specs** — `docs/` and `openspec/specs/` could duplicate content | Low | Clear boundary: `docs/` is for humans learning the system; `openspec/specs/` is for change-tracking and agent-readable contracts. Docs may reference specs but should not duplicate them. |
| **README.md scope creep** — README may regrow after being thinned | Low | README should contain only: project description, badges, quickstart link, and architecture link. Everything else lives in `docs/`. |

## Open Questions

1. **Should `docs/roles/base_butler.md` remain as a normative spec or be fully absorbed into architecture docs?** Current recommendation: absorb into `docs/architecture/butler-daemon.md` and let `openspec/specs/butler-base-spec/` be the canonical normative contract.
2. **Should the frontend docs remain in `docs/frontend/` as-is or be restructured to match the new page template?** Current recommendation: keep the existing structure (it's already well-organized per its own README) but add page template headers for consistency.
3. **Should per-butler pages include full tool lists or link to OpenSpec specs?** Current recommendation: include a summary tool table in the butler page and link to the spec for the full contract.
