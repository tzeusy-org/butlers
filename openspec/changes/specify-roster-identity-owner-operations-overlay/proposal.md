## Why

At historical baseline `63de6168fc852fcbf4e8fc24717585f64df0e2a5`, the roster and dashboard
had incompatible system-prompt authority stories. `bu-f5146g` approved the bounded direction
"Roster identity plus owner operations overlay", not adoption or implementation of this exact
artifact. The predecessor evidence map is `docs/plans/system-prompt-authority-decision.md`
(PR #4152, last changed by `d44446ea0ca6d35eee5098c7d9e4b57de50a5dd3`). Travel's old
observation was already corrected by PR #4154 (`ebdfd64ea74787a89de3f723d2258304464c840c`);
QA's approved staffer exception came from PR #3110. These are historical facts, not fresh
runtime observations.

Adopted RFC0038 D1/D2/D9/D15 supersedes this draft's domain-session composition target.
Domain `CLAUDE.md` is versioned guidance; the lead system prompt comes from lead policy and
the owner profile, the child's from its brief. Domain daemons do not compose reasoning-session
prompts. The generalist collision disposition explicitly requires this rescope before archive,
including the same-name base and spawner blocks. Conforming text needs no new owner exact-text
sign-off and changes no RFC0038 decision.

## What Changes

- Replace the old eight-layer domain-session prompt with the RFC0038 lead/child contract.
  A lead starts with fenced goal input, owner profile, lead policy, a domain/guidance index,
  and its recovery ledger summary. Other context arrives through stamped `context.search`,
  domain read capabilities and `guidance.load`; children receive their brief and governing
  guidance references. Domain text, stored overlays and legacy selectors are not extra system
  prompt sources. Situational-context injection does not survive the cutover.
- Preserve recursive bare-reference resolution relative to its containing file, canonical-roster
  confinement, cycle and missing/read-failure bounds, and the distinct non-recursive HTML include
  contract. Apply them to versioned guidance loading; no runtime adapter expands includes.
  Domain shared instructions remain explicit. Staffer participation remains an infrastructure
  contract choice; QA's opt-out and unchanged prompt bytes remain protected. D18 engineering
  executors are distinct from staffer goals and do not create a domain-session exception.
- Preserve owner-only management, provenance and history duties without claiming that stored
  owner operations are the current goal system prompt or an automatic runner input. Retain
  pre-body/pre-pool authentication, server attribution, central key-or-session admission,
  independent CSRF/exact-Origin checks for cookie unsafe actions, bounded literal input,
  compare-and-swap/no-op concurrency and atomic content-blind audit. No new runtime delivery
  route, grant, subscription or activation mechanism is selected here.
- Preserve the dedicated direct-login writer, separate non-login owner, least-privilege RLS,
  fixed role mapping, generic-pool denial, bootstrap reconciliation and real-PostgreSQL proof
  obligations. Domain credentials do not become a runner credential or cross-domain read path.
- Preserve every historical row and its exact provenance. Old `roster_overlay`,
  `precutover_legacy_hold` and `legacy_full_replacement` selectors remain historical migration
  evidence, not selectable goal-runtime modes. An owner acknowledgement, old rollback deadline
  or compare-and-swap write cannot override D15 or re-enable domain reasoning sessions.
  Operational rollback requires its own authority and cannot silently select a second live runtime.
- Keep the prompt/writer inventories explicitly anchored to the historical source above.
  `core-skills / AGENTS.md Read/Write Access` remains separately owned by
  `specify-improvement-proposal-spine`; this change does not modify or adopt it.

This is a spec-only conformance amendment. It authorizes no runtime implementation, prompt/roster
edit, overlay activation, schema/grant/credential change, database access, deployment, archive or
release. Future feature adoption, exact-head security review and operational authority gates remain
separate; they do not gate merging this already-authorized RFC-conforming text.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-base-spec`: Versioned domain guidance and the lead-policy/owner-profile/child-brief boundary.
- `core-skills`: Recursive, confined guidance resolution without touching AGENTS read/write ownership.
- `core-spawner`: Goal versus engineering-executor prompt inputs, retrieval and adapter invariance.
- `dashboard-butler-management`: Owner-only operations/history without goal-prompt selection authority.
- `database-security`: Writer/RLS/bootstrap isolation and truthful migration/rollback history.
- `staffer-archetype`: Explicit shared-guidance participation without a domain-session exception.
- `staffer-qa`: QA's approved opt-out, isolated engineering executors and exposed staffer-goal distinction.

## Impact

Only this change's proposal, design, task text and seven capability deltas change. All eight
requirement names, 69 scenario obligations and 42 existing task checkbox states are retained;
obsolete mechanisms have explicit goal-runtime dispositions rather than a parallel live target.
The four completed draft tasks are historical completion, not proof of the pending conversion or
future implementation. No canonical baseline spec, RFC, roster, product or test is edited.
The only ratchet change appends the authorized `clause / Interactive response mode /
e69f93fa9eaa` identity under
`specify-roster-identity-owner-operations-overlay/butler-base-spec/CLAUDE.md as System Prompt Entry Point`
in `scripts/spec-overwrite-baseline.json`; no global regeneration or matcher/threshold change occurs.

Future executable owners remain the skills/guidance loader, goal runner and prompt adapters,
`tests/features/test_skills.py`, `tests/core/test_core_spawner.py`,
`tests/core/test_core_spawner_context.py`, `tests/api/test_butler_management.py`,
`tests/config/test_roster_claude_md_include.py`,
`tests/config/test_delegation_guidance_reachability.py`, and the separately authorized real-PG
prompt-history role/migration suite. These are future verification obligations, not executed proof.
QA governance may later update MANIFESTO/capability text only; QA prompt bytes and Travel's repair
remain unchanged. `Tests: +0 ~0 -0` for this amendment.
