> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Native-command-before-gating design shipped via the routed-approvals-replayable change.
> **Successor:** `openspec/changes/make-routed-approvals-replayable`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Replayable Routed Approvals Design

Messenger routed delivery currently parks approval payloads that diverge from the
native delivery handlers. The approved action can reach Messenger and still fail
before provider delivery, while Retry describes every failure as an unreachable
butler.

The selected design materializes one native delivery command before gating. Its
registered tool name and exact kwargs drive rule matching, pending-action persistence,
and immediate delivery. Email reply requires provider-native thread identity.
Dispatch returns a structured internal outcome so Retry can distinguish transport
failure from a reachable executor rejection without changing approval state.

Alternatives rejected:

- Retry-time legacy translation cannot reconstruct omitted thread identifiers and
  creates a second mapping surface.
- Persisting `route.execute` would re-enter policy/routing and is not the original
  delivery handler contract.
- Treating `request_id` as Gmail `thread_id` risks replying to the wrong conversation.

Historical malformed actions remain untouched. The incident action will expire
without delivery. Adjacent non-Messenger approval producers are separate follow-up
work because they have different owners and trust boundaries.
