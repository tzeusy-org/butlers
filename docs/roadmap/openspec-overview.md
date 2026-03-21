# OpenSpec Overview

> **Purpose:** Explain how OpenSpec is used in Butlers for spec-driven development.
> **Audience:** Contributors proposing changes, reviewers, anyone understanding the planning process.
> **Prerequisites:** None.

## Overview

Butlers uses OpenSpec as its spec-driven development workflow. Every significant change -- new features, architectural modifications, cross-cutting concerns -- flows through a structured pipeline: proposal, specification, design, tasks. This ensures changes are well-reasoned before implementation begins and provides a durable record of design decisions.

## The OpenSpec Pipeline

Each change follows four stages:

### 1. Proposal (`proposal.md`)

A short document (typically 1-2 pages) answering three questions:

- **Why:** What problem exists and why it matters.
- **What Changes:** High-level description of what will be added, modified, or removed.
- **Capabilities:** List of new or modified capabilities, grouped by domain.
- **Impact:** Code changes, schema changes, dependencies.

Proposals are the entry point for discussion. They do not prescribe implementation details.

### 2. Specifications (`specs/{capability}/spec.md`)

Each capability listed in the proposal gets a detailed spec. Specs define:

- **Requirements:** Structured as `## ADDED Requirements` or `## MODIFIED Requirements` sections.
- **Behavior contracts:** What the system must do, expressed as testable assertions.
- **Schema:** Database tables, API payloads, configuration formats.
- **Error handling:** Expected failure modes and recovery behavior.

Specs are normative -- they are the source of truth for what the system should do.

### 3. Design (`design.md`)

The technical design document that explains how the specs will be implemented:

- Architecture decisions and trade-offs.
- Component interactions and data flow.
- Migration strategy for existing data or behavior.
- Performance considerations.

### 4. Tasks (`tasks.md`)

The implementation breakdown:

- Ordered task list with dependencies.
- Each task references specific spec requirements.
- Tasks are sized for single-session work items.

## Directory Structure

OpenSpec content lives under `openspec/` in the repository:

```
openspec/
  changes/
    {change-name}/
      proposal.md
      design.md
      tasks.md
      specs/
        {capability-name}/
          spec.md
    archive/
      {completed-change-name}/
        ...
```

Active changes live directly under `changes/`. Completed changes are moved to `changes/archive/`.

## Active Changes

Current (non-archived) OpenSpec changes include:

- **adapter-integration-test-suites** -- Standardized integration testing patterns for connector adapters.
- **memory-residual-gaps** -- Closing remaining gaps in the tiered memory subsystem.
- **crud-to-spo-migration** -- Migrating entity storage from CRUD operations to subject-predicate-object triples.
- **predicate-registry-enforcement** -- Enforcing a controlled vocabulary for entity predicates.
- **docs-information-architecture-rewrite** -- Reorganizing the documentation from implementation-surface taxonomy to contributor-mental-model taxonomy.

## Archived Changes

Completed changes in the archive include:

- **2026-02-24-alpha-release-mvp** -- The comprehensive baseline spec set capturing the entire alpha system: 70+ capabilities across core infrastructure, connectors, modules, dashboard, butler roles, and testing.
- **2026-02-24-contacts-identity-model** -- Contacts and identity resolution system.
- **multi-account-google** -- Google multi-account OAuth and account registry.
- **connector-live-listener** -- Audio live-listener connector.
- **session-process-logs** -- Session lifecycle and process log capture.
- **dynamic-model-routing** -- LLM model catalog and per-butler model routing.
- **connector-ingestion-request-id** -- Canonical request ID assignment at ingest.
- **education-butler** / **education-dashboard** -- Education butler role and dashboard.
- **transitory-entity-on-fact-storage** -- Entity-first fact storage model.
- **home-assistant-integration** -- Wyoming protocol integration.

## Capability Domains

The alpha baseline spec set organizes capabilities into these domains:

| Domain | Examples |
|--------|----------|
| Core Infrastructure | daemon, state, scheduler, spawner, sessions, modules, credentials, skills, telemetry, notify |
| Connectors | base-spec, telegram-bot, telegram-user-client, gmail, discord |
| Modules | approvals, calendar, contacts, email, mailbox, memory, telegram, pipeline |
| Dashboard | shell, visibility, butler-management, admin-gateway, domain-pages, API |
| Butler Roles | base-spec, switchboard, general, relationship, health, messenger, finance, travel |
| Testing | test infrastructure, E2E plans, benchmarks |

## How to Propose a Change

1. Create a directory under `openspec/changes/{descriptive-name}/`.
2. Write `proposal.md` with Why, What Changes, Capabilities, and Impact sections.
3. After proposal review, write specs for each listed capability.
4. Write `design.md` with technical approach.
5. Write `tasks.md` with implementation breakdown.
6. Implement tasks, referencing spec requirements.
7. When complete, move the change directory to `changes/archive/`.

## Related Pages

- [Project Plan](project-plan.md) -- Overall milestone tracking
- [Testing Strategy](../testing/testing-strategy.md) -- How specs translate to tests
