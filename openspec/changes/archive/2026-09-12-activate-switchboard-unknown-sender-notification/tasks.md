## 1. Contract and Tests

- [x] 1.1 Add the scoped entity-identity and switchboard-identity deltas, then validate the change strictly.
- [x] 1.2 Add focused failing tests for pipeline wiring, entity-only first/known/repeated sender behavior, failed delivery, failed claim, and competing claims.

## 2. Switchboard Activation

- [x] 2.1 Wire the production Switchboard pipeline with identity resolution and a `notify.v1` owner-delivery callback.
- [x] 2.2 Replace the inactive legacy contacts notification path with the Unidentified Entities route and atomic durable claim behavior.
- [x] 2.3 Update the directly affected identity-model activation note to describe the live behavior.
- [x] 2.4 Atomically reserve or reuse one unknown-sender transitory entity before pipeline activation without moving relationship fact writes into Switchboard.

## 3. Verification

- [x] 3.1 Run focused identity and wiring tests, strict OpenSpec validation, and applicable static checks.
- [x] 3.2 Sync completed deltas into authoritative specs and inspect the final scoped diff.
- [x] 3.3 Add a concurrent pipeline-level proof that first messages with different labels share one entity and identity context before fact assertion.
