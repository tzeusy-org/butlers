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
added, and validate only compares scenario names. Before archiving, run:

```bash
rg -l '^### Requirement: <Name>$' openspec/changes/*/specs/*/spec.md
```

If two hit, archive one, rebuild the other from the refreshed baseline, and diff to prove nothing
else moved. Re-run the grep after each archive. `make check-spec-overwrites` compares bodies against
a digest-keyed ratchet (`scripts/spec-overwrite-baseline.json`). It catches deletions, not
contradictions: a broader restatement still reads as preserved.

- The guard reads `## MODIFIED` blocks in `openspec/changes/` only. A green run says nothing about a
  direct baseline edit or a `## REMOVED` block. Reviewers diff those, and any remove/add pair,
  themselves.
- Its "MODIFIED ... has no baseline requirement to overwrite" note is not benign: it means no
  baseline and no sibling ADD, and archive will abort.
- A ratchet "healing" commit is proven only by rerunning `collect()` from
  `check_spec_overwrites.py` on the commit and its parent and diffing per record. An entry can
  vanish because its clause was already gone.
- A rename orphans ratchet entries keyed on the old scenario name. Edit those records' `scenario`
  field by hand; `--update-baseline` re-freezes the whole repo.

## Archive

- Spec-amendment work ends with sync plus archive: apply the deltas to `openspec/specs/`, tick
  `tasks.md`, and move the change to `openspec/changes/archive/YYYY-MM-DD-<name>`. A merged but
  unapplied change leaves the old MUSTs binding for the next reader.
- `openspec archive` mutates the tree. Reproduce archive failures in a scratch copy of `openspec/`,
  never in a worktree. A failed archive writes nothing, so one scratch copy can probe many specs.
- Archive revalidates each rebuilt spec in full and stops at the first failure, so fixing one spec
  can reveal the next.
- `--skip-specs` archives without applying deltas. It exits 0 and proves nothing.
- The incomplete-task gate counts `- [ ]` checkboxes only. A heading-style `tasks.md` reports
  "No tasks" and archives unprompted; `make check-countable-tasks` rejects that shape.
- A change under `openspec/changes/archive/` is not evidence its requirements reached
  `openspec/specs/`, because many archives were hand `git mv`s.
  `scripts/check_archived_requirements_landed.py` checks this per requirement. A capability file
  existing proves nothing; an empty `git log -- openspec/specs/<capability>` means it never landed.
- `openspec/changes/archive/**` is not a definition source for cited REQ ids; only
  `openspec/specs/**` and unarchived changes are (`scripts/check_cited_requirements_resolve.py`).

## Baselines and drift

- A baseline contradicting the code is not drift while an open change carries the delta. Grep
  `openspec/changes/` before filing drift or blocking a merge.
- Direct baseline edits are common practice here. Justify them (drift correction with the diff read
  line by line), and only touch requirements no open change modifies. Never "refresh" a superseded
  requirement cosmetically; a stale guarantee should look stale.
