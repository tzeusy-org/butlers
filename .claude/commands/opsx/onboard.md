---
name: "OPSX: Onboard"
description: Guided onboarding - walk through a complete OpenSpec workflow cycle with narration
category: Workflow
tags: [workflow, onboarding, tutorial, learning]
---

Guide the user through their first complete OpenSpec workflow cycle. This is a teaching experience—you'll do real work in their codebase while explaining each step.

---

## Preflight

Before starting, check if OpenSpec is initialized:

```bash
openspec status --json 2>&1 || echo "NOT_INITIALIZED"
```

**If not initialized:**
> OpenSpec isn't set up in this project yet. Run `openspec init` first, then come back to `/opsx:onboard`.

Stop here if not initialized.

---

## Phase 1: Welcome

Display:

```
## Welcome to OpenSpec!

I'll walk you through a complete change cycle—from idea to implementation—using a real task in your codebase. Along the way, you'll learn the workflow by doing it.

**What we'll do:**
1. Pick a small, real task in your codebase
2. Explore the problem briefly
3. Create a change (the container for our work)
4. Build proposal → tasks, proposal → design → tasks, or the retained spec-driven artifact graph
5. Implement the tasks
6. Archive the completed change

**Time:** ~15-20 minutes

Let's start by finding something to work on.
```

---

## Phase 2: Task Selection

### Codebase Analysis

Scan the codebase for small improvement opportunities. Look for:

1. **TODO/FIXME comments** - Search for `TODO`, `FIXME`, `HACK`, `XXX` in code files
2. **Missing error handling** - `catch` blocks that swallow errors, risky operations without try-catch
3. **Functions without tests** - Cross-reference `src/` with test directories
4. **Type issues** - `any` types in TypeScript files (`: any`, `as any`)
5. **Debug artifacts** - `console.log`, `console.debug`, `debugger` statements in non-debug code
6. **Missing validation** - User input handlers without validation

Also check recent git activity:
```bash
git log --oneline -10 2>/dev/null || echo "No git history"
```

### Present Suggestions

From your analysis, present 3-4 specific suggestions:

```
## Task Suggestions

Based on scanning your codebase, here are some good starter tasks:

**1. [Most promising task]**
   Location: `src/path/to/file.ts:42`
   Scope: ~1-2 files, ~20-30 lines
   Why it's good: [brief reason]

**2. [Second task]**
   Location: `src/another/file.ts`
   Scope: ~1 file, ~15 lines
   Why it's good: [brief reason]

**3. [Third task]**
   Location: [location]
   Scope: [estimate]
   Why it's good: [brief reason]

**4. Something else?**
   Tell me what you'd like to work on.

Which task interests you? (Pick a number or describe your own)
```

**If nothing found:** Fall back to asking what the user wants to build:
> I didn't find obvious quick wins in your codebase. What's something small you've been meaning to add or fix?

### Scope Guardrail

If the user picks or describes something too large (major feature, multi-day work):

```
That's a valuable task, but it's probably larger than ideal for your first OpenSpec run-through.

For learning the workflow, smaller is better—it lets you see the full cycle without getting stuck in implementation details.

**Options:**
1. **Slice it smaller** - What's the smallest useful piece of [their task]? Maybe just [specific slice]?
2. **Pick something else** - One of the other suggestions, or a different small task?
3. **Do it anyway** - If you really want to tackle this, we can. Just know it'll take longer.

What would you prefer?
```

Let the user override if they insist—this is a soft guardrail.

---

## Phase 3: Explore Demo

Once a task is selected, briefly demonstrate explore mode:

```
Before we create a change, let me quickly show you **explore mode**—it's how you think through problems before committing to a direction.
```

Spend 1-2 minutes investigating the relevant code:
- Read the file(s) involved
- Draw a quick ASCII diagram if it helps
- Note any considerations

```
## Quick Exploration

[Your brief analysis—what you found, any considerations]

┌─────────────────────────────────────────┐
│   [Optional: ASCII diagram if helpful]  │
└─────────────────────────────────────────┘

Explore mode (`/opsx:explore`) is for this kind of thinking—investigating before implementing. You can use it anytime you need to think through a problem.

Now let's create a change to hold our work.
```

**PAUSE** - Wait for user acknowledgment before proceeding.

---

## Phase 4: Create the Change

**EXPLAIN:**
```
## Creating a Change

OpenSpec keeps canonical requirements in `openspec/specs/`. New single-butler work with no local deltas uses proposal and tasks; `living-specs` adds a required design. Both preserve the generated schema/created metadata and use `skip_specs: true`. Retained multi-PR delta programs stay spec-driven and keep their local deltas. Existing designs are kept and read. The rule lives in `about/heart-and-soul/development.md`.

Let me create one for our task.
```

