---
name: "OPSX: Archive"
description: Verify completion and delete a completed change directory
category: Workflow
tags: [workflow, archive, experimental]
---

The familiar `/opsx:archive` command now verifies completion and deletes the completed
change directory. Git history preserves its proposal, design, tasks and metadata.
Existing `openspec/changes/archive/` entries remain untouched.

**Input**: A change name, for example `/opsx:archive add-auth`. If omitted and the
intended change is ambiguous, run `openspec list --json` and ask the user to select
an active change. Never select an existing archive for deletion.

**Steps**

1. **Read the complete delivery program**

   Recheck `about/heart-and-soul/development.md`, the proposal, tasks, existing design
   and resolved schema. Run `openspec status --change "<name>" --json` to inspect
   artifacts. An eligible `single-butler` change has proposal and tasks with optional
   design; `living-specs` requires design. Cross-butler contracts, shared schemas
   and security boundaries cannot omit design.

   For a new no-delta change, verify its generated `schema` and `created` metadata
   and boolean `skip_specs: true`. Stop if they are missing or malformed. Never
   add that marker or retag a legacy change or retained delta program.

2. **Establish implemented and shipped completion**

   Read the tasks and delivery evidence, including the source PRs, implementation
   review, executing verification and any required runtime evidence. Confirm all
   required delivery PRs have actually merged and the whole program has shipped.
   Run `/opsx:verify <name>` against the actual implementation and resolve any
   completion gaps. Artifact readiness and checked boxes alone do not prove delivery.

   If artifacts, tasks, implementation, review or shipment are incomplete, stop and
   preserve the entire directory. A partially shipped multi-PR program remains
   active. User confirmation cannot turn incomplete work into completed work.

3. **Verify the maintained requirements**

   If local deltas exist, compare every delivered ADDED/MODIFIED/REMOVED/RENAMED
   requirement with the current canonical spec. Inspect overlapping active deltas
   and fold the delivered intent through `/opsx:sync`, with a reviewed semantic diff
   that preserves unrelated requirements, scenario bodies, IDs and source references.
   Recheck the actual canonical result before deletion; never skip a required sync.
   Do not fold or delete any unshipped sibling program.

   For a new `single-butler` or `living-specs` change, verify its required canonical
   updates were delivered. Do not invent local deltas or infer this path for a legacy
   program. Preserve existing designs, approval, implementation review and evidence.

   Run `openspec validate --all --strict` on the resulting tree. Read its populated
   terminal result. Strict validation checks structure and scenario shape; review
   supplies the semantic preservation check and delivery evidence supplies completion.

4. **Delete only the verified completed change**

   Confirm the selected path is exactly `openspec/changes/<name>`, an active tracked
   change directory, and not `archive` or a path inside it. Record the completion,
   semantic-diff and verification evidence in the delivery PR/Beads handoff before
   removing the directory:

   ```bash
   git rm -r -- openspec/changes/<name>
   ```

   Do not invoke `openspec archive`, create a dated archive, or move the directory.
   Review the deletion diff to ensure it contains only the verified completed change.
   Publish the cleanup through the normal PR workflow before closing its Beads work.

5. **Report the result**

   ```
   ## Completion Cleanup

   Change: <change-name>
   Schema: <schema-name>
   Deleted: openspec/changes/<name>/ (history retained in Git)
   Delivery: <merged source PRs and verification evidence>
   Specs: <reviewed canonical sync, or verified same-change canonical update>
   Validation: <populated strict result and semantic-diff review>
   ```

   If completion cannot be verified, report the specific gap and that the active
   directory was preserved. Existing archives and all unfinished programs stay intact.
