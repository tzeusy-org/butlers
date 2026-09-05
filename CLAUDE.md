# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

**Keep this file small.** It loads into every session, so it carries only what you need *before* you
know what you are working on: the non-negotiables, the commands, and where to find everything else.
Detail belongs in the destinations below — add it there, not here.

## Where to look

| You need | Go to |
| --- | --- |
| Runtime traps, subsystem contracts, hard-won gotchas | `AGENTS.md` (§ Notes to self) |
| Beads workflow in depth: worktrees, session protocol, commands | `AGENTS.md` (§ Beads Workflow Integration), `bd prime` |
| WHY (scope, non-negotiables) · WHAT (specs) · HOW (RFCs) · WHERE (topology) · engineering bar | `doctrine` skill — routes to one pillar navigator under `about/` or `openspec/` |
| Architecture / runtime / modules / connectors reference | `docs/index.md` |
| Adding a butler, module, or connector | `butlers-development` skill → `adding-butlers-to-roster` / `adding-connectors-and-modules` subskills |
| Dashboard API conventions (mount boundary, cursor pagination, degraded envelopes) | `docs/api_and_protocols/response-conventions.md` |
| Model / runtime / session-timeout config ownership (catalog vs `runtime_config`) | `docs/runtime/model-routing.md` |
| Identity & entity resolution (`public.entities`, `relationship.entity_facts`, `notify()`) | `docs/concepts/identity-model.md` |
| Butler daemon internals, runtime config seeding | `docs/architecture/butler-daemon.md`, `docs/concepts/butler-lifecycle.md` |
| Memory subsystem design | `docs/modules/memory.md` |
| Debugging a dev-stack session/routing failure | `butlers-tooling` skill → `butler-dev-debug` subskill |

## Repo Root Discipline (NON-NEGOTIABLE)

**Never move HEAD in the main repo root (`~/gt/butlers`) off `main`** — no `git checkout -b`,
`git switch`, or `git checkout <branch>`. Agents and humans rely on the root staying on `main`. If
you find it on another branch, put it back before starting work.

Do branch work in a dedicated worktree outside the repo:

```bash
git worktree add /home/tze/.butlers-worktrees/<branch-name> -b <branch-name> origin/main
```

Commit, push, and open PRs from the worktree; every tracked change goes through this worktree + PR
flow, including small documentation fixes. After review and the PR-head gates complete, add the PR
to the sole merge route with `gh pr merge <n> --squash --auto`. The non-strict queue validates the
current `merge_group` tree, so do not rebase a clean PR merely to refresh it. Remove the worktree
before using `--delete-branch`.

## Project Overview

Butlers is a personal AI agent system. Each "butler" is a long-running MCP server daemon with core
infrastructure (state store, scheduler, LLM CLI spawner, session log) plus opt-in modules (email,
telegram, calendar, …). On trigger, the butler spawns an ephemeral LLM CLI wired exclusively to
itself via a locked-down MCP config, then logs the session. The Switchboard butler routes external
requests to the right butler; inter-butler communication is MCP-only through it.

**Tech stack:** Python 3.12+, FastMCP, Claude Agent SDK, PostgreSQL (JSONB-heavy; one DB, one schema
per butler, cross-butler tables in `public`, each role sees only its schema plus `public`), Docker,
asyncio. Butler config is git-based under `roster/{butler}/`.

## Commands

```bash
uv sync --dev                                          # Install dependencies
make lint | make format | make test | make check       # ruff / ruff / pytest / lint+test
uv run pytest tests/test_foo.py -q --tb=short          # Single file (quiet)
uv run pytest tests/test_foo.py::test_bar -n 0         # Single test (avoid inherited xdist)
make test-plan                                         # Dirty-worktree plan only; does not run pytest
uv run ruff check src/ tests/ --output-format concise  # Lint only (quiet)
uv run ruff format src/ tests/                         # Format only
```

**Test scope:** start with an exact node or file and widen gradually. `make test-plan` inspects
committed, staged, unstaged, and untracked changes but does **not** execute tests. `make test-qg`
is a local `tests/` gate, not universal coverage: it omits roster, root DB/migration suites, and
the CI-shaped selection. The exact agent ladder and final CI-shaped commands live in
`AGENTS.md` (§ Test Scope Policy). `pytest_gate.py` receipts require a positive terminator before a
run is called green; `UNKNOWN` is never a pass.

## Key Conventions

- **uv**, not pip. Hatchling build backend, `src/butlers/` layout.
- **Ruff:** target py312, line-length 100, rules E, F, I, UP.
- **pytest** with pytest-asyncio (`asyncio_mode = "auto"`); write the failing test first.
- **Modules only add tools** — they never touch core infrastructure. Implement the `Module` ABC
  (`src/butlers/modules/base.py`): `register_tools`, `migrations`, `on_startup`, `on_shutdown`.
  Dependencies resolve via topological sort.
- **Manifesto-driven design:** every butler's `roster/{butler}/MANIFESTO.md` defines its identity and
  value proposition. Align features, tools, and UX with it; consult it when scope or framing is
  unclear.
- **Butler API routes** live in `roster/{butler}/api/router.py` (module-level `router` variable, no
  `__init__.py`, Pydantic models co-located in `models.py`) and are auto-discovered by
  `src/butlers/api/router_discovery.py`, with DB deps auto-wired by `wire_db_dependencies()`.
- **Development is spec-driven:** requirements in `openspec/`, doctrine in `about/heart-and-soul/`,
  work items in beads.

## Issue Tracking (Beads)

Use `bd` for **all** task tracking — never TodoWrite, TaskCreate, or markdown TODO lists. Use
`bd remember` for persistent knowledge, not MEMORY.md files. Run `bd prime` for the full command
reference; `AGENTS.md` has the worktree/PR workflow.

```bash
bd ready          # Available work        bd update <id> --claim   # Claim
bd show <id>      # Issue details         bd close <id>            # Complete
```

Backend is the **shared Dolt server** (`dolt.parrot-hen.ts.net:3307`, database `butlers`), discovered via
`.beads/metadata.json` (`dolt_mode: server`). Dolt is the source of truth and `bd create/update/close`
commit to it directly — **there is no `bd sync` in this version.**

- The `.beads/` JSONL is a local, gitignored mirror; **never commit it**. Refresh with
  `bd export -o .beads/issues.export.jsonl` (see `export.path` in `.beads/config.yaml`).
- **Never create `.beads/issues.jsonl`** — on bd 1.0.4 server mode its presence triggers a full-file
  re-import on every write that can wedge bd town-wide.

## Session Completion

Work is **not** complete until `git push` succeeds — never stop before pushing, and never hand back
with "ready to push when you are". Before signalling done:

1. File beads for remaining/follow-up work; close or update what you finished.
2. Run the quality gates above if code changed.
3. `git pull --rebase && git push`, then confirm `git status` shows up to date with origin. If push
   fails, resolve and retry until it succeeds.
4. Clean up (stashes, merged worktrees, stale remote branches) and hand off context for the next
   session.
