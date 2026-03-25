---
name: spec-and-spine
description: >
  Ground all implementation work in capability specifications (openspec/). The capability
  specs are the single source of truth for feature planning and development. Use before
  implementing any feature, when detecting spec-code divergence, when evolving specs, or when
  planning new work. Triggers: "check the spec", "what does the spec say", "spec drift",
  "divergence", "reconcile", "does the code match the spec".
---

# Spec and Spine -- Capability Specifications

The `openspec/` directory is the WHAT pillar of the Butlers knowledge architecture. Capability specs are the single source of truth for what the system does and what it promises. Every feature should trace back to a spec.

## Four-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| Doctrine | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| Design Contracts | `about/law-and-lore/` | HOW -- RFCs defining wire-level contracts |
| **Capability Specs** | `openspec/` | WHAT -- normative requirements |
| Topology | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |

## OpenSpec Structure

```
openspec/
  config.yaml        # OpenSpec configuration
  specs/             # Canonical specs (source of truth)
    <domain>/
      spec.md        # The normative specification
  changes/           # Active and archived change proposals
    <change-name>/
      proposal.md    # What and why
      design.md      # How (technical approach)
      tasks.md       # Implementation breakdown
      specs/         # Spec diffs introduced by this change
    archive/         # Completed changes
```

## Domain Lookup

### Core Daemon

| Spec | Path | Covers |
|------|------|--------|
| core-daemon | `openspec/specs/core-daemon/spec.md` | Daemon lifecycle, startup phases, tick handler, health checks |
| core-spawner | `openspec/specs/core-spawner/spec.md` | LLM CLI spawning, ephemeral MCP config, concurrency limits |
| core-scheduler | `openspec/specs/core-scheduler/spec.md` | Cron-based task scheduling, dispatch modes, missed tick handling |
| core-state | `openspec/specs/core-state/spec.md` | KV state store, JSONB persistence |
| core-sessions | `openspec/specs/core-sessions/spec.md` | Session lifecycle, logging, request context |
| core-skills | `openspec/specs/core-skills/spec.md` | Skill discovery, SKILL.md format, skill execution |
| core-telemetry | `openspec/specs/core-telemetry/spec.md` | OTel integration, metrics, traces, cardinality |
| core-modules | `openspec/specs/core-modules/spec.md` | Module ABC, topological sort, lifecycle hooks |
| core-credentials | `openspec/specs/core-credentials/spec.md` | Credential store, secrets management |
| core-notify | `openspec/specs/core-notify/spec.md` | Notification dispatch, contact-based targeting |

### Modules

| Spec | Path | Covers |
|------|------|--------|
| module-email | `openspec/specs/module-email/spec.md` | Email send/receive tools, draft management |
| module-telegram | `openspec/specs/module-telegram/spec.md` | Telegram messaging tools |
| module-calendar | `openspec/specs/module-calendar/spec.md` | Calendar CRUD, conflict detection |
| module-pipeline | `openspec/specs/module-pipeline/spec.md` | Ingestion pipeline, event processing |
| module-mailbox | `openspec/specs/module-mailbox/spec.md` | Internal mailbox for butler-to-butler messages |
| module-approvals | `openspec/specs/module-approvals/spec.md` | Human-in-the-loop approval gates |
| module-contacts | `openspec/specs/module-contacts/spec.md` | Contact management tools |
| module-memory | `openspec/specs/module-memory/spec.md` | Memory store, fact management, entity graph |
| module-metrics | `openspec/specs/module-metrics/spec.md` | Per-butler metrics collection |
| module-home-assistant | `openspec/specs/module-home-assistant/spec.md` | Home Assistant integration |

### Education Domain

| Spec | Path | Covers |
|------|------|--------|
| module-education-curriculum | `openspec/specs/module-education-curriculum/spec.md` | Curriculum management |
| module-education-teaching-flows | `openspec/specs/module-education-teaching-flows/spec.md` | Teaching session flows |
| module-education-mastery | `openspec/specs/module-education-mastery/spec.md` | Mastery tracking |
| module-education-spaced-repetition | `openspec/specs/module-education-spaced-repetition/spec.md` | Spaced repetition engine |
| module-education-mind-map | `openspec/specs/module-education-mind-map/spec.md` | Mind map visualization |
| module-education-diagnostic | `openspec/specs/module-education-diagnostic/spec.md` | Diagnostic assessments |
| module-education-analytics | `openspec/specs/module-education-analytics/spec.md` | Learning analytics |
| butler-education | `openspec/specs/butler-education/spec.md` | Education butler identity and scope |

