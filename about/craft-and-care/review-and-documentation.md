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

## Document Lifecycle

Give each document a reader, a question, and one authoritative home. Use the
[knowledge map](../README.md#precedence-order-when-layers-disagree) to resolve
conflicts rather than copying competing versions into more files.

| Material | Durable home |
|---|---|
| Product purpose, boundaries, adopted principles | `about/heart-and-soul/` |
| Wire contracts, state machines, architectural decisions | `about/legends-and-lore/` |
| Required behavior and acceptance scenarios | `openspec/specs/`; proposed changes stay in `openspec/changes/` |
| Engineering and verification rules | `about/craft-and-care/` |
| Component and deployment maps | `about/lay-and-land/` |
| Setup, explanation, and recovery procedures | The owning topic in `docs/` |
| Execution ownership, dependencies, and outstanding work | Beads |

Before retiring a plan or brief, compare its substantive clauses with the
owning documents. Synthesize any still-applicable requirement or rationale at
the right home, preserving approval status and unresolved decisions. Coverage
in a spec does not establish implementation; keep outstanding delivery in its
owning change and Beads task. Do not promote a research suggestion into adopted
doctrine merely because it appears in an old artifact.

Delete a spent recipe once its durable content is covered and its live callers
have been repointed. Moving the same body to an archive is not consolidation.
Keep a concise successor/disposition entry where readers need to recover an
old reference; git history preserves the removed body. A retained brief,
research packet, or audit receipt must name the decision, active dependency,
or evidence comparison it still supports and distinguish that role from
current behavioral authority.

Check references across docs, specs, code, tests, skills, and diagrams before
moving or deleting a file. Some planning contracts are read directly by tests:
retire their consumers in the same scoped change or retain the packet until
that dependency is resolved. Verify links and requirement preservation, then
independently review the resulting synthesis.
