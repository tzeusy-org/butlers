---
name: butler-qa-pr-review
description: Review a specific GitHub pull request for Butler QA follow-up. Accepts a repository and PR number, defaulting the repository to https://github.com/Tzeusy/butlers when omitted. Ensures the PR contains no personal information or secrets, addresses every outstanding unresolved review thread, and replies to each thread with either an acceptance response that includes the commit hash or a wontfix response with concrete justification.
---

# Butler QA PR Review

Review one PR end-to-end: scrub personal information, resolve the outstanding review feedback, and leave no unresolved thread without a terminal reply.

## Inputs

- `repo`: GitHub URL or `owner/repo`. Default `https://github.com/Tzeusy/butlers` (`Tzeusy/butlers`).
- `pr_number`: required.

If the user gives only a PR number, assume `Tzeusy/butlers`.

## When To Use

- The user asks to work a specific PR and close out review feedback.
- A QA-generated PR must be checked for PII, secrets, or environment leakage.
- Review threads need either code changes plus an acceptance reply, or an explicit wontfix reply.

## Primary Workflow

### 1. Load the PR and unresolved review threads

Prefer GitHub connector tools when available. Fallback to `gh`.

Minimum data to gather:

- PR title, body, URL, head branch, commits, changed files
- Unresolved review threads with comment IDs, authors, bodies, and URLs
- Existing top-level PR comments if they contain open asks that have not been answered

CLI fallback:

```bash
REPO_INPUT="${REPO:-https://github.com/Tzeusy/butlers}"
REPO="${REPO_INPUT#https://github.com/}"
REPO="${REPO%.git}"
PR_NUMBER="<pr-number>"
OWNER="${REPO%/*}"
NAME="${REPO#*/}"

gh pr view "$PR_NUMBER" --repo "$REPO" \
  --json title,body,url,headRefName,baseRefName,commits,files,reviews

gh api graphql \
  -F owner="$OWNER" \
  -F name="$NAME" \
  -F number="$PR_NUMBER" \
  -f query='
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          comments(first: 20) {
            nodes {
              databaseId
              body
              url
              publishedAt
              author { login }
              commit { oid }
            }
          }
        }
      }
    }
  }
}'
```

Treat unresolved review threads as the required closure set. Do not guess. Enumerate them explicitly before changing code.

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

### 3. Work each unresolved thread to closure

For every unresolved thread:

1. Decide whether the current head already satisfies the request.
2. If not, make the smallest correct code or test change on the PR branch.
3. Run targeted verification for the affected area.
4. Commit and push if you changed code.
5. Reply to the thread with one of the two allowed terminal outcomes below.

Do not leave a thread without a reply.

### 4. Allowed terminal replies

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

### 5. Reply and resolve

Prefer replying inline on the review thread. If you have permission, resolve the thread after replying. If tooling cannot mark the thread resolved, still post the terminal reply and report the remaining unresolved-thread IDs.

Use GitHub connector reply tools when available. Fallback:

- inline review thread: `gh api` or repository automation that posts to the review comment thread
- top-level PR comment: `gh pr comment`

Do not approve or merge the PR as part of this skill.

## Decision Rules

- Favor minimal diffs. Do not refactor unrelated code while addressing review feedback.
- A request that is already satisfied still requires an explicit acceptance reply with a commit hash.
- Use `Wontfix` only when you can defend it concretely. "Not needed" is not enough.
- If a reviewer request conflicts with the active spec or a safety invariant, cite that in the wontfix justification.
- If a thread contains PII in the reviewer text, do not repeat it verbatim in your reply.

## Verification Checklist

Before calling the PR review complete, verify all of the following:

1. The PR text and your new replies contain no personal information or secrets.
2. Every unresolved review thread received a terminal reply.
3. Every acceptance reply includes a commit hash.
4. Every wontfix reply includes concrete justification.
5. Any code changes were pushed to the PR branch.
6. You can enumerate the handled thread IDs and their final outcome (`Accepted` or `Wontfix`).

## Handoff Output

Report:

- repo and PR number reviewed
- whether any PII/secrets were found and how they were removed
- each handled thread URL or ID with its final outcome
- commit hashes added during review
- any threads that could not be resolved due to permission or tooling limits
