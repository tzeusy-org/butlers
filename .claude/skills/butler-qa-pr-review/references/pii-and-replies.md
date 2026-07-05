# PII And Reply Rules

Load this reference before editing PR text, drafting inline replies, or writing
commit messages.

## No-PII Rule

For `Tzeusy/butlers`, align with
[src/butlers/core/healing/anonymizer.py](../../../src/butlers/core/healing/anonymizer.py).

Block at minimum:

- emails
- phone numbers
- IP addresses
- JWTs and bearer tokens
- API keys and DB URLs
- internal hostnames
- absolute filesystem paths
- OAuth tokens and bot tokens
- user names, user message excerpts, or other end-user identifying details

If reviewer text contains PII, do not quote it back verbatim in the reply.

## No Tool-Session Links (bu-mr5t5)

Separately from PII: never leave a tool-session URL or attribution footer
(e.g. a Claude Code session link, an OpenAI Codex cloud task link) in the PR
body, a commit message, or a reply — see
[scripts/session_link_guard.py](../../../../scripts/session_link_guard.py)
for the exact patterns and the `session-link-guard` CI job that enforces
this on every PR. If your CLI tooling appends this kind of footer to commits
by default, strip it before pushing rather than relying on catching it in a
later scrub pass — a PR that merges before the scrub completes leaves the
link permanently baked into `main`'s history.

## Validation Command

Use the built-in validator on candidate PR text, reply text, and new commit
messages:

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

## Allowed Terminal Reply Classes

Every unresolved non-outdated review thread must end in exactly one of these:

- `Accepted`
- `Wontfix`

Acceptance template:

```text
Accepted in <full-or-short-commit-sha>.
Reason: <one sentence tying the change to the review request>.
```

Wontfix template:

```text
Wontfix.
Reason: <specific justification grounded in spec, correctness, security, scope, or regression risk>.
```

Rules:

- If the request is already satisfied, still post an `Accepted` reply with a
  commit hash.
- `Wontfix` requires concrete justification. “Not needed” is not enough.
- Prefer minimal diffs; do not refactor unrelated code.
