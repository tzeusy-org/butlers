## Why

## Problem and outcome
A routed model can currently pass verified=True to relationship_assert_fact, and a non-owner family assertion with caller confidence 0.95 can bypass the family confirmation gate. Relationship fact rows lack stored content authority and author identity; their gap callback resolves the current context instead of reading the fact. A third-party assertion of a known person’s new channel identifier therefore becomes an active routing handle. These are current source observations at main 0af0e213acf0239b198e9612731a44957e7f32f5, not an executed exploit or deployed-fleet observation.

Deliver the entire original S1–S4: server-derived fact authority and author; no public MCP verified parameter; owner-admitted confirmation; candidate handles excluded from inbound/outbound identity until an authenticated owner adopts them; authority-based kinship gates and persisted-row gap attribution; a wired Adopt door and “Reported by …” on identity facts. Preserve the literal originals and their completeness gate in the accompanying parity packet. Owner gate bu-9zmre6 is CLOSED and released run16; no repeat owner-release question is required.

## Current evidence and governing contracts
The current MCP wrapper is roster/relationship/modules/tools.py::relationship_assert_fact (1130–1247), forwarding verified at 1236. The central writer is roster/relationship/tools/relationship_assert_fact.py::_assert_on_conn; its family branch at 1424–1496 selects by caller conf, _same_assertion_fields omits authority, and _answer_knowledge_gaps supplies resolve_content_authority(conn) instead of persisted provenance. src/butlers/identity.py::_resolve_entity_by_triple, bulk/phone variants, daemon._resolve_entity_channel_identifier and preferred-channel reachability already require active facts. Candidate storage is the primary correction; every other consumer must still be classified and tested at its actual SQL boundary. verify_entity_contact currently checks owner existence and updates the selected active row; central dashboard authentication is independently enforced by OwnerAuthMiddleware.

Governing: vision.md, security.md, Relationship MANIFESTO.md, RFC0004 identity attribution and credentials seam, RFC0006 schema isolation, relationship-facts, entity-identity, contacts-identity, module-memory Content authority and steering-class admission, dashboard-relationship provenance, and the exactly adopted specify-host-authorized-dashboard-enrollment contract. Active relationship-fact-effective-time and authorize-relationship-effective-time-cutover obligations remain intact; rel_035 and rel_036_meeting_debriefs are immutable. bu-h3b7t retains its whole cutover/fresh-admission implementation. bu-s11n0s.1 is an unadopted conditional custody proposal, not .2 authority, delivered proof, or a blanket prerequisite. bu-q7vx1q.20 owns typed auth_artifact_observed perception and remains undelivered; that event never authenticates a fact writer.

## Steps to reproduce and causal verification
1. In a real disposable database with normal core, approvals, memory and Relationship migrations, use an actually registered Relationship MCP tool under an admitted third-party context to attempt caller verified=True, then inspect committed fact/author/confirmation fields from a separate connection. Retain a positioned historical writer control under the same additive schema, so an old caller-controlled verified assignment goes red for the claimed invariant rather than failing because a future helper is absent.
2. Plant a candidate and an independent active/legacy-NULL positive. Execute actual single/bulk/normalized inbound lookup, daemon outbound identifier and preference reachability queries under their real roles. The candidate must not resolve; the active positive must. After genuine owner adoption the exact candidate must become usable once, with current auth, rollback, conflict and race controls.
3. With conf=0.95, a third-party parent-of/child-of/family-of assertion must park in pending_actions and write no active edge; owner-class and actual executor-approved positives must remain possible under existing owner-subject policy. Gap answers must read stored row authority, including when a different current session attempts an unchanged replay.
4. Exercise protected HTTP Adopt/verify and the rendered contact card/fact list through the real API helpers: missing/forged/revoked owner proof and stale target refuse; successful readback updates routing and accessible UI; pending/error/interruption do not pretend success. No provider or live data is required.

## Completion boundary
All four slices and documentation land together unless the coordinator records a lawful explicit exact-slice deferral in canonical notes under the original completeness clause. PRIMARY source research, a conditional auth interface, a mocked context, a green parser, or a partial backend PR is not completion. No live-fleet assertion is made without separately authorized deployed evidence. All implementation and runtime verification is currently NOT RUN.

## What Changes

Implement the full S1–S4 server-admitted report, candidate review, stored gap authority and reported-by door. Preserve applied rel035/036 and every original scenario. The existing relationship-fact-effective-time holder retains its complete two authoritative blocks; only this bounded authority amendment is added there, and its foreign tasks/archive remain unchanged.

## Capabilities

### New Capabilities
- None: bounded requirements extend existing capability homes.

### Modified Capabilities
- relationship-facts
- entity-identity
- contacts-identity
- dashboard-relationship
- module-memory (Relationship-only consumer alignment; no memory producer policy change).

## Impact

Relationship attribution migration, central writer, registered source/guard/adapters, approval replay, eligible readers, owner contact API/UI and identity documentation. Existing trusted host/runtime premise; request-admission auth; no custody currentness, new role or host service.
