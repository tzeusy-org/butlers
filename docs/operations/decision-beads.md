# Decision Beads

A decision bead asks the owner to choose among options, not just to do a task. The convention uses
only native `bd` fields, so local engineering tools and owner sessions can read it
without a bespoke format. The former product bridge is retired under owner decision bu-g2zuv2 = A.

## Convention

1. **Label.** Add the `decision` label (`bd create ... --label decision`, or
   `bd update <id> --labels decision`). Consumers discover decisions with
   `bd list --label decision`. Legacy title markers ("DECISION REQUIRED (owner)",
   "[OWNER-GATED]", "OWNER:", "ARCHITECTURAL DECISION") are context only, never a classifier.
2. **Structured options.** Set `metadata.decision.options` (a non-empty list of distinct, non-blank
   strings) and `metadata.decision.default` (exactly one of those options, applied if the owner has
   not answered by the deadline). `metadata.decision` is the single structured source of truth;
   the description does not need to restate the options.
3. **Deadline.** Set bd's native `due_at` with `--due` (`--due 2026-07-25`, `--due +2w`). Do not add
   a second, text-only deadline.
4. **Description.** Free-form prose: context, rationale, links.

```bash
bd create "DECISION REQUIRED (owner): re-enable the api-haiku lane?" \
  --type task --label decision --due 2026-07-25 \
  --metadata '{"decision": {"options": ["A: re-enable now", "B: keep disabled", "C: descope"], "default": "B: keep disabled"}}' \
  --description "Context: ..."
```

Do not use bd's built-in `issue_type: decision`. It is an ADR-style template for a decision already
made (`--validate` demands `## Decision` / `## Rationale` / `## Alternatives Considered`). Decision
beads keep their normal type, usually `task`, and carry the `decision` label.

### Gating work on a decision

- Assign the decision bead to the owner and gate each dependent with a real edge:
  `bd dep add <gated-bead> <decision-bead>`. An `owner-gated` label alone gates nothing: an open,
  unassigned bead with that label still appears in `bd ready` and the fleet can claim it.
- Do not put the `decision` label on `[HOLD]` release gates or on owner action and authorization
  beads ("run the live drill", "authorize the cutover"). Closing a decision bead
  after an owner choice unblocks its dependents, including a "not yet" answer.
  Keep those as owner-assigned tasks; an owner action with no blocker uses manual `blocked` status.
- Write options that make sense on their own ("B: keep disabled"), never bare letters: the owner
  may review options without the description.
- Before filing, check that the question is still open: `git log origin/main --grep <id>`, merged
  spec text, and recorded owner answers often settle it already.

## Linter

`scripts/lint_decision_beads.py` checks every `decision`-labelled bead for the four properties:

```bash
make lint-decision-beads          # labelled beads only
make lint-decision-beads-strict   # also flags open, non-epic beads with a legacy title marker but no label
python3 scripts/lint_decision_beads.py [issue-id...]
```

It reads live through `bd`, or from an offline `--issues-json-file` snapshot (a JSON array or
object, or the newline-delimited `bd export` format). It is a local check, not part of `make check`
or CI: GitHub Actions cannot reach the Dolt server behind `bd`.

The default mode is vacuous against a queue that has not adopted the label: it finds nothing and
reports clean. The strict mode (`--check-unlabeled-markers`) closes that gap.

### Telegram renderability warnings

The linter also emits advisory `warnings` for an option over512 characters or an
estimated fixed Telegram prompt over4096 characters. These retained standalone
format checks do not activate a sender or product digest. Warnings do not change
`ok`, `violations` or exit status: warning-only results exit0, convention
violations exit1, and input acquisition failures exit2.

The estimate preserves the historical fixed template: title, bead id, age,
optional UTC due date, numbered options, literal/newline characters, repeated
default and instruction. Character counts use Python string length. Missing
date context uses the conservative0h age and omits an unusable due suffix;
absence of a warning does not certify delivery. Parse, label, default, date
requirements and the16-option limit remain unchanged.

