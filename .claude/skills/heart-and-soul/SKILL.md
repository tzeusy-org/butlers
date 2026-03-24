---
name: heart-and-soul
description: >
  CRITICAL -- Load the project's foundational doctrine before making architectural decisions,
  writing code, designing APIs, creating tests, or proposing features. The docs/heart-and-soul/
  directory contains prime directives: what Butlers is, what it is not, non-negotiable rules,
  and v1 scope. Selectively load ONLY the documents relevant to your current task. Use
  proactively at the start of substantive work, when making design decisions, or when unsure
  about project conventions.
---

# Heart and Soul -- Project Doctrine

The `docs/heart-and-soul/` directory is the WHY pillar of the Butlers knowledge architecture. It governs design decisions, scope arguments, and feature debates. When in doubt, start here.

## Four-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| **Doctrine** | `docs/heart-and-soul/` | WHY -- vision, principles, scope |
| Design Contracts | `docs/law-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| Topology | `docs/lay-and-land/` | WHERE -- component maps, data flow, deployment |

## Document Index

| # | File | Status | What it answers |
|---|------|--------|-----------------|
| 1 | `docs/heart-and-soul/vision.md` | EXISTS | Core thesis, what Butlers IS and IS NOT, non-negotiable rules, success criteria, anti-patterns |
| 2 | `docs/heart-and-soul/architecture.md` | EXISTS | Structural philosophy: butler-as-daemon, MCP universal interface, domain specialization, modules, connectors, schema isolation, core loop |
| 3 | `docs/heart-and-soul/v1.md` | EXISTS | What v1 ships vs defers, platform targets, success criteria |
| 4 | `docs/heart-and-soul/security.md` | EXISTS | Trust model, LLM sandboxing, approval gates, credential store |
| 5 | `docs/heart-and-soul/development.md` | EXISTS | TDD, OpenSpec-driven, beads issue tracking, manifesto-driven design |

Consult `docs/heart-and-soul/README.md` for the canonical reading order and usage guide.

## Non-Negotiable Rules (from vision.md)

These are the load-bearing constraints. Violating any of them means the change does not ship.

1. **User-federated**: one user, one instance, full sovereignty. No multi-tenancy.
2. **Modules only add tools**: they never touch core infrastructure.
3. **Inter-butler communication is MCP-only through the Switchboard**: no shared memory, no direct calls, no schema cross-access.
4. **Daemon is deterministic infrastructure; intelligence is in ephemeral LLM sessions**: no reasoning in the daemon.
5. **Git-based config is the source of truth for butler identity**: personality, schedule, modules, manifesto live in `roster/`.
6. **Each butler has a manifesto that governs its scope and personality**: features must align with the manifesto.
7. **Transport is connector responsibility; butlers never know about transport details**: connectors normalize, butlers receive structured requests.

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
