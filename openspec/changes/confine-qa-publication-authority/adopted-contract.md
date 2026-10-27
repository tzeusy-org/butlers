# Proposed QA publication authority boundary

Status: PROPOSED, not adopted. Repository butlers, evidence commit
8607220fdd283b155486363684d2b1e80a23acb8, 2026-09-27.
Affected gates: bu-nh37dt and bu-vc90le. No credential, organization settings,
runtime, or live QA identity was inspected.

## Problem and exact change

The current QA spec and manifesto require a token usable for branch push,
PR creation and labeling but with no merge or approval permissions, while
injecting that token into investigation agents. GitHub documents Contents
write for merge and Pull requests write for review creation as well as the
intended publication operations. Therefore a token permission name alone is
not evidence of the required prohibition. Current provider restrictions are
unknown; this is not a claim that the deployed identity can merge.

Adopt effective authority confinement instead of promising a distinct native
GitHub no-merge/no-approve token scope: the trusted deterministic publisher
holds the dedicated QA credential; investigation agents receive no GitHub
credential or generic publication capability. Humans retain merge and approval.
The publisher remains a trusted component with a provider credential whose
coarse permissions may include more than its exposed operations. Compromise of
that trusted component is a residual risk, reduced but not eliminated by
repository protections; do not describe this as provider-side least privilege
equivalent to an absent merge/review permission.

## Binding requirements

1. Credential ownership: only trusted deterministic QA publication code resolves
   the existing dedicated QA secret. No owner-token or ambient-token fallback.
   No credential reaches investigation environment, prompt, filesystem, logs,
   stdout/stderr, tool result, or subprocess under agent control. The existing
   agent-environment scenario must remove GH_TOKEN injection. Build/test agents
   must remain unable to read publisher process state, credential files, IPC,
   inherited descriptors, or another process's environment. Failure to prove
   the process/filesystem boundary disables automated publication.
2. Server-held binding: publication uses a dispatcher-created attempt identity,
   allowlisted repository, one newly allocated QA branch and the PR recorded
   for that attempt. Agent input cannot choose repository, remote, ref, base,
   PR identity, endpoint, credential, operation, labels or arbitrary command.
   Revalidate binding and expected head before every mutation. Duplicate
   requests cannot create a second branch/PR or update an unrelated resource.
3. Closed action surface: permit only validated QA branch publication/update,
   creation/update of its bound PR and fixed sanitized labels. No merge, review,
   approval, queue admission, protected-ref writes, force push, unrelated branch
   or PR, release, settings, workflows, secrets or access-management operation.
   Unknown operations fail before egress. Existing explicit human repository
   merge workflows are outside this QA automation surface and remain unchanged.
4. Treat the submitted tree and metadata as untrusted. Validate all externally
   published commit/tree content, messages, author metadata, PR text and labels
   before the first push. Never execute agent-controlled hooks, git filters,
   git config, credential helpers, remote URLs, executables or workflow files
   inside the credentialed process. A trusted clean export and pinned commands
   must separate source validation from publication; the agent cannot swap
   validated bytes before use. The current anonymization contract remains.
   Unsupported or unprovable sanitization refuses publication with a category.
5. Provider controls: use one-repository dedicated identity, minimum documented
   provider grants, and no administrative/ruleset bypass. Verify effective
   protected-branch restrictions and identity category independently before
   activation. Record the difference between coarse provider grants and the
   narrower publisher action allowlist. Local denial tests do not establish
   provider-side restrictions, and provider roles do not establish containment.
6. Durable outcome: persist attempt-bound expected head, operation stage and
   sanitized terminal/ambiguous category outside agent control. A timeout or
   uncertain response cannot trigger blind retry, cleanup or another resource.
   Read-only reconciliation of the exact bound resource may determine outcome;
   absent proof stays held. Rollback disables publication and keeps evidence;
   never restore token injection as fallback. No remote deletion is implicit.
7. Verification: use synthetic worktrees, mock GitHub transport and actual
   process isolation tests for credential absence, malicious git config/hooks,
   race after validation, target/PR/head substitution, forbidden actions,
   ambiguous mutation, duplicate request and sanitizer failure. One separately
   authorized controlled-resource live run later exercises the same publisher;
   no forbidden real mutation is used as a negative test.
8. Evidence and operational boundary: store only approved categories/digests in
   operational receipts. Do not expose private identifiers, raw provider errors
   or credential material. New credential grants, account/organization changes,
   live canary publication/cleanup, deployment and runtime activation each stay
   outside repository implementation authority. Both operational gate Beads
   remain open until their actual evidence is produced.

## Canonical integration and implementation allocation

Translate these requirements into complete MODIFIED blocks for Investigation
Agent Sandbox and relevant publication requirements in
openspec/specs/qa-investigation-dispatch/spec.md, an active OpenSpec change,
roster/qa/MANIFESTO.md and related source topology documentation. Do not edit
canonical baseline in place or archive unrelated deltas. First reconcile active
same-named blocks and other QA ownership. Any translation weakening a requirement
returns to review; changed user authority returns to the owner.

Proposed cohesive outcomes, sequential for shared ownership:
- canonical spec/manifesto integration and exact contract trace;
- credential-free investigation plus trusted publication operation boundary,
  durable outcome/reconciliation and synthetic adversarial verification;
- isolated operational verifier and content-blind runbook, followed by separate
  exact-environment owner authorization, not automatic activation.

Candidate seams: src/butlers/core/qa/dispatch.py, core/spawner.py,
core/qa/repo_whitelist.py, core/healing/anonymizer.py (reuse, no broad rewrite),
tests/core/qa/test_dispatch.py, test_dispatch_whitelist.py,
test_anonymization_failure.py, test_label_sanitization_gate.py and existing
actual sandbox tests. Final runnable packets require current source ownership,
exact isolation design and named migration allocation before code dispatch.

## Primary provider references checked 2026-09-27

- https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request
- https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request
- https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens

Repository evidence: qa-investigation-dispatch spec sandbox/credentials
scenarios; roster/qa/MANIFESTO.md 136-148; dispatch.py build_sandbox_env,
build_git_auth_env and _create_qa_pr. The read-only dossier metadata client's
credential fallback is a separate path, not proof of publication fallback.
