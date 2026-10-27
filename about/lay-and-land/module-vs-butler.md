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
| **Runs as** | Its own daemon with its own FastMCP server (`butlers up` may host several daemons in one OS process) | Code inside its host daemon |
| **Has its own DB schema?** | Yes — one PostgreSQL schema per butler | No — its tables live in the host butler's schema (Chronicler's memory uses the private `chronicler_mem` schema) |
| **Has its own port?** | Yes — one FastMCP port per butler | No — shares its host butler's port |
| **Lifecycle** | Starts/stops independently; registered in butler registry | Starts/stops inside its host butler's `on_startup`/`on_shutdown` |
| **Defined by** | `butler.toml`, `MANIFESTO.md`, `CLAUDE.md` | `Module` ABC subclass in Python |
| **Enabled/disabled** | Per-deployment (run or don't run the daemon) | Per-butler in `butler.toml` `[modules.*]` sections |

---

## How to Tell Them Apart

**A butler** has a directory in `roster/` that contains a `butler.toml`. Butlers
appear in the Switchboard butler registry and have ports. List them with
`ls roster/`; staffers carry `type = "staffer"` in their `butler.toml`.

**A module** has a Python class that extends `Module` from
`src/butlers/modules/base.py`. Modules appear in `butler.toml` under
`[modules.*]` sections and in the `module.states()` tool output. They do not
have ports or roster entries. List shared modules with
`ls src/butlers/modules/`; butler-local modules live in
`roster/{butler}/modules/`. See which butlers load a module with
`grep -l '^\[modules.memory\]' roster/*/butler.toml`.

---

## The Classic Mistake

A redesign brief or implementation plan sometimes names `memory`, `contact`, or
`household` as if they were butlers. They are not:

| Phantom butler name | Reality |
|---|---|
| `memory` | The **memory module**, loaded by most domain butlers and Switchboard via `[modules.memory]`. `roster/memory/` does not exist. |
| `contact` | The **contacts module**, loaded by several domain butlers via `[modules.contacts]`. `roster/contact/` does not exist. |
| `household` | Functionality served by the **home butler** (`roster/home/`). No `household` module or butler exists. |

When a brief lists butlers "touched" by a change, verify each name against the
`roster/` directory. If the name does not correspond to a `roster/` entry, it is
a module (or does not exist at all), and the analysis must be rewritten against
the butler that hosts it.

---

Modules only add tools to their host butler's FastMCP server. They never start
their own server or claim a port. Enablement, config validation and lifecycle:
[Modules and Connectors](../../docs/concepts/modules-and-connectors.md).

---

## Why This Matters for Analysis

- **Blast radius**: a module outage affects only its host butler, not a fleet of
  independent daemons.
- **Schema access**: modules use their host butler's schema. A "cross-butler
  read" must go through MCP, not direct SQL — even between two modules loaded by
  different butlers.
- **Work inventory**: a task that says "update the memory butler" is actually a
  task on the `memory` module. The PR touches `src/butlers/modules/memory/` and
  possibly the hosts' `butler.toml`, not a `roster/memory/` directory.
- **Routing**: the Switchboard routes to butlers, not modules. A message is
  routed to the domain butler that owns it, which then uses its loaded modules
  to respond.

---

## Reference

- Module ABC: `src/butlers/modules/base.py`
- Module registry: `src/butlers/modules/registry.py`
- RFC 0002: MCP Tool Surface and Modules (`about/legends-and-lore/rfcs/0002-mcp-tool-surface-and-modules.md`)
- Component inventory: `about/lay-and-land/components.md` §2 (Modules)
