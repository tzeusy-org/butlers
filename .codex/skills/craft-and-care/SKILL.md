---
name: craft-and-care
description: >
  MANDATORY for all non-trivial implementation work. Use when implementing, reviewing, or
  validating changes in this repository and the question is how the work should be executed well.
  Load before choosing test scope, deciding whether specs or manifesto updates are required,
  running quality gates, assessing change hygiene, or checking whether a change meets the
  Butlers engineering bar.
---

# Craft and Care -- Engineering Standards

The `about/craft-and-care/` directory is the engineering-character pillar of the Butlers knowledge
architecture. It answers **WHO ARE WE WHEN WE BUILD?** -- not just how to do the work, but what
kind of engineer this repository expects you to be while doing it. Use it to load the engineering
bar before implementing, reviewing, or validating non-trivial changes.

## Five-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| Doctrine | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| Design Contracts | `about/legends-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| Topology | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |
| **Engineering Standards** | `about/craft-and-care/` | WHO WE ARE WHEN WE BUILD -- engineering character in practice: implementation quality, verification, review, operability, maintainability |

## Current Standards Index

Read only the files relevant to your task:

| # | File | Status | What it governs |
|---|------|--------|-----------------|
| 0 | `about/craft-and-care/README.md` | EXISTS | Orienting to the pillar: scope boundary, reading order, file map |
| 1 | `about/craft-and-care/engineering-bar.md` | EXISTS | Default-bar adoption from `/th-engineering`, Butlers-specific done criteria, bias additions, blocking change shapes |
| 2 | `about/craft-and-care/testing-and-verification.md` | EXISTS | Evidence standards, graduated test scope, final quality-gate expectations |
| 3 | `about/craft-and-care/review-and-documentation.md` | EXISTS | Blocking review criteria, author/reviewer obligations, same-change doc updates |
| 4 | `about/craft-and-care/observability-and-operations.md` | EXISTS | Diagnosability, runtime status expectations, telemetry discipline |
| 5 | `about/craft-and-care/interfaces-and-dependencies.md` | EXISTS | Interface evolution, compatibility discipline, dependency admission bar |
| 6 | `about/craft-and-care/security-and-secrets.md` | EXISTS | Secret handling, privilege boundaries, security-sensitive change triggers |
| 7 | `about/craft-and-care/performance-discipline.md` | EXISTS | Evidence-driven optimization rules and performance anti-patterns |
| 8 | `about/heart-and-soul/development.md` | EXISTS | Workflow doctrine that this pillar refines into explicit standards |
| 9 | `AGENTS.md` | EXISTS | Repo-specific contracts, caveats, and execution details discovered during implementation |

## Default Biases

The canonical default quality bar -- the nine engineering biases, the definition of done, and the
change-level subskills that operationalize them (code-readability, test-rigor, dependency-hygiene,
cruft-cleanup) -- lives in `/th-engineering` (engineering-bar subskill). This pillar adopts that
bar by reference. The documents in `about/craft-and-care/` record this project's standards and
overrides; when they conflict with the default bar, this pillar wins.

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
   - design-contract questions -> `legends-and-lore`
   - feature-behavior questions -> `spec-and-spine`
   - ownership/location questions -> `lay-and-land`

## Mandatory Use Rule

For non-trivial implementation work, this skill is not optional. If the task requires judgment
about testing, review quality, observability, compatibility, dependency hygiene, documentation,
security, performance discipline, or maintainability, load this pillar.
