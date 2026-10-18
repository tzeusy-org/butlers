## Why

The Education butler's manifesto promises a tutor that knows what the owner knows. Today it
reports progress it cannot back, in both directions (jarvis pursuit run 13, move 4, `bu-h40h2b.4`):

- The curriculum page can show "Ready" for a request that has produced no usable curriculum,
  because a receipt settles on "a map was created after the trigger time", which is a timestamp
  guess and not an identity.
- Mastery can move on a quiz response whose `user_answer` is `NULL`; the spec for
  `mastery_record_response()` explicitly permits that.
- No review card exists as an object. Pending reviews are inferred from `next_review_at` and the
  scheduler, so a schedule's existence stands in for a question having been asked.
- "Avg Mastery 0%" is a mean over zero graded rows.
- Nothing records why a node has the status it has, so the owner cannot audit "why does it think
  I know this?", and learning velocity is computed from `mind_map_nodes.updated_at`, which changes
  for reasons that have nothing to do with mastery.
- Two specs disagree about whether a mastered node can regress, and the review nudge is specified
  to use Telegram although Education is meant to be transport-neutral through `notify()`.

The governing rule is that Education may claim only what its durable evidence establishes.

This change drafts the normative text so the owner can adopt, amend or decline it. It does not
implement anything. Implementation is `bu-h40h2b.4` and stays blocked until this change is
adopted. Declining it closes that bead.

## What Changes

- **Ready claim.** A curriculum receipt is Ready only for the request- and session-linked active
  map that has at least one node and a canonical plan digest. Empty, draft, unlinked and digestless
  maps settle `failed` (`no_curriculum_created` or the new `curriculum_unverified`). Legacy
  completed receipts stay readable and are exposed as `legacy_unverified`; they are never
  retroactively upgraded. The zero-node half of this predicate is already enforced for every map
  by `education-mind-map-lifecycle-integrity` (PR #4421); this change adds only the identity link
  and the digest.
- **Review cards.** `education.review_cards` becomes the durable object and the only source of a
  pending review. A deterministic reconciler job materializes `review-card-{card_id}` schedules
  and retries failures without touching learning evidence. Delivery is transport-neutral and its
  outcome is copied from the attention ledger, never upgraded.
- **Owner-evidence mastery.** Mastery moves only on a nonblank answer whose provenance the server
  derived from the runtime session. Null or blank answers are rejected before any write; scheduled
  or model-initiated sessions cannot grade; replay is idempotent; concurrent answers serialize.
  There is one public evidence writer. Mastered nodes may regress on owner-evidenced failed
  retention. The map summary reports a nullable average with an explicit `measured_over`.
- **Transitions ledger.** `education.mastery_transitions` records every actual status change in the
  same transaction as the projection, with the evidence that caused it. Pre-existing mastered nodes
  get an explicit `legacy_unknown` entry, never a synthetic proof date. Velocity counts ledger
  transitions by `occurred_at` and ignores legacy entries. The node detail panel gains a "Why"
  disclosure.

Capabilities touched (see `specs/`): `module-education-curriculum`, `module-education-mastery`,
`module-education-spaced-repetition`, `module-education-analytics`, `dashboard-education-api`,
`dashboard-education-ui`, `butler-education`.

## Non-goals

- No implementation code, migrations, scheduler registration, UI, or live-data action here.
- No dashboard or chat answer composer, concepts-as-entities, learning-intent work, external LMS
  integration, or new connector or transport.
- No claim that channel acceptance means the owner read or answered.
- No re-grading of historical answers, synthetic transition timestamps, or inferred owner evidence.

## Overlap and sequencing

- `education-mind-map-lifecycle-integrity` is implemented (PR #4421) and waits only for
  post-deploy verification and archive (`bu-27dxl.17`). This change is separate so built and
  unbuilt requirements are not mixed. It restates the lifecycle change's `Scheduled Tasks`
  block with one added paragraph and a transport-neutral nudge, so that whichever archives
  second must be rebuilt against the other (see `tasks.md`, section 0).
- Requirements this change rewrites that the lifecycle change does not touch were rebuilt
  against the current baseline text in `openspec/specs/`.

## Decisions for the owner

- Separate change rather than folding into the lifecycle change (recorded on `bu-6e8j89`).
- Mastered nodes may regress to `reviewing` on an owner-evidenced failed review. The baseline
  mastery spec forbade this while the spaced-repetition spec required it; this change picks the
  regression and ties it to evidence and the ledger.
- Every quiz response is treated as evidence-bearing, so the null-answer insert is rejected
  outright rather than stored as a zero-quality row.
- Schedule names change from `review-{node_id}-rep{N}` to `review-card-{card_id}`; the batch
  schedule `review-{mind_map_id}-batch` is kept.
- Impact if declined: the status quo stays and `bu-h40h2b.4` closes as declined.
