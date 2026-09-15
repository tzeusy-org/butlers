---
name: butlers-development
description: >
  Build and evolve butlers, their capabilities, specs, docs, and dashboard UX. Routes to one
  workflow: scaffold a new butler; add an external-service connector or module; design or migrate a
  butler's PostgreSQL schema; audit and improve a butler's MCP tool surface; reconcile OpenSpec
  against the codebase; regenerate architecture diagrams; run the generative JARVIS/UX ecosystem
  audit; or orchestrate a dashboard-page redesign. Triggers: "add a new butler", "add a connector",
  "new module", "integrate with <service>", "create a table / write a migration", "review butler
  tools", "audit the tool surface", "reconcile specs vs code", "refresh architecture diagrams",
  "run the JARVIS pursuit", "is this page real or a skin", "redesign the X page". Not for running,
  debugging, QA, testing, or observability of the dev stack — use /butlers-tooling.
metadata:
  owner: tze
  authors:
    - tze
    - Claude
  status: active
  last_reviewed: "2026-09-05"
---

# Butlers Development Router

Building-and-evolving workflows for the Butlers project. Each workflow is a full skill package under
`subskills/`. Load **at most one** per task (redesign work legitimately chains two — see the table).
These are project-grounded execution layers over the five doctrine pillars; when scope or convention
is unclear, load `/doctrine` first, not code inference.

## Discover subskills

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
find "$PKG/subskills" -maxdepth 2 -name SKILL.md
rg -n "^name:|^description:" "$PKG"/subskills/*/SKILL.md
```

## Routing table

| The task is... | Subskill | Typical trigger |
|---|---|---|
| Create a brand-new butler in the roster (scaffolding, `butler.toml`, MANIFESTO, CLAUDE.md, tools, migrations, API, tests) | [subskills/adding-butlers-to-roster/SKILL.md](subskills/adding-butlers-to-roster/SKILL.md) | "add a new butler", "scaffold a butler" |
| Integrate a new external service — account registry, module (MCP tools), connector (ingestion), dashboard API | [subskills/adding-connectors-and-modules/SKILL.md](subskills/adding-connectors-and-modules/SKILL.md) | "add a connector", "new module", "integrate with Spotify" |
| Design or evolve a butler's PostgreSQL schema: tables, migrations, indexes, data model | [subskills/butler-db-schema/SKILL.md](subskills/butler-db-schema/SKILL.md) | "create a table", "write a migration", "add an index" |
| Audit and improve a butler's MCP tool surface: tool counts, docstrings, error messages, unused tools, tool groups | [subskills/butler-tool-review/SKILL.md](subskills/butler-tool-review/SKILL.md) | "review butler tools", "audit the tool surface", "onboard a module's tools" |
| Reconcile OpenSpec specs against the actual implementation, both directions (undocumented code / unimplemented spec) | [subskills/reconcile-spec-to-project/SKILL.md](subskills/reconcile-spec-to-project/SKILL.md) | "reconcile specs vs code", "audit project completeness" |
| Regenerate the Excalidraw architecture diagrams by surveying the code and emitting a diagram beads epic | [subskills/update-architectural-diagrams/SKILL.md](subskills/update-architectural-diagrams/SKILL.md) | "refresh architecture diagrams", "diagrams are stale" |
| Recurring generative UX + ecosystem audit toward a JARVIS-grade system; carries the UI-maturity QC sweep (real vs skin) as its own subskill | [subskills/butler-relentless-jarvis-pursuit/SKILL.md](subskills/butler-relentless-jarvis-pursuit/SKILL.md) | "run the JARVIS pursuit", "is this flow wired", "is the X page real or a skin" |
| Orchestrate a dashboard-page redesign against the Dispatch design language, with or without a Claude Design bundle | [subskills/butlers-redesign-prompt/SKILL.md](subskills/butlers-redesign-prompt/SKILL.md) | "redesign the X page", "integrate the redesign bundle" |

## Routing rules

- One subskill per task. The one sanctioned chain: a redesign runs the UI-maturity QC sweep
  (`butler-relentless-jarvis-pursuit` → `subskills/ui-maturity-audit/`) **first**, then
  `butlers-redesign-prompt` — it often reverses the framing (a "skin" turns out fully wired).
- **Operate vs build**: running, debugging, QA, testing, observability of the dev stack →
  `/butlers-tooling`. This router is for building and evolving capabilities.
- **Project knowledge vs execution**: doctrine, specs, topology, non-negotiables → `/doctrine`.
  Deciding *what* to build / prioritization / repo-health audits → `/th-projects`. Change-level
  engineering judgment (readability, tests, diagnosis, cruft) → `/th-engineering`. Product feel
  (UX contracts, latency, copy, a11y) → `/th-design`. Task tracking → `bd` (see `AGENTS.md`).
- Every butler's identity lives in `roster/{butler}/MANIFESTO.md` — read it directly for one
  butler's purpose or framing; it is not a subskill.
- No subskill fits → answer from this router or say so. Do not load a subskill to browse.
