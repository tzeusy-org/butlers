## Why

The owner can read and resume conversations but cannot rename them or clear completed threads from active history, although the existing metadata PATCH already supports both operations. Conversation organization should be reversible and preserve the selected thread, unsent words, and any running turn.

## What Changes

- Extend the complete Conversation List Sidebar requirement with inline rename, inline validation, archive with inverse-PATCH Undo, explicit unarchive, and Active/Archived history filtering.
- Expose the same target-specific actions through conversation rows, the shared header, normal keyboard navigation, and the existing command palette.
- Define optimistic metadata feedback, failure rollback and reconciliation, pending-action ownership, and accessible focus and error behavior.
- Preserve selected archived conversations and the adopted same-identity draft lifecycle; exclude archived conversations from automatic resume while retaining explicit archived selection and the existing follow-up reactivation behavior.
- Keep the existing all-status message search explicit rather than imply a status-filtered search the backend does not provide.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chat-ui`: extend Conversation List Sidebar while retaining its three existing scenarios verbatim. This requirement previously had no ID; assign `REQ-dashboard-chat-ui-017`, reserved against the complete public requirement census reviewed for this proposal.

## Impact

Future implementation uses the existing owner-authenticated `PATCH /api/butlers/{name}/conversations/{conversation_id}` with title-only or status-only bodies. It will touch the frontend API adapter, shared conversation mutation/cache seam, list/header actions, and the four existing chat posture owners. List, selected detail, message search, and recent recall must converge without erasing reply, route, cost, or turn evidence.

This changeset is a specification proposal only. No frontend/backend implementation, migration, package change, pin field, bulk action, deletion, new owner-thread identity, routing behavior, queue policy, draft store, or resume recency policy is included. RFC0038's frozen classification, dispatch, spawning, and delegation paths remain outside its scope.

The adopted `persist-safe-dashboard-unsent-drafts` contract remains binding. Its actual shared persistence and acceptance/recovery prerequisites are not yet delivered by the current component-local composers; this proposal neither claims them implemented nor replaces them. Future rename/archive integration must use the delivered same-identity retention seam and prove remount recovery before claiming that guarantee. Pending composer queue, scoped-resume, thread-spine, and domain-action proposals are independently owned and are not adopted here.

## Adoption and Completion Boundary

The pursuit release gate permits this source proposal; it does not adopt its behavior. Independent review and explicit owner adoption of the exact reviewed specification must occur before frontend implementation. Native artifact completeness, successful source checks, issue claim, and the closed parent release gate do not satisfy that adoption requirement.

All four original acceptance criteria remain pending as complete criteria. This source phase can supply specification and validation evidence for criterion 1, but cannot complete its owner-adoption clause or any future implementation/test clause:

> 1. An OpenSpec delta to dashboard-chat-ui with WHEN/THEN scenarios for rename success, validation failure, archive with undo, unarchive, PATCH failure rollback, and keyboard/palette access. It passes openspec validate --strict, make check-spec-overwrites and make check-guards, and is adopted by the owner before implementation. 2. ConversationList.test.tsx and ConversationHeader.test.tsx execute rename and archive against a mocked PATCH, including the failure rollback and the undo. 3. No backend, migration or pin field changes. 4. From frontend/: npm run lint, npm run knip, npm run build and npm test pass.

The eventual implementation retains those criteria unchanged. Source-only validation is not evidence that product interactions, draft persistence, or frontend gates have passed.
