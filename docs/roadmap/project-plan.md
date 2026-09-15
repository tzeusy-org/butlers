# Project Plan

> **Purpose:** Locate the maintained scope, milestone evidence, and execution plan.
> **Audience:** Contributors and reviewers deciding what work is required or complete.

Butlers develops against capability specifications grounded in its doctrine.
A list of implemented components is not evidence that a milestone is complete:
completion depends on the promised behavior and its verification.

| Question | Maintained source |
|---|---|
| What belongs in the product? | [Vision](../../about/heart-and-soul/vision.md) and [v1 scope](../../about/heart-and-soul/v1.md) |
| Which v1 criteria have evidence? | [v1 status](../../about/heart-and-soul/v1-status.md), including its refresh rule |
| What behavior is required? | [Capability specifications](../../openspec/specs/) |
| What changes are being designed or delivered? | [Active OpenSpec changes](../../openspec/changes/); an unarchived directory alone does not prove approval or implementation |
| What can be worked on now? | Beads dependencies, ownership, and acceptance criteria; start with `bd ready`, then `bd show <id>` |
| What actually runs? | [Topology](../../about/lay-and-land/README.md), [roster](../../roster/), and the relevant implementation and runtime evidence |

Dated audit dossiers record observations and proposals at a particular point
in time. They inform planning; they do not replace current specifications or
prove that work shipped. The [redesign index](../redesigns/README.md) identifies
those retained records.

## Verification

Run `openspec list` to inspect unarchived change status and `bd show <id>` for
the owning task. Check the cited commit, tests, and deployment evidence before
reusing a completion claim from the v1 status page or a historical report.

## Related Pages

- [OpenSpec workflow](openspec-overview.md): proposing, validating, and closing a change.
- [Testing and verification](../../about/craft-and-care/testing-and-verification.md): the evidence required for completion.
