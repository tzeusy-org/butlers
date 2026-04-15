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

## Available Files

- `scripts/review_threads.py` — list unresolved threads, post inline replies, and resolve threads deterministically through GitHub APIs.
- `scripts/validate_pr_review.py` — fail-closed validator for unresolved threads, terminal reply format, and required GitHub checks.
- `references/butlers-quality-gates.md` — repo-specific CI and local reproduction commands.

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

## Primary Workflow

### 1. Load the PR and unresolved review threads

Default to the bundled script instead of ad hoc GitHub queries:

```bash
python3 scripts/review_threads.py list --pr <pr-number>
```

Pass `--repo <owner/repo-or-url>` when not working against `Tzeusy/butlers`.

If the current agent environment exposes GitHub connector tools, they are acceptable, but the script is the default because it returns the exact thread IDs and top-level comment IDs needed for reply/resolve operations.

Minimum data to gather:

- PR title, body, URL, head branch, commits, changed files
- Unresolved non-outdated review threads with thread IDs, top-level comment IDs, authors, bodies, and URLs
- Existing top-level PR comments if they contain open asks that have not been answered

Use the raw `gh` GraphQL query only for debugging. The script already wraps it.

Treat unresolved review threads as the required closure set. Do not guess. Enumerate them explicitly before changing code, and keep the thread list around while you work so every thread gets a terminal outcome.

### 2. Remove personal information before touching review replies

The PR must contain no personal information, secrets, or environment-specific leakage.

Inspect:

- PR title and body
- Commit messages you plan to add
- Added diff hunks
- Review replies you plan to post

For `Tzeusy/butlers`, align with [src/butlers/core/healing/anonymizer.py](../../../src/butlers/core/healing/anonymizer.py). At minimum block:

- emails, phone numbers, IPs, JWTs, bearer tokens, API keys, DB URLs
- internal hostnames, absolute filesystem paths, OAuth tokens, bot tokens
- user names, message excerpts, or other end-user identifying details copied into PR text

If any leak is present, fix that first. Do not continue thread resolution on a dirty PR.

When validating PR text or reply text inside this repo, use the built-in validator when practical:

```bash
PYTHONPATH=src python3 - <<'PY'
from butlers.core.healing.anonymizer import validate_anonymized

samples = [
    "candidate title/body/reply text here",
]
for text in samples:
    ok, problems = validate_anonymized(text)
    print({"ok": ok, "problems": problems})
PY
```

Run this validator on:

- candidate PR title/body edits
- candidate inline review replies
- new commit messages

### 3. Gather QA-native context when available

If this PR came from a QA investigation, preserve that provenance in your work:

- include `attempt_id`, `fingerprint`, and dashboard URL in your own notes or handoff
- keep commit messages and replies aligned to the investigation fingerprint when practical
- use the repo’s QA prompt contract as the mental model for follow-up work:
  [src/butlers/core/qa/prompts.py](../../../src/butlers/core/qa/prompts.py)

### 4. Work each unresolved thread to closure

For every unresolved thread:

1. Decide whether the current head already satisfies the request.
2. If not, make the smallest correct code or test change on the PR branch.
3. Run targeted verification for the affected area.
4. Commit and push if you changed code.
5. Reply to the thread with one of the two allowed terminal outcomes below.

Do not leave a thread without a reply.

### 5. Allowed terminal replies

Use only these reply classes:

- `Accepted`: the request is satisfied in code. Include the commit hash.
- `Wontfix`: you are intentionally not making the requested change. Include concrete justification.

Acceptance reply template:

```text
Accepted in <full-or-short-commit-sha>.
Reason: <one sentence tying the change to the review request>.
```

Wontfix reply template:

```text
Wontfix.
Reason: <specific justification grounded in spec, correctness, security, scope, or regression risk>.
```

If more than one commit was needed, cite the commit that fully resolves the thread.

### 6. Reply and resolve

Reply inline on the review thread, not as a generic PR comment.

Default operations:

1. Post the reply against the thread’s top-level comment ID:

```bash
python3 scripts/review_threads.py reply \
  --pr <pr-number> \
  --comment-id <top-comment-id> \
  --body-file /tmp/reply.txt
```

2. Resolve the thread by GraphQL thread node ID:

```bash
python3 scripts/review_threads.py resolve --thread-id <thread-node-id>
```

If you cannot resolve the thread because of permission or tooling limits, still post the terminal reply and record the unresolved thread ID in the final handoff. Do not silently skip it.

Do not approve or merge the PR as part of this skill.

### 7. Wait for GitHub quality gates to pass

The PR is not complete until all required GitHub checks are passing.

After the last code push:

1. Run the fail-closed validator:

```bash
python3 scripts/validate_pr_review.py --pr <pr-number>
```

2. If required checks are failing, use
   [references/butlers-quality-gates.md](references/butlers-quality-gates.md)
   to reproduce and fix the exact failing gate locally.
3. If checks are still running, use:

```bash
gh pr checks <pr-number> --repo <owner/repo> --required --watch
```

4. Do not report completion while any required check is failing, pending, or missing.

The validator script is the default because it checks both review-thread closure
and required GitHub checks together. Use raw `gh pr checks` only for interactive
watching or debugging.

If the PR is blocked on an external or infrastructure failure that you cannot remediate from the branch, report that explicitly and do not claim the skill completed successfully.

## Decision Rules

- Favor minimal diffs. Do not refactor unrelated code while addressing review feedback.
- A request that is already satisfied still requires an explicit acceptance reply with a commit hash.
- Use `Wontfix` only when you can defend it concretely. "Not needed" is not enough.
- If a reviewer request conflicts with the active spec or a safety invariant, cite that in the wontfix justification.
- If a thread contains PII in the reviewer text, do not repeat it verbatim in your reply.
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

## Handoff Output

Report:

- repo and PR number reviewed
- attempt ID / fingerprint / dashboard URL when provided
- whether any PII/secrets were found and how they were removed
- each handled thread URL or ID with its final outcome
- commit hashes added during review
- final GitHub quality gate status
- any threads that could not be resolved due to permission or tooling limits
