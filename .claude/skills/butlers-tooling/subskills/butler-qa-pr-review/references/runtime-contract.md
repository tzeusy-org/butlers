# Runtime Contract

Use this reference when you need the execution boundary for Butler QA PR
review work.

## Required Context

- `repo` must identify the Butler repository to review.
- `pr_number` must point at the PR under review.
- The worker must have authenticated `gh`, `git`, Python 3, network access, and
  permission to push to the PR branch.

## Execution Boundary

- Do the work from a dedicated isolated git worktree for the PR branch.
- Do not mutate the PR from a shared checkout or from `main`/`master`.
- If the branch cannot be isolated cleanly, stop and report the blocker.

## Helper Contract

- Use `scripts/review_threads.py list` to enumerate threads with full
  pagination.
- Use `scripts/review_threads.py reply` with a stable `--dedupe-key` so retry
  attempts do not duplicate replies.
- Use `scripts/review_threads.py resolve` for idempotent thread closure.
- Use `scripts/validate_pr_review.py` as the fail-closed completion check.

## Stop Conditions

- Thread enumeration is incomplete or ambiguous.
- Reply posting cannot be made retry-safe.
- Required GitHub checks cannot be verified.
- The PR still has unresolved non-outdated threads.
