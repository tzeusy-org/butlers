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

### Gating work on a decision

- Assign the decision bead to the owner and gate each dependent with a real edge:
  `bd dep add <gated-bead> <decision-bead>`. An `owner-gated` label alone gates nothing: an open,
  unassigned bead with that label still appears in `bd ready` and the fleet can claim it.
- Do not put the `decision` label on `[HOLD]` release gates or on owner action and authorization
  beads ("run the live drill", "authorize the cutover"). The Decision Desk closes a decision bead
  when the owner picks any option, so a "not yet" answer would close it and unblock its dependents.
  Keep those as owner-assigned tasks; an owner action with no blocker uses manual `blocked` status.
- Write options that make sense on their own ("B: keep disabled"), never bare letters: the owner
  answers from Telegram or the dashboard without the description.
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

The linter also emits advisory `warnings` for an option over512 characters or an estimated fixed
Telegram prompt over4096 characters. They appear in human and JSON output. A warning does not
change `ok`, `violations` or exit status: a convention-valid warning-only result still exits0,
convention violations exit1, and input acquisition failures exit2. The existing weekly migration
lint consumes convention violations; these advisories do not add another owner notification.

The estimate uses the current full prompt template, including title, bead id, age, optional UTC
due date, numbered option text, literal/newline characters, repeated default and instruction.
Character counts use the same Python string length as routing. With complete context the
same-clock estimate matches the formatter; incomplete date context uses a conservative0h age
and omits an unusable due suffix. That lower bound can still prove an oversized prompt, while
absence of a warning never certifies eligibility or delivery. Existing parse/label/default/date
requirements and the16-option limit remain unchanged.

Every enabled routing run with an available digest reports integer `skipped_unsendable`, including0:
otherwise-eligible decisions excluded by either length limit, counted once per bead even if both
limits fail. Already-prompted, live-intent, malformed, unavailable and other non-length exclusions
are not counted. Empty-candidate, suppression and budget summaries retain the count. Disabled
or unavailable runs retain their existing response without a fabricated measured count. Counting
adds no per-excluded-bead prompt/intent/ledger write, budget use or delivery; existing enabled-job
maintenance remains. No routing flag, sender, default, resend or callback contract is changed.

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
| G6 label-only-owner-gate | Open unassigned `owner-gated` work has no open owner-assigned or `decision`-labelled blocker. | Add the owner decision edge, or decide it and remove the label. |
| G7 stale-claim (warning) | A nonclosed agent claim has neither an update nor a latest heartbeat newer than24h. | The coordinator verifies and releases it. |

Owner assignment recognizes the repository owner handle `tze` or the bead's native `owner`
identity. Agent claims recognize `coordinator`, `agent`, `worker`, `codex`, `claude`, `gemini`,
`gpt`, and `orca` actor prefixes (with a colon, slash or hyphen separator). G7 considers
`updated_at` and the last `[beads-heartbeat]` block's `last_heartbeat_at` in UTC; unknown times
do not establish a fresh claim. Parent edges use native `parent-child` (or legacy `parent`)
dependencies and the live-list `parent` projection; ancestor walks tolerate cycles.

Exit0 means clean or G7 warnings only; exit1 means a G1-G6 finding; exit2 means unavailable
or malformed input. `--report-only` suppresses exit1, never exit2. `--json` emits only findings,
one per bead and rule, with `id`, `title`, `ok`, and `violations` (strings including a fix).
`ok` always equals `not violations`; warning-only findings therefore have `ok=false` while
remaining exit0. Consumers must distinguish G7 severity rather than reuse the decision lint's
exit-versus-`ok` convention. No existing decision digest consumer is changed here.

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

An applied intent can become `superseded` history when a later local export proves that the
same decision was reopened. Listing or recording a choice requires a timezone-aware bead
`updated_at` after the original applied `finished_at`, a snapshot at least ten minutes after
that finish, and snapshot age between zero and ten minutes. Missing, invalid, future, old or
unavailable evidence preserves the applied live slot; a fresh file mtime alone is insufficient.
Ten minutes is a conservative two-cycle window for the enabled development five-minute export
cadence, not a measured worst-case lag guarantee or calibration for the disabled fifteen-minute
default. An outage cannot qualify by elapsed time alone.

Supersession preserves the original application and audit fields, releases the existing positive
live-index slot, and never requeues or reapplies that intent. A later explicit owner choice gets a
new pending id, including the same option. The Decisions lane shows the live intent first, otherwise
its newest terminal history, and renders superseded as a previous choice. A consumed Telegram
prompt stays consumed and returns Already handled; no prompt is re-sent or routing enabled.
Migration `sw_046` expands the status check only. Its downgrade refuses superseded rows rather
than rewriting or deleting history.

Telegram decision callbacks use `POST /api/decisions/prompts/{prompt_id}/choose` with JSON
`{"option_index": 0, "token": "<original dsk1 callback token>"}`. The connector forwards the
original verified token, its scoped `X-Butlers-Approval-Callback-Token` credential, and the fixed
`X-Butlers-Decision-Actor: owner@telegram` header. A token alone grants no authority; the owner
dashboard key cannot substitute for callback provenance. Caller body attribution is ignored.

The API independently resolves `APPROVAL_CALLBACK_SECRET` from the shared Tier 1 database through
`CredentialStore.resolve(..., env_fallback=False)`. An unavailable pool, absent/empty secret or
lookup failure returns `503 callback_verification_unavailable`; environment values do not rescue
it. Inside the intent transaction the API locks the current prompt with `FOR UPDATE`, verifies the
token against that row's creation time, and compares both the signed prompt id and option index to
the request before any reconciliation or choice write. The lock lasts through commit, including
an authenticated business refusal that commits proved supersession. Invalid authentication
cannot supersede history. A legal reservation may still refresh a `not_attempted` prompt after
the choice commits; an old token cannot authorize that refreshed generation.

Missing, malformed, unencodable, oversized or incorrectly bound tokens return
`403 invalid_callback_token`. Malformed whole requests, invalid path UUIDs and other field
validation errors return `422 invalid_callback_request`; mixed token and other validation errors
use that 422 category. With usable custody a missing prompt remains `404 prompt_not_found`;
unoffered index `422 option_not_offered`, changed options `409 options_changed`, unavailable digest
503, and consumed superseded prompt `409 intent_conflict:superseded` retain their meanings.
Identical authenticated repeats preserve `200` with `meta.created=false`.

Callback audit events retain method, path, status, result, verified context and trace correlation,
but use only a fixed safe body projection, including malformed JSON and the unsupported single
trailing slash. Responses, logs and telemetry omit raw tokens, signature fragments, credentials
and request/exception bodies. The trailing slash remains non-redirecting (owner: 404; scoped
callback credential alone: 401). Connector transport failures say to retry and retain the keyboard.
Deploy compatible connector and API versions together: the API requires `token` and provides no
tokenless fallback. This hardening does not enable routing, expire tokens or change reoffer policy.


## Related Pages

- [Operations index](index.md)
