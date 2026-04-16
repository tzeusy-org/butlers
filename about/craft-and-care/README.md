# Craft and Care -- Engineering Standards

This directory answers **HOW SHOULD WORK BE EXECUTED WELL?** It defines the
quality bar for implementation, verification, review, observability,
interfaces, security hygiene, documentation, and performance discipline in
Butlers.

These documents are standards, not subsystem design docs. They do not replace
the other pillars:

- **Heart and Soul** defines what the project is for and what it refuses to be.
- **Legends and Lore** defines wire-level and architectural contracts.
- **Spec and Spine** defines feature behavior and acceptance criteria.
- **Lay and Land** defines where components live and how they connect.
- **Craft and Care** defines how changes to any of those layers must be carried
  out well.

## Reading Order

| # | File | What it answers |
|---|------|-----------------|
| 1 | [engineering-bar.md](engineering-bar.md) | What makes a change complete, clean, and maintainable here |
| 2 | [testing-and-verification.md](testing-and-verification.md) | What evidence is required before calling work done |
| 3 | [review-and-documentation.md](review-and-documentation.md) | What reviewers should block on and what docs must change with behavior |
| 4 | [observability-and-operations.md](observability-and-operations.md) | What runtime-facing changes must expose to stay diagnosable and operable |
| 5 | [interfaces-and-dependencies.md](interfaces-and-dependencies.md) | How APIs, MCP tools, migrations, and dependencies should evolve |
| 6 | [security-and-secrets.md](security-and-secrets.md) | What secret handling and privilege boundaries must be preserved |
| 7 | [performance-discipline.md](performance-discipline.md) | How to treat performance and efficiency work without cargo culting |

## How to Use These

- **Implementing a feature or bug fix?** Start with
  [engineering-bar.md](engineering-bar.md) and
  [testing-and-verification.md](testing-and-verification.md).
- **Reviewing a change?** Use
  [review-and-documentation.md](review-and-documentation.md) as the blocking
  checklist.
- **Touching background jobs, connectors, routing, or recovery paths?** Read
  [observability-and-operations.md](observability-and-operations.md).
- **Changing an MCP tool, API, schema boundary, or dependency?** Read
  [interfaces-and-dependencies.md](interfaces-and-dependencies.md).
- **Handling credentials, OAuth, contact data, or DB privileges?** Read
  [security-and-secrets.md](security-and-secrets.md).
- **Claiming an optimization?** Read
  [performance-discipline.md](performance-discipline.md).

## Current Repo Alignment

These standards are grounded in the existing workflow and tooling already used
in the repository:

- Test-first and spec-first discipline from
  [`about/heart-and-soul/development.md`](../heart-and-soul/development.md)
- Repo-specific execution constraints and quality gates from
  [`AGENTS.md`](../../AGENTS.md)
- Command entrypoints from [`Makefile`](../../Makefile)
- Tool configuration from [`pyproject.toml`](../../pyproject.toml)

When these standards and the implementation drift, update both in the same
change.
