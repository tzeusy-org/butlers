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

A diagram exists only because a living doc embeds it. Each one is a pair:

- **Render:** `docs/<topic>/<name>.svg`, embedded by a page in the same topic dir
  (e.g. `docs/runtime/spawner-flow.svg` in `docs/runtime/spawner.md`).
- **Source:** `docs/diagrams/<category>/<source>.excalidraw`. Some sources keep
  older numbered names (e.g. `runtime/06a-spawner-runtime.excalidraw` renders
  `runtime/spawner-flow.svg`); name new sources after their render.

Do not export `_dark.svg` variants or renders no page embeds; unreferenced renders
are deleted in docs cleanups. Removing a diagram means deleting its render, its
source, and the embedding line together. The concern groupings in
[`references/diagram-categories.md`](references/diagram-categories.md) (system
topology, butler anatomy, per-butler flows, connectors, core components,
dashboard) describe what each kind of diagram must show.

## Directory Layout

Sources live under `docs/diagrams/<category>/` — `architecture/`, `butlers/`,
`concepts/`, `connectors/`, `data/`, `frontend/`, `identity/`, `modules/`,
`runtime/`, `testing/` — never directly under `docs/diagrams/`. Run
`find docs -name '*.svg' -not -path 'docs/archive/*'` plus
`rg '\.svg' docs --glob '*.md'` to map renders to the pages that embed them.

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
5. **Existing diagrams** — `find docs/diagrams -name '*.excalidraw'`; record
   what already exists, its naming, and which category subdirectory it lives
   in (see Directory Layout below).
6. **Specs** — `ls openspec/specs/` for reference material to cite in bead
   descriptions.

### Phase 2: Diff and Decide

| Situation | Action |
|-----------|--------|
| Embedded diagram contradicts current code or roster | Update child bead for that diagram |
| New butler, module, connector, or router that an embedded diagram enumerates | Update that diagram |
| A page needs a picture it lacks | Create child bead (new source + render + embedding line) |
| Component removed | Update or remove the diagram and its embedding line |
| Diagram exists, nothing changed | Skip — no child bead |

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

  [If updating] Existing file: docs/diagrams/<category>/<name>.excalidraw —
  read it first and preserve layout/style where possible. Update only the
  parts that changed.

Acceptance criteria:
  1. Diagram renders in Excalidraw without errors
  2. <Content-specific checks — one per major element>
  3. Source saved as docs/diagrams/<category>/<name>.excalidraw, render
     exported to docs/<topic>/<name>.svg and embedded by the owning page

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

> **Merge policy:** diagram changes follow the normal worktree + PR flow in
> `CLAUDE.md`, like every tracked change.

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
- **Specify the output path** — every bead names its output file as
  `docs/diagrams/<category>/<name>.excalidraw` (see Directory Layout).
- **Request consistent color coding** — butlers=blue, connectors=green,
  DB=orange, LLM runtimes=purple, dashboard=teal, external channels=gray.
- **Request a legend** for topology diagrams.
- **Use numbered sequences** for flow diagrams, with swim lanes where there
  are 3+ actors.
