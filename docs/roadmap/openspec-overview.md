# OpenSpec Workflow

> **Purpose:** Explain where requirements live and how a change reaches the maintained contract.
> **Audience:** Contributors proposing changes and reviewers checking delivery.
> **Prerequisites:** [Project knowledge map](../../about/README.md)

[Capability specs](../../openspec/specs/) are the maintained requirements.
[Active changes](../../openspec/changes/) preserve the proposal, design, tasks,
and local specs for legacy delta programs or new multi-PR programs.
[Existing archived changes](../../openspec/changes/archive/) remain historical
material. Newly completed programs are deleted after verified delivery; Git
history preserves their artifacts.
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
| `openspec/changes/archive/<dated-change>/` | Which historical artifacts were archived by the former workflow? |

## Choose a workflow

The project default remains `spec-driven`, which preserves the full
proposal → specs → design → tasks graph for legacy changes and retained
multi-PR delta programs. Do not change the schema or metadata of an existing
change just because a new workflow is available.

For a **new standalone change**, select a local
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

New multi-PR programs may use `spec-driven` with local deltas. This exception
does not extend to new standalone work. Preserve all existing legacy programs
with their resolved schemas, metadata, content and deltas unchanged until
verified completion, including legacy standalone programs. Read the governing
doctrine and existing designs before selecting.

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
refreshed baseline so a later sync cannot undo it. Follow the
[repository archive cautions](../../.claude/skills/doctrine/subskills/spec-and-spine/references/openspec-gotchas.md#two-open-changes-overwrite-each-other).
6. Validate all canonical and active OpenSpec artifacts. Sync only changes
that have local delta specs. For a no-delta change, verify its canonical update
instead of inventing a sync step. Verify the entire program is implemented and
shipped, with required source PRs merged and review/verification evidence complete.
Then use `/opsx:archive` (or `/opsx:bulk-archive`) to delete only its verified
completed directory through the normal PR workflow before closing the Beads
work. Git history preserves it. Keep unfinished or unshipped programs and all
existing archives intact; artifact readiness alone does not authorize deletion.

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
in `tests/`, `roster/*/tests/` and `frontend/src/**/*.test.*` against canonical requirements first and complete retained
ADDED/MODIFIED requirements second. It excludes archive history, REMOVED blocks,
mentions and incomplete changes. Review the semantic spec diff as well;
validator success does not establish approval, implementation, or runtime
health.

## Related pages

- [Project plan](project-plan.md): maintained scope and execution sources.
- [Documentation maintenance](../../about/craft-and-care/review-and-documentation.md#document-lifecycle): synthesis and retirement rules.
