## Why

Today a QA investigation runs with the launching butler's persona and memory, and its system prompt
forbids reading source and using the shell. It resolves from the `specialty` tier, shared with
discretion calls, where the first choice is a cheap model that was never chosen to write code. RFC
0038 D18 already mandates that engineering executors get no situational context, and the model
ladder must be explicit about which rung writes code.

The owner approved this direction on `bu-l34qcx` (2026-11-22): default rung `workhorse`, one
reasoning escalation per incident generation (move C budget: 3 sessions / 60 minutes), healing does
not share the profile in this change.

## What changes

- A `qa` session is launched with one versioned, core-owned executor prompt: no persona, DB prompt
  override, memory, situational or timezone preamble, or blind-spot disclosure. Its provenance is the
  single source `engineering_executor_profile@v1`. A missing profile refuses the launch.
- The `qa` dispatch intent requires a new `workspace_agent` capability at `reversible` consequence
  and resolves the exact `workhorse` rung with no tier fallthrough. Admission (Gate 10) resolves under
  the launch identity and intent. `DISPATCH_POLICY_VERSION` becomes `3`.
- A provider auth failure in the executor sandbox is `executor_env_unfit` and never counts toward the
  catalog-entry breaker.
- Replaces `REQ-qa-investigation-dispatch-001` scenario "Agent uses specialty model tier" and adds
  "Executor prompt excludes butler persona and context". No schema change.

## Not decided here

Healing sharing the profile, the reasoning-escalation trigger and its spend accounting (`bu-6a9xxw`
move C), and raising an action-needed condition for `executor_env_unfit` (follow-up).

Tracking: `bu-ejow5p.5`. Spec wording lands in a PR marked `(needs owner approval)`.
