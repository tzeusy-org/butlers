---
name: "OPSX: Bulk Archive"
description: Verify completion and delete multiple completed change directories
category: Workflow
tags: [workflow, archive, experimental, bulk]
---

The familiar `/opsx:bulk-archive` command applies `/opsx:archive` completion cleanup
to a selected batch. Completed directories are deleted; Git retains their history.
Existing archives and unfinished or unshipped programs are preserved.

**Steps**

1. **Select active changes**

   Run `openspec list --json`, show each active change and its resolved schema,
   and ask the user to select the batch. If none exist, report that and stop.
   Do not select directories under `openspec/changes/archive/`.

2. **Verify each whole program**

   Follow `/opsx:archive` steps 1–2 independently for every selection. Read its
   proposal, tasks, existing design and delivery evidence; run `openspec status
   --change "<name>" --json` and `/opsx:verify <name>`. Honor the resolved schema and
   actual design requirement. For new no-delta changes, require generated schema/
   created metadata and boolean `skip_specs: true`. Never retag legacy programs.

   Artifact readiness is not implemented or shipped completion. Confirm required
   source PRs merged, implementation review and executing verification passed, and
   required runtime evidence exists for the whole program. Exclude any incomplete
   or partially shipped multi-PR program from cleanup and preserve its entire tree.
   Do not offer to delete incomplete changes with warnings.

3. **Review canonical requirements and conflicts**

   Map each selected program's local deltas to the affected capabilities and read
   overlapping active deltas. For verified delivered programs, use `/opsx:sync` to
   fold their intent into the current canonical requirements. Review the actual
   semantic diff, including requirement/scenario bodies, IDs and source references.
   A newer timestamp does not authorize overwriting another program's behavior.

   - If only one program is shipped, fold only its delivered requirements and
     preserve every unshipped program. Record any stale sibling delta for its owner
     to rebuild against the refreshed canonical contract; never silently edit it.
   - If multiple programs are shipped, review their combined semantic result and
     resolve overlaps explicitly before deleting any of their source directories.
   - If no program is shipped, preserve them all; do not sync proposed behavior as
     delivered or delete their directories.

   For no-delta changes, verify the required canonical updates were delivered;
   do not create placeholder deltas. Validate the resulting canonical and active
   artifacts with `openspec validate --all --strict`, checking the populated terminal
   result. Strict validation does not prove semantics, review or shipment.

4. **Show eligible cleanup and confirm the selected batch**

   Show each program's completion evidence, schema, canonical disposition and
   eligibility. Excluded programs must have a concrete unfinished step. Confirm
   only the verified completed set; if none qualify, stop with all trees intact.

   ```
   | Change | Delivery evidence | Canonical result | Cleanup |
   |---|---|---|---|
   | add-oauth | merged PRs; verification passed | reviewed sync | Eligible |
   | add-jwt | second delivery PR still open | proposed delta retained | Preserve |
   ```

5. **Delete each verified completed directory**

   Follow `/opsx:archive` step 4: validate each exact active tracked path, record its
   evidence in the delivery PR/Beads handoff, then run only for eligible selections:

   ```bash
   git rm -r -- openspec/changes/<name>
   ```

   Do not invoke `openspec archive`, create archive directories, or move changes.
   Review the deletion diff against the eligible list. Record individual failures
   and preserve failed/unverified programs. Publish cleanup through the normal PR
   workflow before closing the corresponding Beads work.

6. **Report every outcome**

   ```
   ## Batch Completion Cleanup

   Deleted:
   - <completed-change>: openspec/changes/<completed-change>/; history retained in Git

   Preserved:
   - <unfinished-change>: <remaining delivery or verification step>

   Failed:
   - <change>: <exact cleanup failure, if any>

   Canonical requirements: <reviewed semantic sync/update evidence>
   Validation: <populated strict result>
   ```

**Guardrails**

- Apply all single-change completion gates to every selected program.
- Never equate artifact readiness, task counts or strict validation with shipment.
- Never fold unshipped requirements or delete incomplete programs, even on confirmation.
- Resolve semantic conflicts before deleting the corresponding delta sources.
- Preserve all existing archives and all nonselected changes.
