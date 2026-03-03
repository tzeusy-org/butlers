---
name: update-architectural-diagrams
description: >-
  Regenerate the project's Excalidraw architecture documentation by surveying
  the current codebase, diffing against existing diagrams in docs/diagrams/,
  and emitting a beads epic whose children each instruct a worker to produce
  or update one diagram via /excalidraw-diagram. Use when architecture has
  changed, new butlers or modules have been added, or the user asks to refresh,
  regenerate, or update architectural diagrams.
---

# Update Architectural Diagrams

Produce a beads epic + children that, when executed by workers, regenerate all
Excalidraw architecture diagrams in `docs/diagrams/`. Each child bead
instructs its worker to use `/excalidraw-diagram` to create or update one
`.excalidraw` file.

## When to Use

- Architecture has evolved and diagrams are stale
- A new butler, module, connector, or dashboard router has been added
- The user says "update diagrams", "refresh architecture docs", "regenerate diagrams"
- After a milestone or large feature lands

## Diagram Catalog

The canonical set of diagrams lives in `docs/diagrams/`. The numbering
convention groups diagrams by concern:

| Prefix | Concern | Typical contents |
|--------|---------|-----------------|
| `01-`  | System topology | All butlers, connectors, DB, LLM runtimes, dashboard |
| `02-`  | Butler specification | Core + modules anatomy, MCP, spawner, config |
| `03x-` | Fixed butler designs | Switchboard (a), General (b), and any future fixed butlers |
| `04x-` | Rostered butler user flows | One diagram per rostered specialist butler |
| `05-`  | Connector design | ingest.v1 envelope, dedup, heartbeat, implemented connectors |
| `06x-` | Core component deep-dives | Spawner (a), Scheduler (b), State Store (c), Startup (d), DB Schema (e) |
| `07x-` | Dashboard | API gateway (a), core data flows (b) |

## Workflow

### Phase 1: Survey Current State

Gather these inputs in parallel:

1. **Roster inventory** — `ls roster/` then read each `butler.toml` and
   first ~30 lines of `MANIFESTO.md`. Capture: name, port, modules,
   schedule tasks, and one-line purpose.

2. **Core components** — `ls src/butlers/core/` and `ls src/butlers/modules/`.
   Note any new files or removed files vs. what the existing diagrams cover.

3. **Dashboard routers** — `ls src/butlers/api/routers/` and
   `ls roster/*/api/router.py`. Count core and butler-specific routers.

4. **Connectors** — `ls src/butlers/connectors/` or scan for connector
   directories. Note any new or removed connectors.

5. **Existing diagrams** — `ls docs/diagrams/*.excalidraw` and record
   which files already exist and their names.

6. **Specs** — `ls openspec/specs/` for reference material to cite in
   bead descriptions.

### Phase 2: Diff and Decide

Compare the survey results against the diagram catalog above:

| Situation | Action |
|-----------|--------|
| New butler added to roster, no `04x-` diagram | Create a new `04x-` child bead |
| Existing butler's modules/schedule changed | Create an update child bead for its `04x-` diagram |
| New core component (e.g., new file in `src/butlers/core/`) | Create or update a `06x-` child bead |
| New dashboard router | Update `07a-` and possibly `07b-` child beads |
| New connector | Update `05-` child bead |
| Butler removed from roster | Create a child bead to remove its `04x-` diagram |
| Diagram exists and nothing changed | Skip — no child bead needed |
| System topology changed (ports, new butler category) | Update `01-` child bead |

**Always regenerate `01-` (system topology)** — it is the map of the whole
system and should reflect the current roster.

**Always regenerate `02-` (butler spec)** — if core infrastructure or the
module interface changed.

For diagrams that need updating vs. creating from scratch: the child bead
description should note the existing file path and instruct the worker to
read the existing diagram first and evolve it rather than starting from
zero.

### Phase 3: Craft Child Beads

For each diagram that needs creation or update, write a child bead under
the epic. Follow the `/beads-writer` quality standards.

#### Bead Template (adapt per diagram)