### Connectors

| Spec | Path | Covers |
|------|------|--------|
| connector-base-spec | `openspec/specs/connector-base-spec/spec.md` | Base connector contract, checkpointing, health |
| connector-gmail | `openspec/specs/connector-gmail/spec.md` | Gmail push/poll integration |
| connector-telegram-bot | `openspec/specs/connector-telegram-bot/spec.md` | Telegram bot connector |
| connector-telegram-user-client | `openspec/specs/connector-telegram-user-client/spec.md` | Telegram user client (Telethon) |
| connector-discord | `openspec/specs/connector-discord/spec.md` | Discord websocket connector |
| connector-live-listener | `openspec/specs/connector-live-listener/spec.md` | Live audio listener connector |
| connector-filtered-events | `openspec/specs/connector-filtered-events/spec.md` | Event filtering for connectors |
| connector-replay-queue | `openspec/specs/connector-replay-queue/spec.md` | Replay queue for failed ingestion |
| connector-source-filter-enforcement | `openspec/specs/connector-source-filter-enforcement/spec.md` | Source filter enforcement |

### Dashboard

| Spec | Path | Covers |
|------|------|--------|
| dashboard-shell | `openspec/specs/dashboard-shell/spec.md` | Dashboard shell, navigation, layout |
| dashboard-domain-pages | `openspec/specs/dashboard-domain-pages/spec.md` | Per-butler domain pages |
| dashboard-visibility | `openspec/specs/dashboard-visibility/spec.md` | Visibility controls, permissions |
| dashboard-api | `openspec/specs/dashboard-api/spec.md` | Backend API contract |
| dashboard-approvals | `openspec/specs/dashboard-approvals/spec.md` | Approval UI |
| dashboard-butler-management | `openspec/specs/dashboard-butler-management/spec.md` | Butler admin UI |
| dashboard-connector-filter-ui | `openspec/specs/dashboard-connector-filter-ui/spec.md` | Connector filter config UI |
| dashboard-education-api | `openspec/specs/dashboard-education-api/spec.md` | Education dashboard API |
| dashboard-education-ui | `openspec/specs/dashboard-education-ui/spec.md` | Education dashboard UI |
| dashboard-google-accounts | `openspec/specs/dashboard-google-accounts/spec.md` | Google account management UI |
| dashboard-model-settings | `openspec/specs/dashboard-model-settings/spec.md` | Model settings UI |
| dashboard-relationship | `openspec/specs/dashboard-relationship/spec.md` | Relationship dashboard |
| dashboard-admin-gateway | `openspec/specs/dashboard-admin-gateway/spec.md` | Admin gateway for dashboard |

### Identity and Routing

| Spec | Path | Covers |
|------|------|--------|
| contacts-identity | `openspec/specs/contacts-identity/spec.md` | Contact model, identity resolution |
| switchboard-identity | `openspec/specs/switchboard-identity/spec.md` | Switchboard routing identity |
| entity-identity | `openspec/specs/entity-identity/spec.md` | Entity graph identity model |

### Butler Specs

| Spec | Path | Covers |
|------|------|--------|
| butler-base-spec | `openspec/specs/butler-base-spec/spec.md` | Base butler contract (all butlers) |
| butler-general | `openspec/specs/butler-general/spec.md` | General butler (catch-all) |
| butler-switchboard | `openspec/specs/butler-switchboard/spec.md` | Switchboard routing butler |
| butler-health | `openspec/specs/butler-health/spec.md` | Health domain butler |
| butler-relationship | `openspec/specs/butler-relationship/spec.md` | Relationship domain butler |
| butler-finance | `openspec/specs/butler-finance/spec.md` | Finance domain butler |
| butler-travel | `openspec/specs/butler-travel/spec.md` | Travel domain butler |
| butler-home | `openspec/specs/butler-home/spec.md` | Home domain butler |
| butler-messenger | `openspec/specs/butler-messenger/spec.md` | Messenger (delivery plane) butler |

