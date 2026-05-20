# Module vs Butler — A Common Confusion

**Butlers and modules are not the same thing.** This distinction matters when
reading roster inventory, planning implementation work, or writing redesign
briefs. Treating a module as if it were a butler produces incorrect dependency
analysis, wrong blast-radius reasoning, and phantom work items.

---

## The Distinction

| | Butler | Module |
|---|---|---|
| **What it is** | A long-running daemon (FastMCP server process) | A pluggable capability unit loaded by a butler |
| **Lives in** | `roster/{butler-name}/` with a `butler.toml` | `src/butlers/modules/` or `roster/{butler}/modules/` |
| **Has its own process?** | Yes — one OS process per butler | No — runs inside its host butler's process |
| **Has its own DB schema?** | Yes — one PostgreSQL schema per butler | No — uses its host butler's schema |
| **Has its own port?** | Yes — one FastMCP port per butler | No — shares its host butler's port |
| **Lifecycle** | Starts/stops independently; registered in butler registry | Starts/stops inside its host butler's `on_startup`/`on_shutdown` |
| **Defined by** | `butler.toml`, `MANIFESTO.md`, `CLAUDE.md` | `Module` ABC subclass in Python |
| **Enabled/disabled** | Per-deployment (run or don't run the daemon) | Per-butler in `butler.toml` `[modules.*]` sections |

---

## How to Tell Them Apart

**A butler** has a directory in `roster/` that contains a `butler.toml`. Butlers
appear in the process table and in the butler registry (Switchboard heartbeat
list). They have ports. The current roster:

```
roster/switchboard/   roster/general/      roster/relationship/
roster/health/        roster/finance/       roster/travel/
roster/education/     roster/home/          roster/lifestyle/
roster/messenger/     roster/chronicler/    roster/qa/
```

**A module** has a Python class that extends `Module` from
`src/butlers/modules/base.py`. Modules appear in `butler.toml` under
`[modules.*]` sections and in the `module.states()` tool output. They do not
have ports, schemas, or roster entries.

Current shared modules (in `src/butlers/modules/`):

```
email       telegram    calendar    memory
contacts    pipeline    approvals   mailbox
metrics     self_healing
```

---

## The Classic Mistake

A redesign brief or implementation plan sometimes names `memory`, `contact`, or
`household` as if they were butlers. They are not:

| Phantom butler name | Reality |
|---|---|
| `memory` | The **memory module** — loaded by `relationship` via `[modules.memory]` in `butler.toml`. `roster/memory/` does not exist. |
| `contact` | The **contacts module** — loaded by `relationship` via `[modules.contacts]` in `butler.toml`. `roster/contact/` does not exist. |
| `household` | Functionality served by the **home butler** (`roster/home/`). No `household` module or butler exists. |

When a brief lists butlers "touched" by a change, verify each name against the
`roster/` directory. If the name does not correspond to a `roster/` entry, it is
a module (or does not exist at all), and the analysis must be rewritten against
the butler that hosts it.

---

## Module Enablement Pattern

A butler opts into a module by adding a section to its `butler.toml`:

```toml
[modules.memory]
groups = ["core", "entity"]

[modules.contacts]
provider = "google"
include_other_contacts = false
```

The `ModuleRegistry` discovers concrete `Module` subclasses, validates configs,
resolves dependencies via topological sort, runs migrations, and calls
`on_startup()` in order. The daemon owns the module lifecycle.

Modules only add tools to the butler's FastMCP server. They never start their
own server, claim a port, or create a separate PostgreSQL schema.

---

## Why This Matters for Analysis

- **Blast radius**: a module outage affects only its host butler, not a fleet of
  independent daemons.
- **Schema access**: modules use their host butler's schema. A "cross-butler
  read" must go through MCP, not direct SQL — even between two modules loaded by
  different butlers.
- **Work inventory**: a task that says "update the memory butler" is actually a
  task on the `memory` module inside the `relationship` butler. The PR touches
  `src/butlers/modules/memory/` and possibly `roster/relationship/butler.toml`,
  not a `roster/memory/` directory.
- **Routing**: the Switchboard routes to butlers, not modules. A message
  classified as memory-related is routed to the `relationship` butler, which
  then uses its loaded `memory` module to respond.

---

## Reference

- Module ABC: `src/butlers/modules/base.py`
- Module registry: `src/butlers/modules/registry.py`
- RFC 0002: MCP Tool Surface and Modules (`about/legends-and-lore/rfcs/0002-mcp-tool-surface-and-modules.md`)
- Component inventory: `about/lay-and-land/components.md` §2 (Modules)
