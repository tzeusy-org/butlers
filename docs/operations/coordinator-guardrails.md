# Coordinator Guardrails

> **Purpose:** Rules for the beads coordinator: worker and PR-state reconciliation, bd quirks, claims and graph hygiene.
> **Audience:** Coordinators and reconcilers running the beads-orchestration protocol.
> **Prerequisites:** [Decision beads](decision-beads.md).

Moved verbatim from `AGENTS.md`. Workers follow `AGENTS.md` (Beads Workflow Integration); only the coordinator runs `bd create/update/dep/close`.

- A worker can finish with its branch pushed but its bead still `in_progress`. Detect a branch ahead
  of `main` with no PR; open the PR and mark the bead `blocked` with `pr-review` and `external_ref`.
  Merge-blocker workers likewise leave beads `in_progress` after merging: close them and any related
  `pr-review` or original bead.
- Re-run PR-state normalisation after each coordinator cycle rather than assuming earlier updates
  held. Trust Dolt over any stale export mirror.
- `external_ref` is globally unique: keep `gh-pr:<n>` on the original bead and put PR metadata in
  the review bead's notes.
- To make an original bead wait on its review bead, create the review bead without
  `--deps discovered-from:<original>` (that pre-wires the reverse edge and makes a cycle), then
  `bd dep add <original> <review>`.
- Before creating a "Resolve merge blockers for PR #<n>" bead, reuse any open one for the same PR.
- After `gh pr merge --auto`, a queued PR still reads `OPEN`: confirm with
  `gh pr view <n> --json state,mergedAt` before calling it blocked or merged.
- `bd worktree create` may append paths to `.gitignore`; strip them before committing.
- bd v1.0.4 quirks:
  - `bd lint` wants Acceptance Criteria on tasks, features and bugs (the `--acceptance` field
    counts), Success Criteria on epics, and `## Steps to Reproduce` in a bug's description. A clean
    `bd lint --json` can return `total: 0`, `results: null`: keep the text receipt.
  - `bd create --graph` drops `deps` and `parent`; wire edges afterwards with
    `bd dep <blocker> --blocks <blocked>`. `bd create --parent <epic>` works (dotted child ids)
    but inherits the epic's labels (`--no-inherit-labels`) and does not gate readiness: add
    `bd dep add <child> --blocked-by <gate>`.
  - `bd create ... --json` with literal newlines in `--description` emits unparseable JSON; recover
    the id with `bd list --json`.
- `bd worktree create <path> --branch X` branches from the current HEAD: reset new worktrees to
  `origin/main`, and review worktrees to the PR head.
- `bd close` rejects a bead with an open downstream `blocks`; for an already-finished
  original/review pair pass `--force`. It also refuses a bead assigned to another actor even with
  `--actor`: unassign first with `--if-assignee <who> --assignee ''`.
- Graph states that hide or strand work (backlog pursuit run 22 found hundreds):
  - A parent holding a `blocks` edge on its own child is blocked by it, and `bd ready` then hides
    every child. A leaf blocked by its own epic deadlocks. Parents finish when their children close.
  - An open `task` or `feature` with live children shows in `bd ready` as claimable: make it an epic.
  - `blocked` status needs a real `blocks` edge (or an owner action assigned to the owner); a
    hand-set `blocked` with no edge strands the bead once everyone forgets why.
  - Owner gates: see `docs/operations/decision-beads.md` § Gating work on a decision.
- Release a dead coordinator's claim with a guard, never `--force` (bd rejects `--force` together
  with `--if-assignee`): `bd update <id> --if-assignee '<actor>' --assignee ''`, adding
  `--if-status in_progress --status open` for an abandoned in-progress bead.
- Pass your own `--actor` (or set `BEADS_ACTOR`). bd otherwise falls back to `git user.name`, and an
  agent running under the owner's git identity makes its claims look like the owner's, which every
  other agent then treats as off-limits.
- Coordinators arm their own CI watcher with a short grace period; workers go dormant during the
  ~25 minute CI run, so finish the merge and cleanup tail yourself if the PR is still open.
- The PR-reviewer worker may move a branch into its own worktree: run `git worktree list` before
  dispatching a corrector "into the worker's worktree".
- A quiet worktree is not a dead worker: `ListAgents` lists peer sessions, not in-process subagents,
  and a clean `git status` only means nothing is committed yet. Check the task list and
  `TaskStop <worker>` before touching its tree; `git reset --hard` on a live worker's worktree
  leaves two agents committing to one branch. When agents did overlap, re-verify the stopped one's
  claims and diff its abandoned artefacts before deleting them.
