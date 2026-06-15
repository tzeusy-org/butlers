# Switchboard Identity — Entity v3 Delta

This delta adds one invariant to `switchboard-identity`, scoped precisely to **entity-fact assertion**. It changes nothing else: `resolve_contact_by_channel()` continues to read `relationship.entity_facts` exactly as mandated by `relationship-facts` ("re-points to triples"), and the standing unknown-sender **temporary contact creation flow** (`switchboard-identity` "Inbound message identity resolution" — switchboard creates the temp contact in `public.contacts`/`public.entities` before routing, per `roster/switchboard/tools/identity/inject.py`) is unchanged and explicitly out of this invariant's scope.

## ADDED Requirements

### Requirement: Switchboard never asserts entity facts

The Switchboard MUST NOT call `relationship_assert_fact` and MUST NOT issue `INSERT`/`UPDATE`/`DELETE` against `relationship.entity_facts`. Its access to that table SHALL remain read-only channel resolution via `resolve_contact_by_channel()`. Fact assertion arising from ingress (observed identifiers, extracted relationship signals) happens exclusively inside the domain-butler sessions the Switchboard routes to — classify-and-route stays free of domain fact writes (Switchboard manifesto non-responsibilities). The standing temp-contact creation flow for unknown senders (writes to `public.contacts` / `public.entities`) is NOT a fact assertion and remains permitted. A guardrail test MUST source-scan the switchboard module and roster trees for `relationship_assert_fact` invocations and for write-DML on `relationship.entity_facts`, and MUST fail on a hit.

#### Scenario: Unknown sender gets a temp contact, but facts come from the routed session
- **WHEN** a message arrives from an unrecognized sender
- **THEN** the Switchboard MAY create the temporary contact/entity per the standing identity-resolution requirement
- **AND** the Switchboard MUST NOT write any `relationship.entity_facts` row for it
- **AND** any fact assertion (e.g. `has-handle` for the observed channel) MUST occur inside the routed domain-butler session via `relationship_assert_fact()`

#### Scenario: Guardrail catches a switchboard fact-write path
- **WHEN** a change adds a `relationship_assert_fact` call (or write-DML on `relationship.entity_facts`) to switchboard code
- **THEN** the guardrail source-scan test MUST fail
