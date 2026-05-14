# Investigation Notes Artifact -- Gen-1 Reconciliation

**Issue:** bu-cd8cb
**Date:** 2026-05-15
**Parent sub-epic:** bu-6oov3 -- Implement Investigation Notes Artifact contract end-to-end
**Source:** `openspec/changes/redesign-qa-dossier/specs/qa-investigation-dispatch/spec.md`
**Design context:** `openspec/changes/redesign-qa-dossier/design.md` D2-D3

## Scope

This reconciliation covers the Investigation Notes Artifact requirement plus D2/D3:

- D2: `InvestigationNotes` JSONB shape, strict model, tolerant parser, parse metric.
- D3: terminal agent writes `./.qa/investigation_notes.json`; dispatcher reads before teardown and persists.
- G4A: model/parser.
- G4B: prompt/update and `considered`/`concluded` journal emission.
- G4C: dispatcher read/parse/persist path and parse counter.

The spec file currently defines three scenarios under "Investigation Notes Artifact";
G4B also adds the D3-derived journal-emission acceptance for `considered` and `concluded`.

## Scenario Checklist

| Scenario | Status | Implementation | Tests / validation |
|---|---|---|---|
| Agent emits investigation notes JSON at terminal state | **Partial** | `src/butlers/core/qa/prompts.py` instructs terminal agents to write `./.qa/investigation_notes.json` with every required field and a complete example. The prompt is included by `build_investigation_prompt()`. | `tests/core/qa/test_prompts.py::test_prompt_requires_structured_investigation_notes_json` verifies the path, field list, example, and JSON-only rule. **Gap:** Claude-specific structured-output mode is not implemented; see below. |
| Dispatcher reads and persists notes before worktree teardown | **Covered** | `src/butlers/core/qa/dispatch.py::_persist_notes_and_remove_worktree()` calls `_persist_investigation_notes()` before `remove_healing_worktree()`. `_persist_investigation_notes()` reads `.qa/investigation_notes.json`, calls `parse_investigation_notes()`, updates `public.qa_findings.structured_evidence` with `jsonb_set(..., '{investigation_notes}', ...)`, records `qa_investigation_notes_parse_total{status}`, and leaves terminal state untouched on missing/failed notes. | `tests/core/qa/test_investigation_notes.py::{test_dispatcher_persists_ok_notes,test_dispatcher_handles_missing_notes_file,test_dispatcher_persists_partial_notes}`. |
| Notes fields are agent-authored and not re-anonymized internally | **Covered** | `_persist_investigation_notes()` persists `notes.model_dump(mode="json")` directly and does not call `anonymize()` for `evidence_lines`. GitHub-bound PR title/body remains separate through `_load_investigation_notes()` and PR anonymization paths. | `tests/core/qa/test_investigation_notes.py::test_evidence_lines_not_reanonymized`. |
| D3/G4B journal events from parsed notes | **Covered** | `src/butlers/core/qa/dispatch.py::_emit_investigation_notes_journal_events()` emits one `considered` event per `counter_evidence` item and exactly one `concluded` event for parsed notes. `_persist_investigation_notes()` calls it only when parse returns notes. | `tests/core/qa/test_investigation_notes.py::{test_considered_emitted_per_counter_evidence,test_concluded_emitted_once_on_ok_parse,test_no_considered_or_concluded_on_failed_parse}`. |

## Model And Parser Checklist

| Requirement | Status | Evidence |
|---|---|---|
| `InvestigationNotes` fields match D2: `schema_version`, `headline`, `hypothesis`, `blurb_segments`, `claims`, `evidence_lines`, `counter_evidence`, `why_this_fix`, `diff_snapshot` | **Covered** | `src/butlers/core/qa/notes.py` defines `InvestigationNotes`, `BlurbSegment`, `Claim`, `EvidenceLine`, `CounterEvidenceItem`, and `DiffLine` with the D2 field set. |
| Strict validation succeeds on complete schema-conformant JSON | **Covered** | `parse_investigation_notes()` first calls `InvestigationNotes.model_validate_json(raw)`. Covered by `test_full_parse`. |
| Malformed but valid JSON falls back to field-level best-effort extraction | **Covered** | `parse_investigation_notes()` decodes JSON, validates each top-level field through a `TypeAdapter`, fills missing defaults, and returns `status="partial"` when at least one field recovers. Covered by missing-field, wrong-type, and schema-version tests. |
| Invalid JSON or no recoverable fields returns `(None, "failed")` without raising | **Covered** | `parse_investigation_notes()` catches JSON and validation failures. Covered by `test_failed_parse_invalid_json`. |

## Gap

### Claude structured-output mode is not implemented

**Spec / task source:**

- `qa-investigation-dispatch/spec.md`: if the agent supports structured output (Claude), it uses structured-output mode to produce the JSON.
- `design.md` D3: Claude-specific prompt instructs the agent to use structured output for the JSON file's content.
- `tasks.md` 4.5: when runtime is Claude, enable structured-output mode; other runtimes use only JSON-shape instructions.

**Current implementation:**

- `src/butlers/core/qa/prompts.py` explicitly documents that the shared `RuntimeAdapter.invoke()` contract does not expose a structured-output/schema parameter, so the implementation keeps only a portable file contract.
- `src/butlers/core/runtimes/base.py::RuntimeAdapter.invoke()` has no schema/structured-output parameter.
- `src/butlers/core/runtimes/claude_code.py::ClaudeCodeAdapter.invoke()` runs the Claude CLI with `--output-format stream-json`, which structures the session event stream, but does not constrain the artifact file content to `InvestigationNotes`.

**Impact:** The artifact still has a robust prompt contract and tolerant parser, but the Claude-specific structured-output SHALL is not satisfied. This should be tracked as a follow-up under G4; implementing it likely requires an adapter/spawner interface change and is outside this reconciliation bead's small-write scope.

**Recommended gap bead:** P2 child under bu-6oov3, blocked by bu-cd8cb, titled "Enable Claude structured-output mode for QA investigation notes artifact". Acceptance should require either a real Claude structured-output/schema hook for `InvestigationNotes` or an explicit OpenSpec/design amendment if the CLI/runtime contract cannot support artifact-file structured output.

## Verdict

**Not close-ready.** Three implementation scenarios and the G4B journal-emission path are covered by focused tests, but the Claude structured-output mode requirement remains partially implemented. No feature code was changed during this reconciliation.
