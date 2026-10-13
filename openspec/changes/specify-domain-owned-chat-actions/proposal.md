## Why

Chat needs a reviewable action proposal and truthful receipt without acquiring the domain authority that belongs to existing butlers. The identity/resume and durable approval-delivery prerequisites have landed; the remaining gap is a single adopted contract for eligibility, provenance, shared execution recovery, and reversal.

## What Changes

- Specify server-owned, existing-domain action eligibility; `gated`, `page_authority`, and `self_scoped` classify actions but grant no authority. Unclassified, absent, disabled, foreign, secret, and policy-changing commands fail closed.
- Project already-parked domain approvals into durable conversation cards, with verified turn provenance and immutable owner/command/argument bindings.
- Reuse the existing approval decision and owning-daemon execution spine; fence uncertain chat-linked attempts across every existing dispatch/retry entrance.
- Define executed, queued, failed, and ambiguous receipts, plus supported reversal, compensation, and irreversible confirmation.
- Reinforce Concierge's existing read-only capability, manifesto, and roster boundary.
- Keep this changeset **DRAFT / NOT ADOPTED**. All future implementation and activation tasks remain unchecked. Syntax completion does not authorize `bu-0ynlk.8`.

## Capabilities

### New Capabilities

- `chat-domain-actions`: Existing-domain action eligibility, proposal identity and persistence, trusted chat provenance, shared decisions/execution, honest receipts, reversal, and compatibility.

### Modified Capabilities

- `butler-concierge`: Preserve the complete Read-Only Tool Surface requirement and explicitly exclude chat action execution/registration.
- `module-approvals`: Preserve the complete Shared Executor Path requirement and qualify chat-linked recovery with durable attempt evidence.
- `dashboard-conversations`: Add an action projection integration requirement without overwriting active Conversation Pydantic Response Models or channel-anchor/resume contracts.
- `dashboard-chat-ui`: Add the domain-owned action card presentation requirement.
- `dashboard-approvals`: Preserve Safe retry refreshes confirmed approval state and add the chat-linked ambiguity restriction shared by the chat card.

## Impact

This PR changes only OpenSpec planning documents. Tests: +0 ~0 -0. It creates no tool, schema, API, UI, action decision, provider effect, deployment, or activation.

Future seams are the owning daemon/tool metadata, normal approval parking and shared executor, authenticated `/approvals` decisions, conversation persistence/SSE/read models, immutable audit, and the existing chat/approval React surfaces. No Concierge/browser authority registry or direct peer-schema access is introduced.

Governing intent is the clarified owner decision `chat-actions-v1-scope`, recorded in `bu-ubqnma` and `bu-0ynlk.8`, together with current doctrine and canonical specs. The historical broader REST-registry sketch is superseded. PR #3960 and PR #4203 are authoring prerequisites already merged, not an adoption of this new contract.

Release requires independent semantic/security review of the exact draft, explicit owner adoption of the exact reviewed revision, and landing the adopted source before `bu-0ynlk.8` becomes runnable. Runtime enablement remains a later gate with executing evidence.
