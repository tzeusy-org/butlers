# QA Investigation Context

Load this reference only when the PR came from a QA investigation and you have
`attempt_id`, `fingerprint`, or a dashboard URL.

## Why It Matters

This skill is often used for QA-staffer-generated PRs. Preserve provenance so
review handling can be tied back to the QA investigation record.

Include these values in your notes and final handoff when available:

- `attempt_id`
- `fingerprint`
- `dashboard_base_url` or the final `/qa/investigations/<attempt_id>` URL

## Working Rules

- Keep commit messages and replies aligned to the investigation fingerprint when
  practical.
- Do not invent new provenance fields; use the values supplied by the caller.
- If a reviewer request conflicts with the QA investigation’s purpose, spec, or
  safety invariants, explain that in the `Wontfix` justification.

## Source Of Truth

Use the repo’s QA follow-up prompt contract as the mental model for this work:
[src/butlers/core/qa/prompts.py](../../../src/butlers/core/qa/prompts.py)

That code is the project-specific guidance for how QA review follow-up work is
expected to behave.