## Backlog graph hygiene

`scripts/lint_backlog_graph.py` reports graph states that hide or strand work. It never
changes beads or releases claims. The coordinator verifies findings and applies repairs.
This is a local-only check, outside `make check` and CI; it needs the local Dolt server
unless given an offline snapshot.

```bash
make lint-backlog-graph
python3 scripts/lint_backlog_graph.py --issues-json-file /tmp/issues.jsonl --json
python3 scripts/lint_backlog_graph.py --rules G1,G3 --report-only
```

Live discovery reads all statuses with no list limit so closed blockers and ancestors remain
visible. Offline input accepts a JSON array/object or native `bd export` JSONL, including
`dependencies` entries with `type` and `depends_on_id`. Malformed or duplicate issue records
refuse with exit2. A missing referenced blocker never proves that all blockers are closed.

| Rule | Cause | Fix |
| --- | --- | --- |
| G1 parent-blocks-child | A live parent waits on its own live child. | Remove that blocks edge; close the parent after its children. |
| G2 waits-on-ancestor | Live work waits on its parent or another ancestor. | Remove the edge or point at the specific sibling. |
| G3 stale-blocked | A blocked bead's recorded blockers are all closed. | Reopen it or add the real blocker. |
| G4 blocked-without-reason | Unassigned blocked work has no blocks edge or exempt label. | Add a real edge or reopen it. Assigned manual blocks and `pr-review`, `pr-review-task`, `owner-gated`, `external-gate` labels are exempt. |
| G5 claimable-parent | An open non-epic bead has live children and appears claimable. | Retype the parent to epic. |
| G6 label-only-owner-gate | Open unassigned `owner-gated` work has no unresolved owner-assigned or `decision`-labelled blocker. | Add the owner decision edge, or decide it and remove the label. |
| G7 stale-claim (warning) | A nonclosed agent claim has neither an update nor a latest heartbeat newer than24h. | The coordinator verifies and releases it. |

Owner assignment recognizes the repository owner handle `tze` or the bead's native `owner`
identity. Agent claims recognize `coordinator`, `agent`, `worker`, `codex`, `claude`, `gemini`,
`gpt`, and `orca` actor prefixes (with a colon, slash or hyphen separator). G7 considers
`updated_at` and the last `[beads-heartbeat]` block's `last_heartbeat_at` in UTC; unknown times
do not establish a fresh claim. Parent edges use native `parent-child` (or legacy `parent`)
dependencies and the live-list `parent` projection; ancestor walks tolerate cycles.

G6 recognizes native `open`, `in_progress`, `blocked`, and `deferred` blocker states as
unresolved. A real owner/decision edge in any of those states still gates dispatch; only the
dependent must be `open` for G6 to apply. Closed or unrecognized blocker states cannot establish
an unresolved owner gate, and malformed records still refuse input validation.

Exit0 means clean or G7 warnings only; exit1 means a G1-G6 finding; exit2 means unavailable
or malformed input. `--report-only` suppresses exit1, never exit2. `--json` emits only findings,
one per bead and rule, with `id`, `title`, `ok`, and `violations` (strings including a fix).
`ok` always equals `not violations`; warning-only findings therefore have `ok=false` while
remaining exit0. Consumers must distinguish G7 severity rather than reuse the decision lint's
exit-versus-`ok` convention. The graph linter does not activate a product decision digest.

## Engineering-only consumers

Use `bd` and owner sessions to review and record engineering choices. Product
pages, decision API endpoints, prompt routing, runtime snapshot readers and the
tracker applier are retired. The standalone linter still reads live local Dolt
through `bd`, or an explicit offline file; it never changes beads.

Intent and prompt tables remain until code removal is deployed and all old
readers and writers stop. The later separately reviewed migration may destroy
that history under the accepted owner decision. No drop or deployment occurs
as part of the source retirement.

## Related Pages

- [Operations index](index.md)
