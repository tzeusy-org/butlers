---
name: lay-and-land
description: >
  Load the project's topology maps to understand where components live, how they connect,
  and what boundaries exist. The about/lay-and-land/ directory contains component inventories,
  data flow diagrams, dependency maps, and deployment topology. Consult before: adding new
  components, modifying integration points, changing deployment, or when unsure where something
  lives in the system. Use proactively when onboarding or when work crosses component boundaries.
---

# Lay and Land -- Project Topology

The `about/lay-and-land/` directory is the WHERE pillar of the Butlers knowledge architecture. It contains maps of the system: what components exist, how they connect, where data flows, and what the deployment looks like.

## Four-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| Doctrine | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| Design Contracts | `about/law-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| **Topology** | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |

## Map Index

| # | File | Status | What it maps |
|---|------|--------|--------------|
| 1 | `about/lay-and-land/components.md` | EXISTS | Component inventory: every butler, connector, and infrastructure process with boundaries and ownership |
| 2 | `about/lay-and-land/data-flow.md` | EXISTS | Data paths: ingestion flow, scheduling flow, response flow, identity resolution flow, memory flow |
| 3 | `about/lay-and-land/deployment.md` | EXISTS | Deployment topology: Docker, PostgreSQL, ports, environment variables, process supervision |
| 4 | `about/lay-and-land/dependencies.md` | EXISTS | Dependency maps: internal module dependencies (topological sort order) and external service dependencies |
| 5 | `about/lay-and-land/integration.md` | EXISTS | Protocol contracts between subsystems: ingest.v1 envelope, route.v1 dispatch, MCP tool surface, dashboard API |

## Key Boundaries (from existing architecture)

Until the topology documents are written, these are the major architectural boundaries to be aware of:

### Process Boundaries

- **Butlers** (9 daemons): switchboard, general, health, education, finance, relationship, travel, home, messenger -- each a persistent async daemon with its own MCP server
- **Connectors** (standalone processes): gmail, telegram-bot, telegram-user-client, discord, heartbeat, live-listener -- bridge external transports to the ingestion pipeline
- **Dashboard** (FastAPI + Vite): single web process serving API and frontend
- **PostgreSQL** (single instance): shared database with per-butler schemas + `public` schema

### Schema Boundaries

- Each butler owns its own PostgreSQL schema (e.g., `health`, `relationship`, `finance`)
- The `public` schema holds cross-butler identity tables (`contacts`, `contact_info`)
- Schema search_path: `<butler_schema>, public`
- Butlers MUST NOT access each other's schemas

### Communication Boundaries

- Butler-to-butler: MCP only, via Switchboard
- Connector-to-system: ingest.v1 envelope submitted to Switchboard
- LLM-to-butler: MCP tool calls during ephemeral sessions
- Dashboard-to-butler: FastAPI routes backed by direct DB queries (read) and MCP calls (write)

### Port Assignments

- switchboard: 41100
- general: 41101
- relationship: 41102
- health: 41103
- messenger: 41104
- domain butlers: 41105+ (41199 reserved for infrastructure)

## When to Load

- Adding a new butler, connector, or infrastructure component
- Modifying integration points between subsystems
- Changing deployment configuration or process topology
- Unsure where something lives in the system
- Onboarding to the project
- Work that crosses component or schema boundaries

## How to Use

1. Read the specific map relevant to your task -- do not load all five unless necessary.
2. For component questions: `components.md`. For data paths: `data-flow.md`. For infra: `deployment.md`.
3. Cross-reference with the relevant RFC in `about/law-and-lore/` or the component's spec in `openspec/`.