```
Title:  "Diagram: <concise diagram subject>"
        or "Update diagram: <subject>" for existing diagrams
Type:   task
Priority: 2 (match epic)
Parent: <epic-id>

Description:
  Use /excalidraw-diagram to create|update <output-path>.

  <What to show — be exhaustive. List every box, arrow, label, and flow
  the worker needs to draw. Reference specific source files, specs, port
  numbers, tool names, table names, cron expressions, etc. The worker
  has no prior context about the project — the description IS the spec.>

  Reference: <spec paths, source files the worker should read>

  [If updating] Existing file: docs/diagrams/<name>.excalidraw — read it
  first and preserve layout/style where possible. Update only the parts
  that changed.

Acceptance criteria:
  1. Diagram renders in Excalidraw without errors
  2. <Content-specific checks — one per major element>
  3. File saved as docs/diagrams/<name>.excalidraw

Estimate: 60  (minutes)
```

#### Required Elements per Diagram Category

**01 — System topology:**
- All butlers with port numbers
- All connectors as external processes
- Data flow arrows: ingress, egress, persistence
- PostgreSQL with schema isolation
- LLM runtimes as ephemeral subprocesses
- Dashboard gateway
- Color legend (butlers=blue, connectors=green, DB=orange,
  LLM=purple, dashboard=teal, external channels=gray)

**02 — Butler specification:**
- Two-layer design (core ring + modules ring)
- MCP SSE transport + tool registration
- Ephemeral LLM spawning sequence
- DB schema + Alembic migrations
- Config directory tree (roster/{name}/)
- Module interface methods
- Core tools enumeration

**03x — Fixed butler designs:**
- Butler-specific MCP tools and endpoints
- Ingestion/routing/dispatch flows (Switchboard)
- Key user flow as numbered sequence diagram with swim lanes
- Scheduled job sidebar

**04x — Rostered butler user flows:**
- Enabled modules listed
- Inbound user interaction flow (Telegram → Switchboard → Butler)
- Each scheduled task as a flow
- Primary user flow as numbered sequence
- Data model callout (schema tables)

**05 — Connector design:**
- Connector as standalone process (NOT a butler)
- Transport-only lifecycle loop
- ingest.v1 envelope exploded view (source, event, sender, payload, control)
- Deduplication decision tree (3 tiers)
- Crash-safe checkpoints, rate limiting, heartbeat
- All implemented connectors as examples

**06x — Core deep-dives:**
- Component internals with data structures
- Sequence diagrams for key operations
- Error/edge cases where relevant
- Cross-references to other components

**07x — Dashboard:**
- FastAPI gateway + middleware stack
- All core and butler-specific routers listed
- Router discovery mechanism
- Key data flows as sequences (session viewer, memory browser,
  approval workflow, cost tracking, calendar workspace, SSE streaming)

### Phase 4: Create Epic and Children

Use `/beads-writer` conventions:

1. Create the epic first:
   ```
   Title: "Regenerate Excalidraw architecture documentation"
   Type: epic, Priority: 2
   Description: <scope summary listing which diagrams will be created/updated/removed>
   ```

2. Create children sequentially (to capture IDs for dependencies).

3. Create a final **reconciliation bead** that depends on all children:
   ```
   Title: "Reconcile spec-to-code coverage for architecture diagrams"
   ```
   Follow the reconciliation bead template from /beads-writer.

4. Wire dependencies: `bd dep add <recon-id> <child-id>` for every child.

### Phase 5: Verify and Present

1. `bd dep tree <epic-id>` — confirm structure
2. `bd ready | grep <epic-prefix>` — confirm children are unblocked
3. `bd export -o .beads/issues.jsonl && bd sync` — persist

> **Merge policy:** If a worker's changes are exclusively docs/diagram files
> (`.excalidraw`, `docs/`), a direct commit + push to `main` is fine — no PR
> needed. Only open a PR when implementation code is also changed.

Present the created beads as a table:

| ID | Title | Action | File |
|----|-------|--------|------|
| ... | ... | create/update/remove | docs/diagrams/... |

## Style Guide for Diagram Descriptions

When writing bead descriptions, follow these rules so workers produce
consistent diagrams:

- **Be exhaustive** — list every box, arrow, and label. Workers have no
  project context beyond the bead description and referenced files.
- **Cite specifics** — port numbers, tool names, cron expressions, table
  names, file paths. Never say "various tools"; enumerate them.
- **Reference source files** — include `Reference:` lines pointing to
  specs, source code, and config files the worker should read.
- **Specify the output path** — every bead must name its output file
  in `docs/diagrams/`.
- **Request consistent color coding** — butlers=blue, connectors=green,
  DB=orange, LLM runtimes=purple, dashboard=teal, external channels=gray.
- **Request a legend** for topology diagrams.
- **Use numbered sequences** for flow diagrams, with swim lanes where
  there are 3+ actors.
