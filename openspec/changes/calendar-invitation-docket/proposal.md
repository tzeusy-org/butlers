## Why

Most invitation answers repeat the owner's own judgment: "decline anything in my focus blocks",
"accept recurring 1:1s from my manager", "tentative for all-hands". Today each one is read,
decided and answered by hand. Owner decisions forbid sending an answer to another person without
the owner's approval of that exact call, so the labor cannot simply be automated. bu-5ii4gj.9
(calendar pursuit run 26 move 9) asks that the owner write the judgment once, as typed policies,
and that each pending invitation then arrive already answered, with the exact call and the policy
term that produced it, for the owner to sign.

## What changes

- **Owner-written policies.** A `public.calendar_rsvp_policies` table, versioned, written only by the
  owner through a dashboard route. Each policy is an ordered list of closed typed predicates, a
  verdict (`accepted`, `declined`, `tentative`) and a `send_updates` choice. No title or description
  is ever a predicate input; no model writes or edits a policy.
- **Deterministic evaluation.** A pure function picks the first matching policy for an invitation
  from verified facts. No model, no provider call.
- **Prepared answers.** A deterministic `calendar_rsvp_docket` job (calendar-module logic), in the one butler that holds the
  calendar module and its response fence, parks one per-event approvals card per matched
  invitation through the existing `prepare_and_park`, with a server-built dossier citing the policy
  id, its version and the matched facts.
- **A docket to sign.** A dashboard docket lists every parked answer with its exact call; one submit
  approves each card individually through the existing approve path. A card that changed since it
  was shown is refused and named.
- **Per-policy agreement ledger.** Approved as prepared, rejected by the owner or answered elsewhere, shown beside each
  policy. It never feeds autonomy promotion and never creates a rule.

## Out of scope

No standing rule, auto-approval or promotion for `calendar_respond`: a policy can only prepare an
answer, never send one. No learned or model-written policy, no series-root answers (the response
snapshot already refuses them), no counter-proposal of new times and no decline note template (that
would extend `calendar_respond`'s public arguments and the custody fence to content-bearing
egress). The goal-runtime decision set is not used; the docket is a dashboard batch whose submit re-evaluates each card and is
the owner's own on the owner surface. The declared-hours flag is a separate change
(`calendar-declared-hours-guard`).
