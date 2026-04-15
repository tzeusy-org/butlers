---
name: butler-qa-pr-review
description: Use when working a GitHub PR for a Butler QA investigation or reviewer follow-up: unresolved review threads must be answered inline, PR text must contain no personal information or secrets, and the branch is not done until required GitHub checks are green.
compatibility: Requires Python 3, git, gh CLI authenticated to GitHub, network access, and permission to push to the PR branch and reply to review threads.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
  status: active
  last_reviewed: "2026-04-15"
---

# Butler QA PR Review

Review one PR end-to-end: scrub personal information, resolve the outstanding review feedback, leave no unresolved thread without a terminal reply, and finish only when the PR's GitHub quality gates are green.

## Load These Only When Needed

- `/heart-and-soul` — load in the dedicated alignment subagent for doctrine, scope, manifesto, and non-negotiable-rule checks on the PR delta.
- `/craft-and-care` — load in the dedicated alignment subagent for engineering-bar, verification, documentation, and change-hygiene checks on the PR delta.
- `/spec-and-spine` — load in the dedicated alignment subagent for feature-behavior, active OpenSpec change, and spec-drift checks on the PR delta.
- `scripts/review_threads.py` — list unresolved threads, post inline replies, and resolve threads deterministically through GitHub APIs.
- `scripts/validate_pr_review.py` — fail-closed validator for unresolved threads, terminal reply format, and required GitHub checks.
- `references/pii-and-replies.md` — load before drafting replies, commit messages, or PR text edits.
- `references/qa-investigation-context.md` — load only when `attempt_id`, `fingerprint`, or dashboard context is available.
- `references/butlers-quality-gates.md` — load only when required GitHub checks are failing or you need local reproduction commands.

## Inputs

- `repo`: GitHub URL or `owner/repo`. Default `https://github.com/Tzeusy/butlers` (`Tzeusy/butlers`).
- `pr_number`: required.
- `attempt_id`: optional. Include when this PR came from a QA investigation and you want handoff tied back to the `healing_attempts` row.
- `fingerprint`: optional. Include when available so replies and commits can stay correlated to the QA finding.
- `dashboard_base_url`: optional. Include when you want the review handoff to link back to `/qa/investigations/<attempt_id>`.

If the user gives only a PR number, assume `Tzeusy/butlers`.

## When To Use

- The user asks to work a specific PR and close out review feedback.
- A QA-generated PR must be checked for PII, secrets, or environment leakage.
- Review threads need either code changes plus an acceptance reply, or an explicit wontfix reply.

## Workflow

### 1. Enumerate the closure set

Use the script, not ad hoc API calls:

```bash
python3 scripts/review_threads.py list --pr <pr-number>
```

Pass `--repo <owner/repo-or-url>` when not working against `Tzeusy/butlers`.

Treat unresolved non-outdated review threads as the required closure set. Keep
the resulting thread IDs and top-level comment IDs around while you work.

### 2. Dispatch the alignment subagent

Before changing code or replying to review comments, create one dedicated
subagent for alignment analysis of the PR diff and feature delta.

That subagent must:

1. Inspect the PR diff and changed files.
2. Run `/heart-and-soul` for doctrine and manifesto alignment.
3. Run `/craft-and-care` for execution-quality, verification, and doc hygiene.
4. Run `/spec-and-spine` for normative feature-behavior and active-change alignment.
5. Return a concise report with:
   - doctrine blockers or scope concerns
   - craft-and-care blockers or missing verification/doc updates
   - spec drift, missing spec coverage, or active OpenSpec delta authority
   - explicit `go` / `no-go` recommendation for the diff
   - follow-up required: code fix, spec update, doc update, or beads issue
   - exact files/specs/docs reviewed
   - a disposition for each finding: `fix now`, `wontfix with reason`, or `unrelated baseline`
   - `no issues found` when the diff is clean across all three alignment passes

Minimum prompt shape:

```text
Review PR #<pr-number> in <repo>. Check the diff and feature delta for alignment.
Load /heart-and-soul, /craft-and-care, and /spec-and-spine.
Return only blockers, risks, and missing updates, with exact file/spec references and a disposition for each.
```

Do not close threads or call the PR complete until the alignment subagent has
reported no unaddressed blockers.

### 3. Load the relevant references

- Before changing PR text, replies, or commit messages, read
  [references/pii-and-replies.md](references/pii-and-replies.md).
- If the PR came from a QA investigation, read
  [references/qa-investigation-context.md](references/qa-investigation-context.md).
- Only if checks are failing, read
  [references/butlers-quality-gates.md](references/butlers-quality-gates.md).

### 4. Work threads to closure

For each unresolved thread:

1. Decide whether the current head already satisfies the request.
2. If not, make the smallest correct code or test change on the PR branch.
3. Run targeted verification for the affected area.
4. Commit and push if you changed code.
5. Post an inline terminal reply.
6. Resolve the thread if permissions allow.

Default reply/resolve operations:

```bash
python3 scripts/review_threads.py reply \
  --pr <pr-number> \
  --comment-id <top-comment-id> \
  --body-file /tmp/reply.txt

python3 scripts/review_threads.py resolve --thread-id <thread-node-id>
```

If you cannot resolve the thread because of permission or tooling limits, still
post the terminal reply and record the unresolved thread ID in the final
handoff. Do not silently skip it.

Treat alignment-subagent blockers the same way you treat review comments: either
fix them in the PR or record an explicit `Wontfix` justification in the final
handoff.

Do not approve or merge the PR as part of this skill.

### 5. Validate the final state

The branch is not done until both thread closure and required GitHub checks are
green.

Run the fail-closed validator:

```bash
python3 scripts/validate_pr_review.py --pr <pr-number>
```

If checks are still running, use:

```bash
gh pr checks <pr-number> --repo <owner/repo> --required --watch
```

If the validator reports failing or pending checks, then load
[references/butlers-quality-gates.md](references/butlers-quality-gates.md) and
reproduce the failing gate locally.

## Decision Rules

- Favor minimal diffs. Do not refactor unrelated code while addressing review feedback.
- The alignment subagent is mandatory for this skill; do not skip it even for small diffs.
- Passing local tests is not enough. The PR must pass the actual GitHub quality gates on the remote branch.
- Prefer the bundled scripts over ad hoc API calls; they are the deterministic path for this skill.

## Verification Checklist

Before calling the PR review complete, verify all of the following:

1. The PR text and your new replies contain no personal information or secrets.
2. Every unresolved non-outdated review thread received a terminal reply.
3. Every acceptance reply includes a commit hash.
4. Every wontfix reply includes concrete justification.
5. Any code changes were pushed to the PR branch.
6. All required GitHub quality gate checks are passing on the PR head.
7. `python3 scripts/validate_pr_review.py --pr <pr-number>` exits successfully.
8. You can enumerate the handled thread IDs and their final outcome (`Accepted` or `Wontfix`).
9. A dedicated alignment subagent completed `/heart-and-soul`, `/craft-and-care`, and `/spec-and-spine` checks on the diff and feature delta.
10. Any doctrine, craft-and-care, or spec blockers were either fixed in the PR or documented as explicit blockers with justification.

## Handoff Output

Report:

- repo and PR number reviewed
- attempt ID / fingerprint / dashboard URL when provided
- whether any PII/secrets were found and how they were removed
- each handled thread URL or ID with its final outcome
- commit hashes added during review
- alignment subagent findings, including doctrine/craft/spec blockers and their dispositions
- final GitHub quality gate status
- any threads that could not be resolved due to permission or tooling limits
