# OpenSpec Workflow

> **Purpose:** Explain where requirements live and how a change reaches the maintained contract.
> **Audience:** Contributors proposing changes and reviewers checking delivery.
> **Prerequisites:** [Project knowledge map](../../about/README.md)

[Capability specs](../../openspec/specs/) are the maintained requirements.
[Active changes](../../openspec/changes/) preserve the proposal, design, tasks,
and—only for retained or deliberately selected delta programs—local specs.
[Archived changes](../../openspec/changes/archive/) preserve delivery history.
A proposal is not implementation authority, and an archive is not proof that a
required canonical update was delivered.

| Artifact | Question it answers |
|---|---|
| `openspec/specs/<capability>/spec.md` | What behavior is required now? |
| `openspec/changes/<change>/proposal.md` | Why change it, and which canonical contracts are affected? |
| `openspec/changes/<change>/specs/<capability>/spec.md` | Which requirements change in a retained local-delta program? |
| `openspec/changes/<change>/design.md` | How will the change work, including boundaries, tradeoffs and migration? |
| `openspec/changes/<change>/tasks.md` | Which implementation and verification steps remain? |
| `openspec/changes/<change>/.openspec.yaml` | Which schema governs this change, and whether its new no-delta workflow skips local specs? |
| `openspec/changes/archive/<dated-change>/` | What was proposed and delivered in that change? |

## Choose a workflow

The project default remains `spec-driven`, which preserves the full
proposal → specs → design → tasks graph for legacy changes and retained
multi-PR delta programs. Do not change the schema or metadata of an existing
change just because a new workflow is available.

For a **new standalone change without change-local deltas**, select a local
schema explicitly:

- `single-butler`: proposal → tasks for eligible single-butler scope; a design
  may be added.
- `living-specs`: proposal → design → tasks when design is required.

Both no-delta schemas use the canonical files under `openspec/specs/` as the
maintained contract. When required behavior changes, update the affected
canonical requirement in the same source change. For a new change, preserve the
CLI-generated `schema` and `created` values in `.openspec.yaml`, add
`skip_specs: true`, and do not create an empty `specs/` directory. This marker
is not for existing changes, retained local-delta programs, or incomplete
specification work.

A retained multi-PR program keeps `spec-driven`, its current metadata, and all
local deltas until that program is delivered. Any change that needs change-local deltas uses `spec-driven`, whether it is
new or part of a retained program. Use `living-specs` when a design is required
and no local deltas are needed. Use `single-butler` for eligible new
single-butler work with no local deltas. Read the governing doctrine and
existing designs before selecting.

## Contributor workflow

1. Read the governing doctrine, RFC, canonical requirements and existing
designs. The [development principles](../../about/heart-and-soul/development.md#spec-before-code-rfc-before-exception)
define when a change or design is required.
2. For a new change, classify the actual scope and choose the compatible schema
explicitly with `openspec new change <name> --schema <schema>`. Add the no-delta
marker only for a new `single-butler` or `living-specs` change, preserving the
other metadata.
3. Write the proposal, design when required, and countable tasks. For a
no-delta schema, identify the affected canonical contract and state whether it
will be updated. For retained local-delta programs, preserve the exact
capability paths, requirement IDs and scenarios.
4. Implement and verify the approved behavior. Update affected canonical
requirements, runbooks and interface documentation in the same change. A
no-delta schema does not relax design, test, review, security or owner gates.
5. Before syncing retained deltas, compare same-named MODIFIED requirements in
other active changes. Apply the delivered delta to the baseline and inspect the
actual requirement-body diff. Rebuild any overlapping delta against that
refreshed baseline so a later archive cannot undo it. Follow the
[repository archive cautions](../../.claude/skills/doctrine/subskills/spec-and-spine/references/openspec-gotchas.md#two-open-changes-overwrite-each-other).
6. Validate all canonical and active OpenSpec artifacts. Sync only changes
that have local delta specs. For a no-delta change, verify its canonical update
instead of inventing a sync step. Archive the completed change and confirm its
requirements and evidence before closing the Beads work.

## Verification

```bash
openspec list
openspec validate --all --strict
make check-requirement-trace
uv run --no-sync pytest tests/contracts/test_requirement_citations.py -q --tb=short -n 0
make check-guards
```

Strict validation checks the native OpenSpec artifacts. The trace guard applies
to active local deltas, while the citation contract test checks qualified IDs
in test files against canonical requirements first and complete retained
ADDED/MODIFIED requirements second. It excludes archive history, REMOVED blocks,
mentions and incomplete changes. Review the semantic spec diff as well;
validator success does not establish approval, implementation, or runtime
health.

## Related pages

- [Project plan](project-plan.md): maintained scope and execution sources.
- [Documentation maintenance](../../about/craft-and-care/review-and-documentation.md#document-lifecycle): synthesis and retirement rules.
