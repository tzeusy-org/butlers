# Renaming a scenario that is already in the baseline

Verified against `@fission-ai/openspec` 1.9.0 (the `openspec` CLI on this
machine; note the npm package is scoped, so `npx openspec@1.9.0` does not
resolve). Every claim below was reproduced in a scratch copy of `openspec/`;
the exact error strings are quoted from real runs.

## The rule that causes the problem

`findMissingCurrentScenarios` (`dist/core/parsers/requirement-blocks.js`,
shared by `validate` and `archive` via `dist/core/validation/validator.js`)
compares scenario **names** and never compares bodies. A
`## MODIFIED Requirements` block replaces the whole requirement, so it must
reproduce every scenario name the baseline still carries, verbatim:

```
✗ [ERROR] <spec>/spec.md: MODIFIED "<Requirement>" omits scenario(s) the
  current spec still has: "<name>". Copy them into the MODIFIED block (a
  MODIFIED requirement replaces the whole block, so archive refuses to drop
  them).
```

So a delta cannot correct a scenario heading. It has to carry the stale name,
and archive writes that same stale name back into the baseline. `RENAMED`
does not help: the validator walks the `renamedFrom` chain and still runs the
check against the old block. `REMOVED` plus `ADDED` of the same requirement
name inside one change is refused outright:

```
✗ [ERROR] <spec>/spec.md: Requirement present in both ADDED and REMOVED: "<n>"
```

## Prevention comes first: name the guarantee, not the mechanism

**Write scenario headings that describe what the system guarantees, not the
machinery that delivers it.** Mechanisms get replaced; headings cannot follow
them without the procedure below.

- Bad: `An attention-ledger push notifies the owner once per breach window`
- Good: `The owner is notified exactly once per breach window`

The bad heading pinned an attention-ledger write into the spec. When the
mechanism became a durable outbox episode, the heading became a lie that no
delta could correct.

## The procedure, when a heading is already wrong

Two changes, archived in order. Both may exist in the tree at the same time
and may ship in the same commit or PR; only the **archive order** is
constrained.

1. **Step 1, retire.** A change whose only delta is
   `## REMOVED Requirements` naming the requirement, with a `**Reason**:`
   saying the removal is a rename step and not a retirement of the behaviour.
2. **Step 2, restore.** A change whose only delta is
   `## ADDED Requirements` carrying the requirement back byte for byte, with
   the one heading corrected.
3. **Rebuild every unarchived `## MODIFIED` block** for that requirement
   against the refreshed baseline. They now omit a name the baseline carries
   and fail `openspec validate --strict` until repointed.
4. **Repoint the frozen ratchet entries.** `scripts/spec-overwrite-baseline.json`
   identifies a frozen loss by `(kind, scenario, digest)`, so a scenario
   rename orphans every entry recorded under the old name and
   `scripts/check_spec_overwrites.py` goes red. Edit the `scenario` field on
   exactly those records. Do not run `--update-baseline`, which re-freezes the
   whole repo and would swallow unrelated regressions.

Worked example in the archive:
`openspec/changes/archive/2026-08-23-rename-fleet-halt-scenario-heading-step-1-retire`
and `...-step-2-restore`.

## Costs, all verified

- **Never leave the gap open.** Between step 1's archive and step 2's archive
  the baseline has no such requirement. In that window any unarchived
  `## MODIFIED` block naming it passes `openspec validate --strict` and then
  hard-aborts at archive with
  `<spec> MODIFIED failed for header "### Requirement: <n>" - not found`.
  Validate does not warn. Archive the pair back to back.
- **Archiving out of order is safe but useless.** Step 2 first aborts with
  `<spec> ADDED failed for header "### Requirement: <n>" - already exists` and
  changes no files.
- **The requirement moves.** `ADDED` appends, so the restored block lands at
  the end of the requirements section rather than its original position. The
  guards key on names, not positions, so nothing breaks, but the diff is
  large and the file's reading order changes.
- **Every open change against that requirement pays.** Each one has to be
  rebuilt, which is real coordination cost when several changes are in flight
  against the same spec. This, not the mechanism, is why prevention is the
  primary advice.
- **The body-level guard is blind to the whole procedure.**
  `scripts/check_spec_overwrites.py` reads `## MODIFIED` sections only, so a
  step-1 `REMOVED` that deletes an entire baseline requirement is invisible to
  it. Nothing checks that step 2 restores what step 1 removed. That discipline
  is yours: diff the restored block against the removed one and confirm the
  heading is the only difference.
- **If it is the last requirement in the spec**, step 1 deletes the capability's
  whole `spec.md`. Check before removing.

## Do not hand-edit the baseline instead

Editing `openspec/specs/**/spec.md` directly is the failure mode
`spec-overwrite-guard` exists to catch, and it collides with any open change at
archive time. Both steps above go through `openspec archive`, which is what
makes the baseline edit legitimate.
