## Context

The available-connector endpoint is a static framework catalog consumed by the
dashboard to identify connector types that can be deployed.  Its
`supports_backfill` field conflates that discovery surface with the separate
Switchboard-owned MCP backfill protocol.  The current frontend does not use
the value, and no repository consumer establishes a compatibility reason to
retain it.

## Goals / Non-Goals

**Goals:**

- Make each available-connector profile exactly a four-field deployability
  descriptor: `connector_type`, `channel`, `provider`, and `display_name`.
- Keep backend serialization, its Pydantic model, the frontend API type, and
  local fixtures in lockstep.
- Retain all internal backfill tools and connector heartbeat capabilities.

**Non-Goals:**

- Changing `backfill.poll`, `backfill.progress`, or heartbeat capability
  behavior.
- Adding a replacement capability field, a database migration, or a versioned
  compatibility endpoint.
- Rewriting archived OpenSpec provenance.

## Decisions

### Publish identity, not orchestration capability

Remove `supports_backfill` from the static catalog entries and response model
instead of assigning a default value or adding a renamed capability field.
The catalog's purpose is connector discovery; backfill is a Switchboard MCP
workflow with its own protocol contract.  Keeping a boolean would imply it is
safe for catalog consumers to infer orchestration behavior.  A replacement
field would preserve the same unsupported coupling without a demonstrated
consumer need.

### Test the response shape exactly

The backend contract test will compare each profile's keys with the exact
four-field set, rather than only asserting required-field inclusion.  This
prevents an unreviewed catalog capability claim from returning later.  The
existing membership assertions continue to check representative identity
values without asserting any backfill behavior.

### Change backend and frontend types in one delivery

The Pydantic `ConnectorProfile`, TypeScript `ConnectorProfile`, and the two
typed roster fixtures change together.  Deploying a temporary compatibility
alias is unnecessary because the only verified consumer is updated in this
same repository change.

## Risks / Trade-offs

- **Risk: an unobserved external client relies on the field.** → The change is
  explicitly marked breaking in the proposal and will receive independent PR
  review before merge; no in-repository external-client contract was found.
- **Risk: future code reintroduces a capability claim.** → Exact-key backend
  assertions make the response surface reviewable and fail on additive drift.
- **Trade-off: catalog clients cannot choose a backfill implementation from
  discovery alone.** → That is intentional; they must use the dedicated
  Switchboard protocol contract instead.

## Migration Plan

1. Deploy the backend catalog/model and frontend type/fixture cleanup in the
   same release.
2. No data migration or service configuration change is required.
3. If rollback is necessary, revert the single change to restore the previous
   response shape; no state needs restoration.

## Open Questions

None.  The scope is constrained to the existing endpoint contract and does
not alter the internal backfill protocol.
