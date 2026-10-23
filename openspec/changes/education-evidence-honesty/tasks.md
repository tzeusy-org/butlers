## Tasks

Implementation is owned by `bu-h40h2b.4`, serialized in three slices, and begins only after this
change is adopted. This change performs none of the work below, so every box stays unchecked until
the corresponding piece lands.

### 0. Archive ordering and rebuild preconditions

- [ ] 0.1 Archive `education-mind-map-lifecycle-integrity` first (`bu-27dxl.17`), then rebuild this
      change's `butler-education` `Scheduled Tasks` block against the archived text.
- [ ] 0.2 Archive `generalist-lead-focused-delegation` (RFC 0038) before this change, then rebuild
      the four blocks both changes modify (`butler-education` `Scheduled Tasks`, `module-education-mastery`
      `Quiz response recording`, `module-education-spaced-repetition` `Schedule Creation via Core
      Scheduler` and `Review Delivery via notify()`) on its text. RFC 0038 wins where it decides; resolve
      the open owner questions in `proposal.md` first.
- [ ] 0.3 Several kept scenario headings no longer describe their bodies (mastery "Response recorded
      with null user answer", "Response recorded with optional session ID" and "Mastered node is not regressed"; spaced-repetition "Schedule name
      encodes node and repetition", "Failed recall resets repetition counter in schedule name" and
      "Delivery channel defaults to the owner contact's preferred channel"), because OpenSpec 1.9
      refuses a MODIFIED block that drops a baseline scenario name. Archive the doctrine
      retire/restore rename pair (guarantee-phrased headings) back to back before this change, then
      rebuild these blocks on the new names. `generalist-lead-focused-delegation` carries the
      null-answer heading too, so coordinate with 0.2.
- [ ] 0.4 Repoint the `scenario` fields of this change's entries in
      `scripts/spec-overwrite-baseline.json` to the new headings by hand, without
      `--update-baseline`, and run `make check-spec-overwrites`.

Acceptance:
- The archived `Scheduled Tasks` block carries the lifecycle paragraph, RFC 0038's obligation wording and the transport-neutral nudge.
- No kept heading contradicts its body, and the baseline entries still match.

### 1. Slice 1: representation and claim honesty

- [ ] 1.1 Additive migrations (nullable): `mind_maps.curriculum_request_id`, `created_session_id`,
      `plan_digest`; `curriculum_requests.plan_digest`; empty `review_cards` and
      `mastery_transitions` tables with their constraints.
- [ ] 1.2 `curriculum_generate()` records the identity link and the canonical `sha256:` digest at activation.
- [ ] 1.3 Receipt settlement uses the Ready predicate and drops time-window correlation.
- [ ] 1.4 API: `verification` on receipts; `measured_over` and nullable average on the mastery summary.
- [ ] 1.5 Frontend: Ready badge only for `verified`; "No graded evidence yet".

Acceptance:
- Requirement "Curriculum plan identity and digest" scenarios pass.
- Requirement "Curriculum request receipt lifecycle" scenarios pass.
- Legacy rows stay readable and render unverified.

### 2. Slice 2: propagation, one writer, review delivery

> Dependency: tasks 2.3 and 2.4 (and the review-delivery acceptance they serve) can land only with or after the RFC 0038 cutover. The Schedule Creation requirement (SR-005) requires an occurrence of a git-declared obligation and bans prompt mode, no obligation runtime exists before the cutover, and prompt mode is the only model-backed scheduled dispatch today.

- [ ] 2.1 Route every evidence-bearing producer through one internal transaction; remove the second public writer and the mastery arguments of `mind_map_node_update`.
- [ ] 2.2 Review cards created or superseded in the same transaction; pending reviews read cards.
- [ ] 2.3 Reconciler standing order in `roster/education/butler.toml`, registered through the Education job map.
- [ ] 2.4 Transport-neutral review prompts and nudge; delivery outcome copied from the attention ledger.
- [ ] 2.5 Update the Education manifesto, `AGENTS.md` and the review, teaching, diagnostic, interactive-response and progress-digest skills so none names two writers or a channel.

Acceptance:
- Requirement "One public evidence writer" and the review-card requirements pass.
- Reconciler replay creates at most one schedule per card.

### 3. Slice 3: enforcement, ledger projection, owner-visible Why

- [ ] 3.1 Require server-attributed owner evidence; row-lock per node; idempotent replay.
- [ ] 3.2 Ledger append in the projection transaction; idempotent `legacy_unknown` backfill.
- [ ] 3.3 Velocity from ledger `occurred_at`, excluding legacy entries.
- [ ] 3.4 Transition history endpoint, typed client and hook, and the node "Why" disclosure.

Acceptance:
- Requirements "Owner evidence gates mastery movement" and "Mastery transition ledger" pass.
- Real-Postgres tests prove transition and projection commit or roll back together and that concurrent duplicate evidence writes one response, one transition and one card.
