# Specify a content-blind Home Assistant person-mapping boundary

## Why

`connectors.home_assistant_persons` can already hold a direct
Home Assistant person ID to `public.entities.id` mapping, but the product has
no safe operator path for creating one. Direct SQL, an MCP tool, a prompt, or a
URL would put private household identity data outside the dashboard's narrow
owner-control boundary. Name or alias inference would also turn an
owner-authoritative identity decision into a guess.

The actual mapping submission (`bu-pvapy`) must therefore remain held behind a
specified, reviewed, and separately approved dashboard workflow. This change
defines that contract only. It does not collect identifiers or perform a
mapping.

## What Changes

- Add one future endpoint, `POST /api/home/person-mappings`, accepting a raw
  encoded request body bounded to 32,768 octets before JSON decoding and a
  batch of exact, already-observed `person.<slug>` IDs and existing live person
  entity UUIDs. Neither identifier appears in the URL.
- Require the existing fail-closed `require_dashboard_owner_control` boundary
  before any body read or database acquisition. `authenticated_principal()` is
  used only after authentication to derive the persisted actor.
- Record the closed, adopted `bu-pb6oy` configured-key decision: the browser
  establishes an expiring/revocable server-managed session over HTTPS using an
  HttpOnly/Secure/SameSite=Strict cookie, state-changing requests receive CSRF
  protection, non-browser callers retain `X-API-Key`, and same-origin alone is
  never owner authentication. Its conforming implementation remains a UI
  prerequisite; this proposal neither implements nor alters it.
- Keep default keyless Compose usability separately unresolved under
  `bu-azqfpk`: the owner has not selected E1 host authority (transferable
  one-time host code or host approval of an inert browser challenge) or E2 HTTPS
  entry (existing Tailscale Serve or a separately specified loopback TLS
  terminator). This change selects neither enrollment mechanism.
- Define a server-generated opaque receipt and browser-generated opaque
  idempotency key, one mapping-specific transaction advisory lock, exact replay,
  deterministic locks on every referenced entity row, identical no-op, and
  all-or-nothing conflict handling. A mapping conflict in either direction
  returns a content-blind `409` and commits no mapping row.
- Exclude the entire request and both private identifiers from generic audit
  body capture, logs, URLs, errors, responses, metrics, traces, prompts,
  sessions, and MCP tools. Explicit audit records contain only the operation,
  server-derived actor, receipt, aggregate counts, outcome, and a fixed failure
  category.
- Require future real-PostgreSQL, API, browser-client, concurrency, rollback,
  and privacy absence tests before implementation can be considered complete,
  including forced entity-lifecycle and simultaneous-idempotency interleavings.

## Capabilities

### New Capabilities

- `home-assistant-person-mapping`: owner-authenticated, dashboard-only creation
  of exact Home Assistant person-to-existing-person-entity mappings.

### Modified Capabilities

None.

## Impact

- Affected future API surface: `POST /api/home/person-mappings`.
- Affected future storage seams: `connectors.home_assistant_persons` and a
  content-blind durable idempotency/receipt record.
- Affected future UI: a dashboard-only mapping form that keeps submitted values
  out of navigation state, browser persistence, query keys, and rendered errors.
- This draft changes no runtime behavior, schema, frontend, mapping data,
  credentials, deployment, or environment.
- `bu-q364q` remains blocked on independent exact-head privacy/security review,
  separate owner approval of the exact artifact, implementation of the adopted
  configured-key session boundary, and separate resolution/adoption of
  `bu-azqfpk` E1/E2 before claiming default keyless Compose usability.
  `bu-pvapy` remains blocked on the later implementation and environment
  availability. Merge, queue, deployment, and actual mapping submission are
  separate acts.