**DO:** Create the change with a derived kebab-case name:
Classify the actual scope and whether this is a retained multi-PR delta program. Use
`--schema single-butler` for eligible new single-butler work without local deltas; use
`--schema living-specs` when design is required but no local deltas are needed; keep
`--schema spec-driven` for retained delta programs. New no-delta changes preserve their generated
schema/created metadata and add `skip_specs: true`. Verify those fields and the marker before
continuing; if they are missing or malformed, correct the new change first. Existing
spec/review/verification and owner-approval gates remain; schema readiness alone does not authorize work.
```bash
openspec new change "<derived-name>" --schema <selected-schema>
```

**SHOW:**
```
Created: `openspec/changes/<name>/`

The generated folder depends on the selected schema:
```
openspec/changes/<name>/
├── .openspec.yaml ← Keep schema/created; add skip_specs: true only for new no-delta schemas
├── proposal.md    ← Why we're doing this (empty, we'll fill it)
├── design.md      ← Present for living-specs or when otherwise required/chosen
├── specs/         ← Present only for spec-driven local-delta programs
└── tasks.md       ← Implementation checklist (empty)
```
Single-butler and living-specs intentionally have no `specs/` directory. Update an affected
canonical requirement in the source change when required behavior changes.

Now let's fill in the first artifact—the proposal.
```

---

## Phase 5: Proposal

**EXPLAIN:**
```
## The Proposal

The proposal captures **why** we're making this change and **what** it involves at a high level. It's the "elevator pitch" for the work.

I'll draft one based on our task.
```

