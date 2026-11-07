# OpenSpec gotchas

Traps in `@fission-ai/openspec` 1.9.0 as used in this repo. Use the installed global `openspec`
binary (`npx openspec@1.9.0` fails `notarget`). Debug parser output with
`openspec change show <id> --json --deltas-only`. Scenario carry-over and heading renames are in
[the skill](../SKILL.md#authoring-delta-blocks) and
[renaming-a-baseline-scenario.md](renaming-a-baseline-scenario.md).

## Validation

- A bare `openspec validate --strict` prints "Nothing to validate" and exits 0. Always name a
  target: `<change-id>`, `--changes`, `--specs` or `--all` (`make check-openspec-strict` runs
  `--all`). Treat a ticked "ran openspec validate" task as unverified if it used the bare form.
- `--strict` marks a spec failed for WARNING-only issues. Filter
  `openspec validate --specs --strict --json` for `items[].issues[].level == "ERROR"` to find the
  defects that block archive.
- RFC-2119 is read from the first line of each requirement paragraph. Put SHALL or MUST before the
  first line break. A requirement with no prose at all is a hard error; prose without the keyword
  is only a warning.
- The scenario-count check accepts any `####` heading, and its error does not name the
  requirement. Find it by looking for a `### Requirement:` with no `####` child of any kind. Never
  silence it with a non-scenario heading.
- `--changes` validates delta syntax, not application. It cannot see a `## MODIFIED` block whose
  target spec file does not exist ("only ADDED requirements are allowed for new specs"), or
  prose-less requirements elsewhere in a spec the change rebuilds. Both abort only at archive.

## Authoring deltas

- A `## MODIFIED` header must match the baseline `### Requirement:` header verbatim. `[TARGET-STATE]`
  is ADDED-only; a tagged MODIFIED header creates a parallel requirement instead of modifying.
- Promoting a `[TARGET-STATE]` requirement is `## REMOVED` (old header, with `**Reason**:`) plus
  `## ADDED` (new header). `ADDED` appends to the end of the requirements section.
- Quote the real prior literal when restating a value, and name the existing rule a new
  carve-out overrides.
- After mechanically rebuilding a MODIFIED block, look for a scenario body joined directly to the
  next `##`/`###` heading. Strict validation can pass while the joined heading hides later
  requirements from the body-loss guard.
- A `pending-parent` MODIFIED block, whose capability is ADDed by an unarchived sibling change, is
  correctly labelled. Relabelling it ADDED aborts in one archive order or the other; fix the archive
  order instead.
- `spec-trace-check` IDs are bare `REQ-{spec-name}-NNN` with no suffix such as `(modified)`, and the
  ID line must sit in one contiguous paragraph.

## Two open changes overwrite each other

`openspec archive` writes the whole requirement. Two unarchived MODIFIED blocks for the same
requirement, authored against different ancestors, race: the second archive deletes what the first
added, and validate only compares scenario names. The current workflow uses reviewed
semantic sync and deletion after verified completion, not native archive. Before
syncing a delivered delta, run:

```bash
rg -l '^### Requirement: <Name>$' openspec/changes/*/specs/*/spec.md
```

If two hit, fold only delivered intent through reviewed semantic diffs. The owner
of an unshipped sibling must rebuild its delta against the refreshed baseline in
its own reviewed change; do not edit or delete that program during completion
cleanup. Re-run the grep after each sync. OpenSpec strict validation checks scenario names,
not requirement-body preservation or conflicts between stale restatements. The former local
spec-overwrite body ratchet is retired, so compare each active delta against the current canonical
requirement and review the exact union before completed-program deletion. A broader restatement can still omit behavior
while preserving scenario names.

- Direct baseline edits and `## REMOVED` blocks need explicit review; strict validation does not
  prove their intended effect.
- A "MODIFIED ... has no baseline requirement to overwrite" note is not benign: it means no
  baseline and no sibling ADD, and archive will abort.

## Completion cleanup and historical native archive

- `/opsx:archive` and `/opsx:bulk-archive` now verify whole-program implementation
  and shipment, require reviewed canonical updates and populated strict validation,
  and delete only completed change directories. Git history preserves their bodies.
  Artifact readiness, task checkboxes and strict success alone do not prove delivery.
- For a retained delta program, fold delivered requirements into `openspec/specs/`
  through reviewed semantic diffs before deleting its completed source directory.
  Keep every unfinished or unshipped program and all existing archives intact.
- Historical native `openspec archive` moves directories and applies deltas. It is
  not the repository's completion command. Reproduce any needed native behavior
  investigation in a disposable scratch copy, never on a live program. Native
  archive revalidates each rebuilt spec and can expose application defects that
  delta-only validation misses; this does not establish semantic preservation.
- Native `--skip-specs` can archive without applying deltas. Exit 0 proves no
  canonical update or delivery. Its incomplete-task gate counts checkbox tasks
  and can miss heading-only work; the former local countable-task guard is retired.
- Existing `openspec/changes/archive/` entries do not prove requirements reached
  canonical specs; some were hand moves. Inspect canonical diffs and delivery
  evidence when using history. The former archived-landing guard is retired.
- `openspec/changes/archive/**` is not a definition source for cited REQ IDs.
  `tests/contracts/test_requirement_citations.py` scans Python and frontend tests
  against canonical definitions first and complete retained ADDED/MODIFIED blocks
  second. It rejects archived definitions, prose mentions and REMOVED blocks.

## Baselines and drift

- A baseline contradicting the code is not drift while an open change carries the delta. Grep
  `openspec/changes/` before filing drift or blocking a merge.
- Direct baseline edits are common practice here. Justify them (drift correction with the diff read
  line by line), and only touch requirements no open change modifies. Never "refresh" a superseded
  requirement cosmetically; a stale guarantee should look stale.
