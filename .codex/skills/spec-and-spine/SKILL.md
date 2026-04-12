---
name: spec-and-spine
description: >
  Use when the question is what behavior Butlers is required to implement. Load before
  implementing or reviewing feature work, reconciling spec-code drift, deciding whether a
  change needs a new spec, or locating the relevant capability spec or active OpenSpec change
  in `openspec/`. Triggers: "check the spec", "what does the spec say", "which spec covers X",
  "spec drift", "does the code match the spec", "need a new spec".
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
  status: active
  last_reviewed: "2026-04-12"
---

# Spec and Spine

`openspec/` is the WHAT layer of the Butlers knowledge architecture. Use this skill to find the
normative behavior for a capability, determine whether an implementation matches that behavior,
and route follow-on spec work into the correct OpenSpec workflow.

## Source of Truth

Read these in order:

1. `about/README.md` for pillar precedence and the "Spec and Spine" role in the traceability chain.
2. `openspec/specs/<domain>/spec.md` for the canonical requirement set.
3. `openspec/changes/<change>/specs/<domain>/spec.md` only when an active change is modifying the capability.
4. `about/law-and-lore/rfcs/*.md` when the requirement's design rationale or contract source is unclear.
5. `about/craft-and-care/` when deciding how the spec change must be tested, reviewed, or documented.

Archived changes are historical context, not the live source of truth.

## Use This Skill For

- Finding the right capability spec before implementation or review
- Checking whether code matches the current spec
- Deciding whether a behavior change needs an OpenSpec change first
- Determining whether a delta spec in `openspec/changes/` is the active authority for a capability
- Turning requirements and scenarios into implementation and test targets

## Do Not Use This Skill For

- Project purpose or scope arguments: use `heart-and-soul`
- Wire contracts, state machines, or schema design: use `law-and-lore`
- Test scope, verification bar, or documentation hygiene: use `craft-and-care`
- Code ownership or placement questions: use `lay-and-land`

## Fast Routing

Use the naming pattern first; then confirm by reading the file:

| If the work smells like... | Start with... |
|---|---|
| daemon, sessions, scheduler, telemetry, modules, credentials | `openspec/specs/core-*/spec.md` |
| butler identity or per-butler guarantees | `openspec/specs/butler-*/spec.md` |
| modules | `openspec/specs/module-*/spec.md` |
| connectors | `openspec/specs/connector-*/spec.md` |
| dashboard or API surfaces | `openspec/specs/dashboard-*/spec.md` |
| memory, finance, healing, identity, cross-cutting capabilities | `openspec/specs/<domain>/spec.md` by keyword search |

Useful repo-native lookups:

```bash
find openspec/specs -mindepth 2 -maxdepth 2 -name spec.md | sort
rg -n "<keyword>|^### Requirement:|^#### Scenario:" openspec/specs
find openspec/changes -path '*/specs/*/spec.md' | sort
rg -n "<keyword>|^### Requirement:|^#### Scenario:" openspec/changes
```

## Grounding Workflow

1. Identify the capability domain from the task language and touched codepaths.
2. Read the canonical spec in `openspec/specs/` before touching code.
3. Check `openspec/changes/` for an active delta spec affecting the same domain.
4. If a delta exists, read its `proposal.md`, `design.md`, and `tasks.md` alongside the changed spec.
5. Extract the exact requirements and scenarios that bind the work.
6. Map those scenarios to tests, review expectations, and code changes.

## Decision Rules

- Mainline specs in `openspec/specs/` are canonical unless an active change is intentionally modifying them.
- Active change specs are authoritative only for the scope of that change until synced or archived.
- If code and spec disagree, do not silently treat code as truth. Check for an active change, then either fix code or update the spec explicitly.
- If a non-trivial behavior has no spec coverage, start with an OpenSpec change before implementation.
- If the spec is vague, resolve the ambiguity in the spec and scenarios rather than encoding hidden assumptions in code.
- Role specs define stable behavioral contracts; frequently changing operational values belong in roster config or runtime state, not in the role spec.

## OpenSpec Workflow Hand-off

Use the existing OpenSpec workflow skills for the next step:

- `openspec-explore`: clarify a new capability before writing the change
- `openspec-new-change`: create a new change with proposal, design, tasks, and delta specs
- `openspec-continue-change`: continue an in-progress change
- `openspec-apply-change`: implement an approved change
- `openspec-verify-change`: verify code matches the change artifacts
- `openspec-sync-specs`: sync finalized delta specs into main specs
- `openspec-archive-change`: archive a completed change

## Review and Implementation Expectations

- Quote or reference the specific requirement and scenario headings that justify the change.
- When behavior changes, update the relevant spec in the same change, not as follow-up drift.
- Convert the binding WHEN/THEN scenarios into tests or an explicit verification checklist.
- If the task crosses pillars, re-load `law-and-lore` for contract rationale and `craft-and-care` for execution quality.
