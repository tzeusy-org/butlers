---
name: butler-qa-invoke
description: Use when you need to run an end-to-end QA validation canary against the Butlers dashboard API, wait for the backend QA staffer to process it, and confirm the investigation ends as unfixable.
---

# Butler QA Invoke

Run the synthetic QA validation canary end-to-end and confirm the backend QA staffer classifies it as `unfixable`.

## When to Use

- Validating that QA dispatch/investigation recovered after a fix
- `force_patrol` is unavailable because the API is running out-of-process
- You need proof that the backend QA staffer processed a synthetic finding instead of only accepting the API request

## Preconditions

- The target dashboard API must have `QA_ALLOW_SYNTHETIC_FINDINGS=true`
- If dashboard auth is enabled, provide the API key as `--api-key` or `DASHBOARD_API_KEY`
- Run from the repo root so local helper paths resolve

## Primary Path

Use the helper script:

```bash
python3 .claude/skills/butler-qa-invoke/scripts/invoke_qa_canary.py \
  --base-url https://tzeusy.parrot-hen.ts.net/butlers-dev-api \
  --api-key "$DASHBOARD_API_KEY"
```

What the script does:

1. `POST /api/qa/dev/synthetic-findings`
2. Records the returned fingerprint
3. Polls `GET /api/qa/investigations` until the matching investigation appears
4. Succeeds only when that investigation reaches `status = "unfixable"`

Any other terminal status (`failed`, `timeout`, `anonymization_failed`, `pr_open`, `pr_merged`) is a failed validation.

The script accepts either:

- A dashboard root like `http://localhost:41200`
- A prefixed root like `https://.../butlers-dev-api`

It normalizes both to the same `/api/qa/...` calls.

## Fallback Debug Path

If the script times out or the API surface is inconclusive, load `butler-dev-debug` and verify via DB:

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "
SELECT id, fingerprint, status, created_at, updated_at, closed_at, error_detail
FROM public.healing_attempts
WHERE fingerprint = '<fingerprint>'
ORDER BY created_at DESC
LIMIT 5;
"
```

Also inspect the finding row:

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "
SELECT id, patrol_id, source_butler, dedup_reason, dispatch_queued, healing_attempt_id, created_at
FROM public.qa_findings
WHERE fingerprint = '<fingerprint>'
ORDER BY created_at DESC
LIMIT 5;
"
```

Expected steady-state success:

- A matching `healing_attempts` row exists
- Its final `status` is `unfixable`
- The queued `qa_findings` row no longer has `dispatch_queued = true`

## Notes

- The synthetic canary is intentionally framed as a non-code-bug validation case, so `unfixable` is the success outcome.
- Use `--timeout-seconds` if the patrol interval or investigation runtime is slower than the default.
- If the initial POST returns `403`, the backend gate is still off; if it returns `401`, the dashboard API key is missing or wrong.

## Verification

Before calling this skill ready:

1. Verify the helper script parses: `python3 .claude/skills/butler-qa-invoke/scripts/invoke_qa_canary.py --help`
2. Verify the skill references the correct API routes:
   `/api/qa/dev/synthetic-findings` and `/api/qa/investigations`
3. If you run the skill for real, report the fingerprint, attempt ID, final status, and whether fallback DB checks were needed
