---
name: update-architectural-diagrams
description: >-
  Regenerate the project's Excalidraw architecture documentation by surveying
  the current codebase, diffing against existing diagrams in docs/diagrams/,
  and emitting a beads epic whose children each instruct a worker to produce
  or update one diagram via /excalidraw-diagram. Use when architecture has
  changed, new butlers or modules have been added, or the user asks to refresh,
  regenerate, or update architectural diagrams.
metadata:
  owner: tze
  authors: [tze, Claude]
  status: active
  last_reviewed: "2026-09-05"
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

Numbering convention, grouped by concern:

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

Gather in parallel:

1. **Roster** — `ls roster/`, then each `butler.toml` + first ~30 lines of
   `MANIFESTO.md`. Capture: name, port, modules, schedule tasks, one-line purpose.
2. **Core** — `ls src/butlers/core/` and `ls src/butlers/modules/`. Note
   files added/removed vs. what existing diagrams cover.
3. **Dashboard routers** — `ls src/butlers/api/routers/` and
   `ls roster/*/api/router.py`. Count core and butler-specific routers.
4. **Connectors** — `ls src/butlers/connectors/` (or scan for connector
   dirs). Note new/removed connectors.
5. **Existing diagrams** — `ls docs/diagrams/*.excalidraw`; record what
   already exists and its naming.
6. **Specs** — `ls openspec/specs/` for reference material to cite in bead
   descriptions.

### Phase 2: Diff and Decide

| Situation | Action |
|-----------|--------|
| New butler added, no `04x-` diagram | Create a `04x-` child bead |
| Existing butler's modules/schedule changed | Update child bead for its `04x-` diagram |
| New core component (new file under `src/butlers/core/`) | Create/update a `06x-` child bead |
| New dashboard router | Update `07a-` and possibly `07b-` |
| New connector | Update `05-` |
| Butler removed from roster | Child bead to remove its `04x-` diagram |
| Diagram exists, nothing changed | Skip — no child bead |
| System topology changed (ports, new butler category) | Update `01-` |

**Always regenerate `01-`** (system topology) — it must reflect the current
roster. **Always regenerate `02-`** (butler spec) if core infra or the module
interface changed.

For updates (vs. from-scratch): note the existing file path in the bead and
instruct the worker to read it first and evolve rather than restart.

### Phase 3: Craft Child Beads

One child bead per diagram needing creation/update, under the epic. Follow
`/beads-writer` quality standards.

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

Required elements differ by category (what boxes/flows each prefix must
show) — see [`references/diagram-categories.md`](references/diagram-categories.md)
when drafting the "what to show" section for a specific diagram.

### Phase 4: Create Epic and Children

Follow `/beads-writer` conventions:

1. Create the epic first: `Title: "Regenerate Excalidraw architecture
   documentation"`, `Type: epic, Priority: 2`, description = scope summary
   listing which diagrams will be created/updated/removed.
2. Create children sequentially (to capture IDs for dependencies).
3. Create a final **reconciliation bead** — `Title: "Reconcile spec-to-code
   coverage for architecture diagrams"` — depending on all children; follow
   the reconciliation bead template from `/beads-writer`.
4. Wire dependencies: `bd dep add <recon-id> <child-id>` for every child.

### Phase 5: Verify and Present

1. `bd dep tree <epic-id>` — confirm structure
2. `bd ready | grep <epic-prefix>` — confirm children are unblocked
3. Bead mutations already auto-commit to the shared Dolt server — no sync
   step. (Optionally `bd export -o .beads/issues.export.jsonl` to refresh
   the git-tracked mirror.)

> **Merge policy:** if a worker's changes are exclusively docs/diagram files
> (`.excalidraw`, `docs/`), a direct commit + push to `main` is fine — no PR
> needed. Only open a PR when implementation code also changed.

Present the created beads as a table:

| ID | Title | Action | File |
|----|-------|--------|------|
| ... | ... | create/update/remove | docs/diagrams/... |

## Style Guide for Diagram Descriptions

- **Be exhaustive** — list every box, arrow, and label. Workers have no
  project context beyond the bead description and referenced files.
- **Cite specifics** — port numbers, tool names, cron expressions, table
  names, file paths. Never say "various tools"; enumerate them.
- **Reference source files** — `Reference:` lines pointing to specs, source
  code, and config files the worker should read.
- **Specify the output path** — every bead names its output file in
  `docs/diagrams/`.
- **Request consistent color coding** — butlers=blue, connectors=green,
  DB=orange, LLM runtimes=purple, dashboard=teal, external channels=gray.
- **Request a legend** for topology diagrams.
- **Use numbered sequences** for flow diagrams, with swim lanes where there
  are 3+ actors.
