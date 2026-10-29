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

S2 derives the existing wire-pair, heartbeat, default-policy, scope, history and
auth-recovery consumers from the same catalog. Neutral frozen scope records adapt
into the unchanged API types; a small generated public TypeScript projection
feeds the existing recovery resolver independently of generated API DTOs.
Canonical WhatsApp and ActivityWatch now receive their source-owned credential
classification, and explicit legacy selectors remain separate from registration.
No scope version, consent request, observed validity or enrollment flow changes.

S3 binds runtime identities to strict canonical rows, removes the twenty identity
environment reads and twenty-six redundant Compose leaves, and shares Gmail
parser stages while preserving entrypoint semantics and account credentials.
Only Spotify's two unread identity fields are removed.

The complete original follow-on S4 health-kit/port/deployment work remains unfinished.
The owner-deferred Google deployment-consolidation S4 is a different proposal;
Google's four deployments remain separate. Before descriptor S4, the coordinator
must find or file its actual health-kit prerequisite. S1 ships independently.

## Impact

S1 changes catalog data, scrub label selection and discovery. S2 removes duplicate
static consumer declarations; runtime algorithms and deployment state stay owned
by their current consumers. The discovery
wire remains exactly four fields and requires no registry database. Credentials,
authentication, endpoint resolution, deployment state, allowed ingress pairs and
all other operational configuration retain their existing authority. Rollback is
per consumer slice. Later slices require their own concrete source verification.
