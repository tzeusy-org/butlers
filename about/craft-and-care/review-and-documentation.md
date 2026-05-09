# Review and Documentation

This file defines the author and reviewer obligations for Butlers changes.

## Review Should Block On

A reviewer should block the change when any of the following is true:

- The change violates doctrine, manifesto scope, or a known RFC contract.
- The implementation changed behavior without matching spec or doc updates.
- Tests are missing for a practical bug fix or feature path.
- The code introduces hidden fallback behavior, dead compatibility layers, or
  surprising control flow without strong justification.
- Failure paths are harder to diagnose after the change.
- The verification story is too weak for the risk surface.

## Author Obligations

The author is responsible for:

- identifying the relevant manifesto, RFC, spec, and topology context
- making the narrowest change that solves the problem cleanly
- adding or updating regression protection
- updating docs in the same change when behavior, contracts, or workflow
  expectations moved
- stating what verification actually ran
- filing follow-up work in beads instead of leaving informal TODO debt

## Spec discipline

UI mockups that propose a tab list, hero block, or panel set not already
present in `openspec/specs/dashboard-*` require a cited existing capability
or a paired spec change before implementation begins. A mockup is not enough
to create dashboard surface area: if the spec does not already name the tab,
hero, or panel, the author must either point to the capability contract it
renders or land the OpenSpec delta first.

## Reviewer Obligations

The reviewer is responsible for:

- challenging weak assumptions, not just syntax or style
- checking for spec drift, manifesto mismatch, and contract regressions
- asking whether dead paths can be deleted instead of preserved
- checking that verification depth matches the change risk
- distinguishing real compatibility requirements from same-repo inertia

## Same-Change Documentation Rules

Update docs in the same change when you alter:

- user-visible behavior
- MCP tool contracts
- API payloads or routes
- migration/runtime assumptions
- operator workflow
- test or quality-gate expectations
- pillar structure or reading order

The default is not "docs later." The default is "docs now."

## Feedback Posture

Good review culture here is rigorous but not theatrical:

- Accept valid feedback quickly.
- Push back on incorrect or scope-distorting feedback with specifics.
- Prefer evidence and contracts over taste.
- Do not preserve bad code to avoid a hard conversation.
