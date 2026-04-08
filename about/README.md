# About Butlers

Butlers is a personal AI agent system where specialized, long-running daemons
handle the recurring mental labor of daily life. Each butler owns a life
domain — health, relationships, finance, education, travel, home, lifestyle —
and acts autonomously on schedules and in response to incoming messages. One
user. One instance. Full sovereignty over data, credentials, and LLM keys.

## The Four-Pillar Knowledge Architecture

The project's self-knowledge is organized into four pillars. Three live here
under `about/` with poetic names; capability specs live at `openspec/` with
their own structure and tooling.

| Pillar | Directory | Question | Content | Start here |
|--------|-----------|----------|---------|------------|
| **Heart and Soul** | `about/heart-and-soul/` | **WHY** does this exist? | Vision, 7 non-negotiable rules, scope boundaries, anti-patterns | [vision.md](heart-and-soul/vision.md) |
| **Law and Lore** | `about/law-and-lore/` | **HOW** will it work? | 12 RFCs defining wire contracts, state machines, data models | [README.md](law-and-lore/README.md) |
| **Spec and Spine** | `openspec/` | **WHAT** exactly must be built? | 141 capability specs with WHEN/THEN scenarios | `openspec/specs/` |
| **Lay and Land** | `about/lay-and-land/` | **WHERE** does everything live? | Component maps, data flow, dependencies, deployment topology | [README.md](lay-and-land/README.md) |

### Traceability Chain

Every implementation decision should trace back through this chain:

```
Doctrine principle → RFC design decision → Spec requirement → Code → Test
```

Topology cross-cuts all layers — it shows where the doctrine is embodied,
where the design contracts apply, and where the specs are implemented.

## Reading Order

**New to the project?** Read top-down — each pillar grounds the next:

1. **[vision.md](heart-and-soul/vision.md)** — The thesis: what Butlers is,
   what it is not, and the seven non-negotiable architectural rules.
2. **[v1.md](heart-and-soul/v1.md)** — What v1 ships and what it defers.
   Scope debates end here.
3. **[Law and Lore README](law-and-lore/README.md)** — Index of 12 RFCs in
   recommended data-flow reading order.
4. **[components.md](lay-and-land/components.md)** — Every runtime piece,
   what it owns, and its stability level.
5. **`openspec/specs/`** — Browse by capability domain for detailed
   requirements.

**Already familiar?** Jump to the pillar that answers your question:
- *"Can I do X?"* → Heart and Soul (check the non-negotiable rules)
- *"How does X work at the wire level?"* → Law and Lore (find the RFC)
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
