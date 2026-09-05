# Dispatch Kit

Portable execution artifacts for the Dispatch design language. The normative
language itself is the spec: `openspec/specs/dashboard-design-language/spec.md`.
This kit is the how — JSX patterns, page recipes, paste-ready tokens, and the
pre-merge checklist. Lives inside the `butlers-redesign-prompt` skill
(`references/dispatch-kit/`); hand to a fresh Claude session by pasting
`KICKOFF_PROMPT.md` and pointing at this folder.

## Files

| File                  | Purpose |
|-----------------------|---------|
| `KICKOFF_PROMPT.md`   | Paste at top of a fresh redesign chat |
| `openspec/specs/dashboard-design-language/spec.md` (repo) | The philosophy — read this first (canonical Dispatch spec) |
| `PATTERNS.md`         | Concrete JSX snippets for every primitive |
| `RECIPES.md`          | One recipe per high-priority page |
| `tokens.css`          | Paste-ready CSS variables, both themes |
| `CHECKLIST.md`        | 12-item review list, run before merging |
| `IMPLEMENTATION.md`   | Overview-page-specific build recipe |

## Workflow for a new page redesign

1. Open a fresh Claude chat in your repo.
2. Paste `KICKOFF_PROMPT.md`, replacing `<PAGE_NAME>`.
3. Let Claude read the four docs and the existing page, then
   propose a layout in prose.
4. Review the proposal. Push back. Approve.
5. Claude writes the code.
6. You run `CHECKLIST.md` against the diff.
7. If all 12 pass, merge.

That's the whole loop.
