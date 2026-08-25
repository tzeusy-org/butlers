## 1. Target-branch health gate

- [x] 1.1 Classify one hosted run's `status`/`conclusion` pair, treating the
  empty-string conclusion as in-flight and `cancelled` as never-a-pass.
- [x] 1.2 Distinguish a path-filter exclusion from a run that has not been
  created yet, using the merged commit's changed paths.
- [x] 1.3 Poll only workflows that can earn a per-SHA verdict on `main`,
  excluding branch-keyed `cancel-in-progress` workflows and tag-only triggers.
- [x] 1.4 Enumerate the local repo-wide guards from the tree under test rather
  than a frozen list, and fail the sweep when a guard dirties the tree.
- [x] 1.5 Fold the verdicts into one proceed / wait / halt decision whose exit
  code separates "definitively red" from "no trustworthy verdict yet".

## 2. Consumption by the merge route

- [x] 2.1 Evaluate target-branch health in `merge_pr_exact_base.py` before the
  merge request is issued, and return without merging on halt or wait.
- [x] 2.2 Provide a per-workflow acknowledgement so the fix for a red target
  branch can land while any other red still halts the batch.

## 3. Documentation

- [x] 3.1 Document the gate, its exit codes, and the between-merges invocation in
  `scripts/README.md` and `AGENTS.md`.