### Memory

| Spec | Path | Covers |
|------|------|--------|
| memory-catalog-schema | `openspec/specs/memory-catalog-schema/spec.md` | Memory catalog database schema |
| memory-discovery-catalog | `openspec/specs/memory-discovery-catalog/spec.md` | Discovery catalog for memory |
| memory-events-enrichment | `openspec/specs/memory-events-enrichment/spec.md` | Event enrichment for memory |
| memory-migration-integration-tests | `openspec/specs/memory-migration-integration-tests/spec.md` | Memory migration tests |
| memory-retention-policy | `openspec/specs/memory-retention-policy/spec.md` | Memory retention and eviction |

### Infrastructure and Cross-Cutting

| Spec | Path | Covers |
|------|------|--------|
| google-multi-account-oauth | `openspec/specs/google-multi-account-oauth/spec.md` | Multi-account Google OAuth |
| google-account-registry | `openspec/specs/google-account-registry/spec.md` | Google account registry |
| session-process-logs | `openspec/specs/session-process-logs/spec.md` | Session process logging |
| model-catalog | `openspec/specs/model-catalog/spec.md` | LLM model catalog and routing |
| model-benchmark-harness | `openspec/specs/model-benchmark-harness/spec.md` | Model benchmark framework |
| catalog-token-limits | `openspec/specs/catalog-token-limits/spec.md` | Token limit catalog |
| ingestion-policy | `openspec/specs/ingestion-policy/spec.md` | Ingestion policy engine |
| ingestion-event-registry | `openspec/specs/ingestion-event-registry/spec.md` | Event type registry |
| ingress-injection | `openspec/specs/ingress-injection/spec.md` | Ingress injection testing |
| error-fingerprinting | `openspec/specs/error-fingerprinting/spec.md` | Error fingerprinting |
| e2e-ecosystem-staging | `openspec/specs/e2e-ecosystem-staging/spec.md` | End-to-end staging tests |
| testing | `openspec/specs/testing/spec.md` | Testing infrastructure |
| complexity-classification | `openspec/specs/complexity-classification/spec.md` | Complexity classification |
| routing-scorecard | `openspec/specs/routing-scorecard/spec.md` | Routing quality scorecard |

### Self-Healing

| Spec | Path | Covers |
|------|------|--------|
| self-healing-module | `openspec/specs/self-healing-module/spec.md` | Self-healing module |
| self-healing-dispatch | `openspec/specs/self-healing-dispatch/spec.md` | Self-healing dispatch |
| self-healing-skill | `openspec/specs/self-healing-skill/spec.md` | Self-healing skill |
| healing-anonymizer | `openspec/specs/healing-anonymizer/spec.md` | Log anonymization for healing |
| healing-model-tier | `openspec/specs/healing-model-tier/spec.md` | Model tier selection for healing |
| healing-session-tracking | `openspec/specs/healing-session-tracking/spec.md` | Session tracking for healing |
| healing-worktree | `openspec/specs/healing-worktree/spec.md` | Worktree management for healing |

## Grounding Workflow

Before implementing any feature:

1. **Find the spec**: Look up the relevant domain in the tables above. Read `openspec/specs/<domain>/spec.md`.
2. **Check for active changes**: Run `ls openspec/changes/` to see if there is an in-progress change that modifies this spec.
3. **Compare spec to code**: Does the implementation match what the spec says? Note divergences.
4. **Implement from spec**: The spec is the source of truth. If the code disagrees with the spec, either fix the code or propose a spec change through the OpenSpec workflow.
5. **Update spec if needed**: If implementation reveals that the spec is wrong or incomplete, update the spec as part of the same change (use `/opsx:new` or `/opsx:continue`).

## When to Load

- Before implementing any feature (find the relevant spec first)
- When detecting code that does not match what you expect (check for spec drift)
- When planning new work (does a spec exist? does it need one?)
- When reviewing PRs (does the change align with the spec?)
- When evolving existing features (update the spec alongside the code)
