# Decision Beads

A decision bead asks the owner to choose among options, not just to do a task. The convention uses
only native `bd` fields, so the decision-review runtime, the linter and the dashboard can read it
without a bespoke format.

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

## Consumer

`src/butlers/jobs/decision_review.py` runs the weekly decision-review digest and the P1/deploy
escalation cron (both no-ops when `BUTLERS_DECISION_REVIEW_ENABLED=0`). It classifies open,
non-epic decision beads by the `decision` label alone, and its digest runs the strict lint against
the mounted `issues.export.jsonl`, sending a low-priority nudge when it finds unmigrated beads.

The Decision Desk records the owner's answer (`bu-ckkpz.3`). A choice made through
`POST /api/decisions/{bead_id}/intent` or a Telegram decision prompt (`jobs/decision_routing`, off
unless `BUTLERS_DECISION_ROUTING_ENABLED=1`) is stored in `switchboard.decision_intents`. It must
name one of the bead's current `metadata.decision.options` exactly. The beads CronJob then closes
the bead with `bd`, with the reason `Decision: <option> (decision-intent <id>, via <source>)` and
actor `butlers-decision-desk`. An intent whose bead was closed, relabelled or re-optioned first
fails with a categorical reason and leaves the bead alone; see
[Kubernetes deployment](kubernetes-deployment.md#beads-export).

## Related Pages

- [Operations index](index.md)
