---
name: craft-and-care
description: >
  Use when implementing, reviewing, or validating changes in this repository and the question is
  how the work should be executed well. Load before choosing test scope, deciding whether specs or
  manifesto updates are required, running quality gates, assessing change hygiene, or checking
  whether a change meets the Butlers engineering bar.
---

# Craft and Care -- Engineering Standards

The `about/craft-and-care/` directory is the execution-quality pillar of the Butlers knowledge
architecture. It answers **HOW SHOULD WORK BE EXECUTED WELL?** Use it to load the engineering bar
before implementing, reviewing, or validating non-trivial changes.

## Five-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| Doctrine | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| Design Contracts | `about/law-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| Topology | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |
| **Engineering Standards** | `about/craft-and-care/` | HOW SHOULD WORK BE EXECUTED WELL -- testing, verification, review, documentation, maintainability |

## Current Standards Index

Read only the files relevant to your task:

| # | File | Status | What it governs |
|---|------|--------|-----------------|
| 1 | `about/craft-and-care/engineering-bar.md` | EXISTS | Definition of done, change hygiene, cleanup bias, explicitness, durable-fix bar |
| 2 | `about/craft-and-care/testing-and-verification.md` | EXISTS | Evidence standards, graduated test scope, final quality-gate expectations |
| 3 | `about/craft-and-care/review-and-documentation.md` | EXISTS | Blocking review criteria, author/reviewer obligations, same-change doc updates |
| 4 | `about/craft-and-care/observability-and-operations.md` | EXISTS | Diagnosability, runtime status expectations, telemetry discipline |
| 5 | `about/craft-and-care/interfaces-and-dependencies.md` | EXISTS | Interface evolution, compatibility discipline, dependency admission bar |
| 6 | `about/craft-and-care/security-and-secrets.md` | EXISTS | Secret handling, privilege boundaries, security-sensitive change triggers |
| 7 | `about/craft-and-care/performance-discipline.md` | EXISTS | Evidence-driven optimization rules and performance anti-patterns |
| 8 | `about/heart-and-soul/development.md` | EXISTS | Workflow doctrine that this pillar refines into explicit standards |
| 9 | `AGENTS.md` | EXISTS | Repo-specific contracts, caveats, and execution details discovered during implementation |

## When to Load

- Before implementing or reviewing non-trivial changes
- When deciding whether a task needs a spec, RFC, manifesto edit, or only code/tests
- When choosing test scope or final verification depth
- When checking whether a change is clean enough to merge
- When a review comment questions maintainability, quality gates, or change hygiene
- When updating repo docs, workflows, migrations, or cross-cutting contracts

## How to Use

1. Start with `about/craft-and-care/engineering-bar.md` and then load only the specialized standards doc your task needs.
2. Cross-check `about/heart-and-soul/development.md` for workflow doctrine and `AGENTS.md` for repo-specific execution details.
3. Use `Makefile` and `pyproject.toml` only to confirm exact commands and tool configuration; do not invent alternate workflows.
4. Cross-load the other pillars as needed:
   - doctrine questions -> `heart-and-soul`
   - design-contract questions -> `law-and-lore`
   - feature-behavior questions -> `spec-and-spine`
   - ownership/location questions -> `lay-and-land`
