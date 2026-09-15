# Integration, Switchboard Registration & Pitfalls

Covers the framework wiring that makes a new butler live: runtime skills,
Switchboard routing, auto-discovery, and the common mistakes checklist.

## Runtime skills (`.agents/skills/`)

Skills provide detailed workflow guidance to runtime instances. Each skill is a
subdirectory with a `SKILL.md`. Skills live in `.agents/skills/` (discovered by
Codex) with a `.claude -> .agents` symlink for Claude Code compatibility.

### Shared skills (most butlers)

```bash
mkdir -p roster/<butler-name>/.agents/skills/
cd roster/<butler-name>/.agents/skills/
ln -s ../../../shared/skills/butler-memory butler-memory
ln -s ../../../shared/skills/butler-notifications butler-notifications
cd roster/<butler-name>/
ln -s .agents .claude
```

### Custom skills

Butler-specific skills for domain workflows (e.g., health check-in flow,
relationship outreach cycle). Each should include:
- Pre-requisites (what data to gather first)
- Step-by-step flow
- Example conversational interactions
- Error handling patterns

## Register with Switchboard

Switchboard routing rules are **hardcoded in `roster/switchboard/CLAUDE.md`** —
the LLM's system prompt classifies messages by reading them. Update it manually:

1. **Available Butlers section** (~line 10): add a bullet:
   ```
   - **<butler-name>**: <one-line description of what it handles>
   ```
2. **Classification Rules section** (~line 15): add a rule:
   ```
   - If the message is about <domain keywords> → <butler-name>
   ```
3. **Update the message-triage skill** if it exists under `roster/switchboard/skills/`.

The framework auto-discovers butlers via `discover_butlers()` for registry and
connectivity, but routing classification is LLM-driven from Switchboard's
CLAUDE.md — without the manual update, messages in your domain fall through to `general`.

## Auto-Discovery

No registration code needed — placing the right files in the right structure is enough:

- **Modules**: `_register_roster_modules()` in `src/butlers/modules/registry.py` scans `roster/*/modules/__init__.py` for `Module` subclasses — how domain tools get wired to MCP.
- **Tools**: `register_all_butler_tools()` in `src/butlers/tools/_loader.py` scans `roster/*/tools.py` or `roster/*/tools/__init__.py`.
- **Migrations**: `_discover_butler_chains()` in `src/butlers/migrations.py` scans `roster/*/migrations/`.
- **API Routers**: `discover_butler_routers()` in `src/butlers/api/router_discovery.py` scans `roster/*/api/router.py`.
- **Registry**: `discover_butlers()` scans butler.toml files to populate the butler registry.

No hardcoded butler names anywhere.

## Common Mistakes

1. **Wrong database config**: Using `name = "butler_<name>"` (old pattern). Correct: `name = "butlers"` + `schema = "<name>"`.
2. **Overlapping domain**: Tools duplicating what another butler already does. Check existing butlers first.
3. **Missing branch_labels**: Forgetting `branch_labels = ("<name>",)` in the first migration causes Alembic chain resolution failures.
4. **Port conflicts**: Using a port already assigned to another butler.
5. **Non-Python-identifier name**: Names with hyphens or leading digits break module imports.
6. **Missing `__init__.py`**: The migrations directory needs an empty `__init__.py`. The tools/ package needs one with re-exports.
7. **Framework imports in tools**: Tools must be pure async functions taking `pool: asyncpg.Pool`. No FastMCP decorators.
8. **Forgetting Switchboard CLAUDE.md update**: New butlers won't receive routed messages unless Switchboard's system prompt knows about them.
9. **TIMESTAMP instead of TIMESTAMPTZ**: Always use timezone-aware timestamps.
10. **Missing Interactive Response Mode**: User-facing butlers receiving Telegram/email need the full IRM section in CLAUDE.md.
11. **Missing memory taxonomy**: Butlers with `[modules.memory]` need domain-specific Memory Classification in CLAUDE.md.
12. **Forgetting shared skill symlinks**: Most butlers should symlink `butler-memory` and `butler-notifications` from `roster/shared/skills/`.
13. **Missing AGENTS.md**: Every butler needs this file, even if initially just a header.
14. **API router missing `router` export**: Dashboard routes must export a module-level `router` variable (APIRouter instance).
15. **Missing domain module package**: If the butler defines tools in `roster/{name}/tools/`, a `modules/` package is required to register them as MCP tools. Without it the runtime sees no domain tools — only core and shared module tools. See [module-package.md](module-package.md).
