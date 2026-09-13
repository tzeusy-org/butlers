# OpenSpec Workflow

> **Purpose:** Explain where requirements live and how a change reaches the baseline.
> **Audience:** Contributors proposing changes and reviewers checking delivery.
> **Prerequisites:** [Project knowledge map](../../about/README.md)

[Capability specs](../../openspec/specs/) are the maintained requirements.
[Active changes](../../openspec/changes/) contain proposals and deltas;
[archived changes](../../openspec/changes/archive/) preserve their delivery
history. A proposal is not implementation authority, and an archive directory
is not proof that its requirements reached the baseline.

| Artifact | Question it answers |
|---|---|
| `openspec/specs/<capability>/spec.md` | What behavior is required now? |
| `openspec/changes/<change>/proposal.md` | Why change it, and which capabilities are affected? |
| `openspec/changes/<change>/specs/<capability>/spec.md` | Which requirements and WHEN/THEN scenarios change? |
| `openspec/changes/<change>/design.md` | How will the change work, including tradeoffs and migration? |
| `openspec/changes/<change>/tasks.md` | Which implementation and verification steps remain? |
| `openspec/changes/archive/<dated-change>/` | What was proposed and delivered in that change? |

## Contributor Workflow

1. Read the governing doctrine, RFC, and baseline requirements. The
   [development principles](../../about/heart-and-soul/development.md#openspec-driven-development)
   define when a specification or RFC is required.
2. Write the proposal and capability deltas. Preserve existing requirements
   and scenarios unless the change explicitly amends them; record unresolved
   decisions and obtain any required owner sign-off before implementation.
3. Capture design choices in `design.md` and use countable `- [ ]` checkboxes
   in `tasks.md`. Track execution ownership and dependencies in Beads, as
   required by the [development workflow](../../about/heart-and-soul/development.md#issue-tracking-with-beads).
4. Implement and verify the approved behavior. Update the affected specs,
   runbooks, and interface documentation in the same change.
5. Before archiving, compare same-named MODIFIED requirements in other active
   changes. Apply the delivered delta to the baseline and inspect the actual
   requirement-body diff. Rebuild any overlapping delta against that refreshed
   baseline so a later archive cannot undo it. Follow the
   [repository archive cautions](../../AGENTS.md#two-unarchived-openspec-changes-can-silently-overwrite-each-other).
6. Validate the baseline and active changes, then archive the completed change.
   Confirm its requirements landed and refresh relevant v1 evidence before
   closing the epic. Moving a directory alone does not complete this step.

## Verification

```bash
openspec list
openspec validate --all --strict
make check-spec-overwrites
python3 scripts/check_archived_requirements_landed.py
```

These commands inspect different properties: syntax and scenarios, destructive
active deltas, and missing archived requirements. Review the semantic diff as
well; validator success does not establish approval, implementation, or runtime
health. `make check-guards` runs the repository's combined guard set.

## Related Pages

- [Project plan](project-plan.md): maintained scope and execution sources.
- [Documentation maintenance](../../about/craft-and-care/review-and-documentation.md#document-lifecycle): synthesis and retirement rules.
