---
name: investigation-notes
description: >
  Emit a structured investigation_notes.json artifact at terminal state.
  Load when you are an investigation agent finishing a fix or unfixable verdict.
  The dispatcher reads this file before worktree teardown and persists it into
  qa_findings.structured_evidence.investigation_notes.
---

# Investigation Notes Emission

When your investigation reaches a **terminal step** — either because you have
produced a commit ready for a PR, or because you have determined the issue is
unfixable — you MUST write a JSON artifact at `./.qa/investigation_notes.json`
inside your worktree before signalling completion.

## Required File Location

```
./.qa/investigation_notes.json
```

Create the `.qa/` directory if it does not already exist. The path is relative
to the root of your worktree.

## JSON Schema

The file must contain a single JSON object that conforms to the
`InvestigationNotes` model defined in
`src/butlers/core/qa/notes.py`. The authoritative field list:

```json
{
  "schema_version": 2,
  "headline": "<one-line summary of the root cause, anonymized>",
  "hypothesis": "<one or two sentences stating the root-cause claim>",
  "blurb_segments": [
    { "claim": "c1", "text": "<prose sentence anchored to claim c1>" },
    " ",
    { "claim": "c2", "text": "<prose sentence anchored to claim c2>" },
    "<additional free text>"
  ],
  "claims": {
    "c1": {
      "evidence_ids": ["e1"],
      "note": "<one sentence rationale for this claim>"
    },
    "c2": {
      "evidence_ids": ["e1", "e2"],
      "note": "<one sentence rationale for this claim>"
    }
  },
  "evidence_lines": [
    {
      "id": "e1",
      "ts": "HH:MM:SS",
      "lvl": "ERROR",
      "butler": "<butler-name>",
      "ref": "E1",
      "msg": "<verbatim span of evidence bundle item E1's anonymized text>"
    }
  ],
  "counter_evidence": [
    {
      "hypothesis": "<alternative root cause you considered>",
      "verdict": "rejected",
      "reason": "<why you ruled it out>"
    }
  ],
  "why_this_fix": "<one sentence explaining why this code change resolves the root cause>",
  "diff_snapshot": []
}
```

### Field contract

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `Literal[1, 2]` | Always `2`. |
| `headline` | `str` | One line, anonymized. Renders as the case title in the dossier rail. |
| `hypothesis` | `str` | Root-cause claim in 1–2 sentences. |
| `blurb_segments` | `list[str \| {claim, text}]` | Mixed list: plain strings for free text, objects with `claim` key to anchor prose to a claim id. |
| `claims` | `dict[str, {evidence_ids, note}]` | Keys are claim ids referenced in `blurb_segments`. `evidence_ids` is a list of ids from `evidence_lines`. |
| `evidence_lines` | `list[{id, ts, lvl, butler, ref, msg}]` | Quotes from the evidence bundle. `ref` is required: the id (`E1`...) of the bundle item quoted. `msg` must be a verbatim span of that item's anonymized text (at least 16 characters, or the whole item), never a raw log line. The dispatcher checks it against its own recorded copy; a line that does not match is stored `unsupported`, and a claim citing only unsupported lines is shown as an unsupported claim. |
| `counter_evidence` | `list[{hypothesis, verdict, reason}]` | `verdict` must be one of `"rejected"`, `"accepted"`, `"pending"`. |
| `why_this_fix` | `str` | One sentence. Renders below the diff preview. |
| `diff_snapshot` | `list[{kind, text}]` | Leave as `[]`. The dispatcher overwrites this field by running `git diff HEAD~1..HEAD` after your commit. |

### Tolerant parsing

The dispatcher uses a best-effort parser: if some fields are malformed or
missing, it recovers what it can. An empty object `{}` is preferable to no
file at all. The dispatcher never fails the investigation because of a
malformed artifact — it increments `qa_investigation_notes_parse_total` with
`status="partial"` or `"failed"` but preserves your terminal state.

## Emission Steps

1. Create the `.qa/` directory:
   ```bash
   mkdir -p .qa
   ```

2. Write the JSON object to `.qa/investigation_notes.json`. Use
   `jq -n` or a direct file write — plain JSON, no trailing newline required.

3. Verify the file exists and is valid JSON before signalling completion:
   ```bash
   python3 -c "import json, sys; json.load(open('.qa/investigation_notes.json'))" \
     && echo "valid" || echo "invalid — fix before continuing"
   ```

4. Continue to your commit step or unfixable signal. The dispatcher reads
   the file after the runtime exits; you do not need to do anything else with it.

## Anonymization Rules

- `headline`, `hypothesis`, `blurb_segments`, `claims`, `counter_evidence`,
  and `why_this_fix` are narrative fields you author. They are shown to the
  operator and may eventually appear in external PR descriptions.
  **Do not include PII, credentials, or raw personal identifiers in these fields.**
- `evidence_lines[].msg` quotes the evidence bundle, which is already
  anonymized. Copy the span verbatim; never paste a raw log line from anywhere
  else, and never edit the quote.
- `diff_snapshot` is populated by the dispatcher; leave it as `[]`.
