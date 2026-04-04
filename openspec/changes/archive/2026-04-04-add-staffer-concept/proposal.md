## Why

The Butlers ecosystem currently treats all agents uniformly as "butlers," but two distinct archetypes have emerged: **domain butlers** that serve the user directly (health, finance, travel, etc.) and **infrastructure agents** that serve the ecosystem itself (switchboard routes messages, messenger delivers outbound). This conflation leads to ad-hoc special-casing in routing, permissions, and connectivity. Formalizing a **staffer** archetype — sharing the same core engine (long-running MCP daemon, modules, ephemeral LLM sessions) but with a distinct permissions model and connectivity topology — eliminates implicit exceptions and creates a clean extension point for future infrastructure agents (e.g., a QA staffer that inspects logs, triages issues, and raises fix PRs against the codebase).

## What Changes

- **New architectural primitive: Staffer.** A staffer is a long-running MCP server daemon identical in engine to a butler but distinguished by: (a) cross-butler permissions (may connect to and act on behalf of multiple butlers), (b) an infrastructure contract (replacing the user-facing manifesto), (c) exclusion from direct switchboard user-message routing.
- **`butler.toml` schema extension.** Add a `type` field (`"butler"` | `"staffer"`, default `"butler"`) to the `[butler]` table. This is the single source of truth for the distinction.
- **Switchboard routing exclusion.** Staffers are never direct targets for user-message classification. Butlers can still route *to* staffers via switchboard (e.g., the existing `notify → messenger` mechanism).
- **Daily briefing exclusion.** Staffers do not participate in `daily_briefing_contribution` jobs.
- **Infrastructure contract.** Staffers use `MANIFESTO.md` with infrastructure-contract framing (SLA, responsibilities, failure modes) rather than user-relationship framing.
- **Reclassification.** `switchboard` and `messenger` move from butler to staffer type.
- **Heart-and-soul updates.** `vision.md`, `architecture.md`, and `v1.md` updated to define the butler/staffer split.
- **Law-and-lore updates.** RFC 0003 (switchboard routing) updated to formalize staffer routing exclusion and butler-to-staffer routing via switchboard. RFC 0006 (database isolation) updated to describe staffer schema permissions.

## Capabilities

### New Capabilities
- `staffer-archetype`: Defines what a staffer is as an architectural primitive — type field in butler.toml, identity contract, infrastructure-contract manifesto, cross-butler permissions model, and lifecycle differences from butlers.

### Modified Capabilities
- `butler-base-spec`: Add `type` field to butler identity contract; define staffer as a valid agent type sharing the same engine with different wiring.
- `butler-switchboard`: Formalize that switchboard is a staffer; update routing rules to exclude staffers from direct user-message classification; document butler-to-staffer routing via switchboard.
- `butler-messenger`: Reclassify messenger as a staffer; reframe manifesto as infrastructure contract.
- `core-daemon`: Daemon startup must read `type` from config and apply type-specific behaviors (e.g., skip switchboard client connection for switchboard staffer, skip briefing job registration for all staffers).
- `cross-butler-briefing-contribution`: Exclude staffer-typed agents from briefing contribution registration.

## Impact

- **Config:** `butler.toml` gains `type` field; existing butlers default to `"butler"`, switchboard and messenger set to `"staffer"`.
- **Code:** `src/butlers/config.py` (new `ButlerType` enum, config parsing), `src/butlers/daemon.py` (type-aware startup logic, briefing exclusion).
- **Roster:** `roster/switchboard/butler.toml` and `roster/messenger/butler.toml` updated; both `MANIFESTO.md` files reframed as infrastructure contracts.
- **Docs:** `about/heart-and-soul/vision.md`, `architecture.md`, `v1.md` updated. `about/law-and-lore/rfcs/0003-*.md` and `0006-*.md` updated.
- **Dashboard:** Staffers may need distinct visual treatment (separate section or badge); dashboard API may filter staffers from "butler list" views.
- **Future:** The design must accommodate future staffers (e.g., QA staffer with codebase R/W access, log inspection, and PR creation capabilities) without requiring architectural changes.
