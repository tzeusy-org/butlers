---
name: heart-and-soul
description: >
  CRITICAL -- Load the project's foundational doctrine before making architectural decisions,
  writing code, designing APIs, creating tests, or proposing features. The about/heart-and-soul/
  directory contains prime directives: what Butlers is, what it is not, non-negotiable rules,
  and v1 scope. Selectively load ONLY the documents relevant to your current task. Use
  proactively at the start of substantive work, when making design decisions, or when unsure
  about project conventions.
---

# Heart and Soul -- Project Doctrine

The `about/heart-and-soul/` directory is the WHY pillar of the Butlers knowledge architecture. It governs design decisions, scope arguments, and feature debates. When in doubt, start here.

## Five-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| **Doctrine** | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| Engineering Standards | `about/craft-and-care/` | WHO WE ARE WHEN WE BUILD -- engineering character in practice: implementation quality, verification, review, operability, maintainability |
| Design Contracts | `about/legends-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| Topology | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |

## Document Index

| # | File | Status | What it answers |
|---|------|--------|-----------------|
| 1 | `about/heart-and-soul/vision.md` | EXISTS | Core thesis, what Butlers IS and IS NOT, non-negotiable rules, success criteria, anti-patterns |
| 2 | `about/heart-and-soul/architecture.md` | EXISTS | Structural philosophy: butler-as-daemon, MCP universal interface, domain specialization, modules, connectors, schema isolation, core loop |
| 3 | `about/heart-and-soul/v1.md` | EXISTS | What v1 ships vs defers, platform targets, success criteria |
| 4 | `about/heart-and-soul/security.md` | EXISTS | Trust model, LLM sandboxing, approval gates, credential store |
| 5 | `about/heart-and-soul/development.md` | EXISTS | TDD, OpenSpec-driven, beads issue tracking, manifesto-driven design |

Consult `about/heart-and-soul/README.md` for the canonical reading order and usage guide.

## Non-Negotiable Rules

Read the seven rules in `about/heart-and-soul/vision.md`; do not rely on a copy.
Since 2026-10-14 the doctrine describes goal-based reasoning: one lead per goal,
optional depth-1 children, grants derived from goal origin and enforced by the
Switchboard broker (RFC 0038). Discovery and task prompts never confer
authority. The code reaches this design in one hard cutover; until then the
classify-and-route path is frozen to fixes.

## Do Not Use This Skill For

- Wire contracts, state machines, or schema design: use `legends-and-lore`
- Feature behavior and acceptance scenarios: use `spec-and-spine`
- Test scope, verification bar, or documentation hygiene: use `craft-and-care`
- Code ownership or placement questions: use `lay-and-land`

## When to Load

- Starting substantive work on any butler or core component
- Making architectural decisions or proposing new features
- Writing code that touches inter-butler communication, module boundaries, or transport
- Unsure whether a feature belongs in core, a module, or a connector
- Resolving scope disputes or design disagreements
- Reviewing PRs for alignment with project principles

## How to Use

1. Read the specific document(s) relevant to your task -- do not load all five unless necessary.
2. For scope questions: `vision.md` (non-negotiable rules) and `v1.md` (what ships).
3. For design philosophy: `architecture.md`.
4. For security concerns: `security.md`.
5. For workflow and process: `development.md`.