**DO:** Draft the proposal content (don't save yet):

```
Here's a draft proposal:

---

## Why

[1-2 sentences explaining the problem/opportunity]

## What Changes

[Bullet points of what will be different]

## Canonical Contract Impact

- `openspec/specs/<capability>/spec.md`: [unchanged, updated in this change, or not applicable]

## Local Delta Specs

<!-- Required only for a resolved spec-driven program; new no-delta schemas do not create these. -->
- `<capability-name>`: [brief description]

## Impact

- `src/path/to/file.ts`: [what changes]
- [other files if applicable]

---

Does this capture the intent? I can adjust before we save it.
```

**PAUSE** - Wait for user approval/feedback.

After approval, save the proposal:
```bash
openspec instructions proposal --change "<name>" --json
```
Then write the content to `openspec/changes/<name>/proposal.md`.

```
Proposal saved. This is your "why" document—you can always come back and refine it as understanding evolves.

Next, follow the resolved schema: `single-butler` goes to tasks (design may be added),
`living-specs` goes to design, and `spec-driven` goes to its local delta specs. Do not create
placeholder specs for either no-delta schema.
```

---

## Phase 6: Specs

**EXPLAIN:**
```
## Specs

This phase applies only when the resolved schema is `spec-driven` and its proposal declares local
deltas. Preserve the complete retained multi-PR program and author its actual ADDED/MODIFIED/
REMOVED/RENAMED requirements with scenarios and trace lines.

For a new `single-butler` or `living-specs` change, skip this phase: those schemas have no
change-local specs and use the canonical files under `openspec/specs/` as the maintained contract.
When required behavior changes, update the affected canonical requirement in the same source change.
Do not add an empty `specs/` directory or use `skip_specs` on a retained delta program.


---

## Phase 7: Design

For an eligible single-butler change, design is optional. If a new standalone change crosses
a cross-butler contract, shared schema or security boundary and has no local delta specs, use
`living-specs` and complete this phase. Retained multi-PR delta programs stay spec-driven. Read any
existing design in every case; choosing no local deltas does not omit technical judgment or
verification.

**EXPLAIN:**
```
## Design

The design captures **how** we'll build it—technical decisions, tradeoffs, approach.

For small changes, this might be brief. That's fine—not every change needs deep design discussion.
```

**DO:** Draft design.md:

```
Here's the design:

---

## Context

[Brief context about the current state]

## Goals / Non-Goals

**Goals:**
- [What we're trying to achieve]

**Non-Goals:**
- [What's explicitly out of scope]

## Decisions

### Decision 1: [Key decision]

[Explanation of approach and rationale]

---

For a small task, this captures the key decisions without over-engineering.
```

Save to `openspec/changes/<name>/design.md`.

---

## Phase 8: Tasks

**EXPLAIN:**
```
## Tasks

Finally, we break the work into implementation tasks—checkboxes that drive the apply phase.

These should be small, clear, and in logical order.
```

**DO:** Generate tasks from the proposal and resolved schema. Read the design when present and
read affected canonical specs for no-delta schemas; read local specs for a spec-driven program:

```
Here are the implementation tasks:

---

## 1. [Category or file]

- [ ] 1.1 [Specific task]
- [ ] 1.2 [Specific task]

## 2. Verify

- [ ] 2.1 [Verification step]

---

Each checkbox becomes a unit of work in the apply phase. Ready to implement?
```

**PAUSE** - Wait for user to confirm they're ready to implement.

Save to `openspec/changes/<name>/tasks.md`.

---

## Phase 9: Apply (Implementation)

**EXPLAIN:**
```
## Implementation

Now we implement each task, checking them off as we go. I'll announce each one and note how the canonical contract, local deltas when present, and design inform the approach.
```

**DO:** For each task:

1. Announce: "Working on task N: [description]"
2. Implement the change in the codebase
3. Reference specs/design naturally: "The spec says X, so I'm doing Y"
4. Mark complete in tasks.md: `- [ ]` → `- [x]`
5. Brief status: "✓ Task N complete"

Keep narration light—don't over-explain every line of code.

After all tasks:

```
## Implementation Complete

All tasks done:
- [x] Task 1
- [x] Task 2
- [x] ...

The change is implemented! One more step—let's archive it.
```

---

## Phase 10: Archive

**EXPLAIN:**
```
## Archiving

When a change is complete, we archive it. This moves it from `openspec/changes/` to `openspec/changes/archive/YYYY-MM-DD-<name>/`.

Archived changes become your project's decision history—you can always find them later to understand why something was built a certain way.
```

**DO:**
```bash
openspec archive "<name>"
```

**SHOW:**
```
Archived to: `openspec/changes/archive/YYYY-MM-DD-<name>/`

The change is now part of your project's history. The code is in your codebase, the decision record is preserved.
```

---

## Phase 11: Recap & Next Steps

```
## Congratulations!

You just completed a full OpenSpec cycle:

1. **Explore** - Thought through the problem
2. **New** - Created a change container
3. **Proposal** - Captured WHY
4. **Specs** - Defined WHAT in detail
5. **Design** - Decided HOW
6. **Tasks** - Broke it into steps
7. **Apply** - Implemented the work
8. **Archive** - Preserved the record

This same rhythm works for any size change—a small fix or a major feature.

---

## Command Reference

| Command | What it does |
|---------|--------------|
| `/opsx:explore` | Think through problems before/during work |
| `/opsx:new` | Start a new change, step through artifacts |
| `/opsx:ff` | Fast-forward: create all artifacts at once |
| `/opsx:continue` | Continue working on an existing change |
| `/opsx:apply` | Implement tasks from a change |
| `/opsx:verify` | Verify implementation matches artifacts |
| `/opsx:archive` | Archive a completed change |

---

## What's Next?

Try `/opsx:new` or `/opsx:ff` on something you actually want to build. You've got the rhythm now!
```

---

## Graceful Exit Handling

### User wants to stop mid-way

If the user says they need to stop, want to pause, or seem disengaged:

```
No problem! Your change is saved at `openspec/changes/<name>/`.

To pick up where we left off later:
- `/opsx:continue <name>` - Resume artifact creation
- `/opsx:apply <name>` - Jump to implementation (if tasks exist)

The work won't be lost. Come back whenever you're ready.
```

Exit gracefully without pressure.

### User just wants command reference

If the user says they just want to see the commands or skip the tutorial:

```
## OpenSpec Quick Reference

| Command | What it does |
|---------|--------------|
| `/opsx:explore` | Think through problems (no code changes) |
| `/opsx:new <name>` | Start a new change, step by step |
| `/opsx:ff <name>` | Fast-forward: all artifacts at once |
| `/opsx:continue <name>` | Continue an existing change |
| `/opsx:apply <name>` | Implement tasks |
| `/opsx:verify <name>` | Verify implementation |
| `/opsx:archive <name>` | Archive when done |

Try `/opsx:new` to start your first change, or `/opsx:ff` if you want to move fast.
```

Exit gracefully.

---

## Guardrails

- **Follow the EXPLAIN → DO → SHOW → PAUSE pattern** at key transitions (after explore, after proposal draft, after tasks, after archive)
- **Keep narration light** during implementation—teach without lecturing
- **Don't skip phases** even if the change is small—the goal is teaching the workflow
- **Pause for acknowledgment** at marked points, but don't over-pause
- **Handle exits gracefully**—never pressure the user to continue
- **Use real codebase tasks**—don't simulate or use fake examples
- **Adjust scope gently**—guide toward smaller tasks but respect user choice
