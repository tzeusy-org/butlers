# Consolidate connector descriptors

## Why

Discovery currently publishes ten channel/provider pairs that disagree with
existing connector emissions. Separate connector lists also hide the distinction
between canonical identities, safe display aliases and deployment targets.

## What changes

S1 introduces a pure frozen connector catalog, derives the buffered-preview label
set and four-field discovery response from it, and checks those projections against
the unchanged accepted ingress relation. Discovery includes the already-supported
local/Compose Live Listener. No connector, accepted pair, table, credential or
OAuth flow is added. The current membership cases retain their inputs and
assertions while replacing the obsolete Gmail provider and Telegram Bot channel
expectations with their existing canonical source identities.

This change records the complete original follow-on work, without claiming it is
implemented: S2 contract/default/scope/history/auth consumers, S3 connector identity
configuration and loader consolidation, and S4 health-kit/port/deployment wiring.
The owner-deferred Google deployment-consolidation S4 is a different proposal;
Google's four deployments remain separate. Before descriptor S4, the coordinator
must find or file its actual health-kit prerequisite. S1 ships independently.

## Impact

S1 changes catalog data, scrub label selection and discovery only. The discovery
wire remains exactly four fields and requires no registry database. Credentials,
authentication, endpoint resolution, deployment state, allowed ingress pairs and
all other operational configuration retain their existing authority. Rollback is
per consumer slice. Later slices require their own concrete source verification.
