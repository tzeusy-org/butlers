---
name: doctrine
description: >
  Load the Butlers project's own normative knowledge before deciding, implementing, or reviewing:
  doctrine (why Butlers exists, what it refuses to be, the non-negotiable rules), design contracts
  (RFCs defining wire-level behavior), capability specs (required behavior in openspec/), topology
  (where components live and connect), and engineering standards (the bar for changing any of it).
  Routes to exactly one pillar; do not infer project conventions from code alone. Triggers: "what
  does Butlers believe", "is this in scope", "check the spec", "what does the spec say", "spec
  drift", "which RFC covers this", "where does this live", "what's the engineering bar here",
  "how should this be tested or reviewed", "does this violate a non-negotiable rule".
---

# Butlers Doctrine Router

Five-pillar knowledge architecture. Each pillar has a navigator under `subskills/`; load **at most
one** per question, then read only the pillar files that navigator points at. Never bulk-read a
pillar directory.

## Discover subskills

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
find "$PKG/subskills" -maxdepth 2 -name SKILL.md
grep -n '^name:' "$PKG"/subskills/*/SKILL.md
```

## Routing table

| The question is... | Pillar | Load |
|---|---|---|
| WHY — vision, scope, non-negotiable rules, what Butlers is NOT, v1 boundary, security posture | Doctrine (`about/heart-and-soul/`) | [subskills/heart-and-soul/SKILL.md](subskills/heart-and-soul/SKILL.md) |
| HOW — wire-level contracts, routing/ingestion protocols, state machines, RFC decisions | Design contracts (`about/legends-and-lore/`) | [subskills/legends-and-lore/SKILL.md](subskills/legends-and-lore/SKILL.md) |
| WHAT — required behavior, normative requirements, active OpenSpec changes, spec-code drift | Capability specs (`openspec/`) | [subskills/spec-and-spine/SKILL.md](subskills/spec-and-spine/SKILL.md) |
| WHERE — butlers, modules, connectors, schemas, dashboard surfaces, deployment topology | Topology (`about/lay-and-land/`) | [subskills/lay-and-land/SKILL.md](subskills/lay-and-land/SKILL.md) |
| WHO WE ARE WHEN WE BUILD — engineering bar, test scope, verification evidence, review, operability | Engineering standards (`about/craft-and-care/`) | [subskills/craft-and-care/SKILL.md](subskills/craft-and-care/SKILL.md) |

`craft-and-care` is mandatory for non-trivial implementation work, not optional context.

## Routing rules

- One pillar per question. "Is this in scope AND how do I test it" is two sequential loads, not a
  bulk read of both.
- A butler's own identity and value proposition lives in `roster/{butler}/MANIFESTO.md`, not in a
  pillar — read it directly when the question is about one butler's purpose or framing.
- Deciding what to build, prioritizing, auditing the repo → `/th-projects`. Change-level
  engineering judgment (readability, test rigor, diagnosis, cruft) → `/th-engineering`. Task
  tracking → `bd` (see `AGENTS.md`). This router covers Butlers' own recorded knowledge only.
- No pillar fits → answer from this router or say the project has not recorded it. Do not load a
  subskill to browse.

## Maintenance

This package is the canonical navigation layout produced by `/th-projects` (project-shape). Verify
it with that subskill's scanner — `bash <project-shape>/scripts/shape-scan.sh .` must report
`DOCTRINE_LAYOUT=SUPERSKILL`, and every installed subskill must be linked from the table above.

Frontmatter here is deliberately `name` + `description` only: project-shape's fail-closed validator
rejects any other key, so the `metadata:` block that `/th-engineering` (skill-standards) recommends
is intentionally omitted and its WARNs on this package are expected.
