# About Butlers

Butlers is a personal AI agent system where specialized, long-running daemons
handle the recurring mental labor of daily life. Each butler owns a life
domain — health, relationships, finance, education, travel, home, lifestyle —
and acts autonomously on schedules and in response to incoming messages. One
user. One instance. Full sovereignty over data, credentials, and LLM keys.

## The Five-Pillar Knowledge Architecture

The project's self-knowledge is organized into five pillars. Four live here
under `about/` with poetic names; capability specs live at `openspec/` with
their own structure and tooling.

| Pillar | Directory | Question | Content | Start here |
|--------|-----------|----------|---------|------------|
| **Heart and Soul** | `about/heart-and-soul/` | **WHY** does this exist? | Vision, 7 non-negotiable rules, scope boundaries, anti-patterns | [vision.md](heart-and-soul/vision.md) |
| **Craft and Care** | `about/craft-and-care/` | **WHO ARE WE WHEN WE BUILD?** | Engineering character in practice: engineering bar, testing discipline, review expectations, observability, security, maintainability | [README.md](craft-and-care/README.md) |
| **Legends and Lore** | `about/legends-and-lore/` | **HOW** will it work? | RFCs defining wire contracts, state machines, data models | [README.md](legends-and-lore/README.md) |
| **Spec and Spine** | `openspec/` | **WHAT** exactly must be built? | Capability specs with WHEN/THEN scenarios | `openspec/specs/` |
| **Lay and Land** | `about/lay-and-land/` | **WHERE** does everything live? | Component maps, data flow, dependencies, deployment topology | [README.md](lay-and-land/README.md) |

### Traceability Chain

Every implementation decision should trace back through this chain:

```
Doctrine principle → RFC design decision → Spec requirement → Code → Test
```

Topology cross-cuts all layers — it shows where the doctrine is embodied,
where the design contracts apply, and where the specs are implemented. Craft
and Care cross-cuts the same chain as the execution-quality layer: it defines
how changes to any part of the chain must be implemented, verified, reviewed,
and documented.

### Precedence Order When Layers Disagree

The pillars above are not a free-for-all. When two artefacts disagree, resolve
them in this order — higher numbers defer to lower numbers, never the reverse:

| # | Layer | Owns | Home |
|---|-------|------|------|
| 1 | **Heart and Soul** | Principles, scope boundaries, the 7 non-negotiable rules | `about/heart-and-soul/` |
| 2 | **Legends and Lore** | Wire contracts, state machines, data models, sanctioned rule exceptions | `about/legends-and-lore/rfcs/` |
| 3 | **Spec and Spine** | Feature behaviour, acceptance scenarios (WHEN/THEN), per-butler contracts | `openspec/specs/` |
| 4 | **Craft and Care** | Execution-quality standards, test scope, review gates, observability bar | `about/craft-and-care/` |
| 5 | **Lay and Land** | Topology snapshot — where components live, how they connect, stability levels | `about/lay-and-land/` |
| 6 | **Roster config** | Live butler identity: `butler.toml`, `MANIFESTO.md`, `CLAUDE.md`, skills, API routes | `roster/{butler}/` |
| 7 | **Code** | Runtime behaviour — executed source, migrations, tests | `src/`, `alembic/`, `tests/` |

Precedence in practice:

- **Higher layers (1–2) bind lower layers.** A commit that contradicts Heart
  and Soul without a formal RFC does not ship. A commit that contradicts an
  accepted RFC without amending the RFC does not ship.
- **Specs describe intended behaviour; roster describes identity; code
  implements both.** When a spec and the code disagree, the spec must be
  either updated or the code must be fixed — never both left stale. Same for
  roster ↔ code.
- **Roster is the source of truth for live butler identity (Rule 5).** Models,
  schedules, modules, and manifesto are owned by `roster/{butler}/` — not by
  the role spec. The role spec is a stable *contract* about scope and
  guarantees; it does not mirror every roster field. If a detail drifts
  frequently (model IDs, concurrency caps, exact cron minutes), it belongs in
  the roster, not in the spec.
- **Operational tuning lives in the database, not git.** Per-butler
  `runtime_config` row overrides roster seed values at runtime. Changes
  there do not require spec or roster edits.
- **Topology (Lay and Land) is a snapshot, not a contract.** If `components.md`
  disagrees with what is running, fix `components.md` — it does not drive the
  build.

The repo ships contract tests under `tests/contracts/` (marker: `contract`) that
project from one source of truth into the artefacts below it, so drift fails in
CI rather than in production. When you add a new cross-cutting invariant, add
a contract test that projects from the highest-level artefact downward.

## Reading Order

**New to the project?** Read top-down — each pillar grounds the next:

1. **[vision.md](heart-and-soul/vision.md)** — The thesis: what Butlers is,
   what it is not, and the seven non-negotiable architectural rules.
2. **[v1.md](heart-and-soul/v1.md)** — What v1 ships and what it defers.
   Scope debates end here.
3. **[Craft and Care README](craft-and-care/README.md)** — The engineering bar:
   testing, verification, review, observability, security, and maintainability.
4. **[Legends and Lore README](legends-and-lore/README.md)** — Index of RFCs in
   recommended data-flow reading order.
5. **[components.md](lay-and-land/components.md)** — Every runtime piece,
   what it owns, and its stability level.
6. **`openspec/specs/`** — Browse by capability domain for detailed
   requirements.

**Already familiar?** Jump to the pillar that answers your question:
- *"Can I do X?"* → Heart and Soul (check the non-negotiable rules)
- *"What quality bar must this change meet?"* → Craft and Care
- *"How does X work at the wire level?"* → Legends and Lore (find the RFC)
- *"What exactly must X do?"* → Spec and Spine (find the spec)
- *"Where does X live in the codebase?"* → Lay and Land (check components)

## Key Architectural Facts

- **11 daemons** — 3 staffers (Switchboard, Messenger, QA) + 8 domain
  butlers, each a FastMCP server on its own port.
- **MCP everywhere** — LLM-to-butler, butler-to-butler (via Switchboard),
  and connector-to-Switchboard all use MCP.
- **Single PostgreSQL, per-butler schemas** — Logical isolation without
  physical overhead. Cross-butler data lives in the `public` schema.
- **Deterministic daemon, intelligent sessions** — The daemon manages
  lifecycle; reasoning happens exclusively in ephemeral LLM sessions.
- **Modules only add tools** — They never touch core infrastructure.

For a narrative introduction, see the
[blog post](https://tze.how/blog/butlers-introduction).
