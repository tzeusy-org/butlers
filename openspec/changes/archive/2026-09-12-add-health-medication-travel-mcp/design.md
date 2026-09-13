## Context

Medication records are owned by the Health butler and stored as active `facts` rows with
`predicate = 'medication'`, `scope = 'health'`, and metadata containing `name`, `dosage`,
`frequency`, `schedule`, `active`, and optional `notes`. The old relational medication tables are
orphaned. Travel currently has no sanctioned way to read those facts, and its insight job contains
an intentional medication-preparation no-op.

Vision Rule 3, RFC 0002, and RFC 0006 prohibit Travel from importing Health code or querying the
Health schema. RFC 0010's read-only view exception does not apply because this is an on-demand,
bounded domain query rather than a pre-scheduled batch aggregation. Travel already receives a
Switchboard MCP client during module runtime wiring, and the Switchboard's `route` tool can call a
named tool on Health's MCP server.

## Goals / Non-Goals

**Goals:**

- Establish Health as the authoritative provider of active medication preparation data.
- Return only fields needed to prepare for a trip.
- Route Travel's request through the Switchboard and Health MCP endpoints.
- Enforce Travel's existing `cross_butler` permission before dispatch.
- Define a strict, versioned response with deterministic empty and error behavior.
- Reuse the canonical Health fact storage without a migration.

**Non-Goals:**

- Generate medication-preparation insight candidates; that remains bu-za343.
- Export conditions, symptoms, dose history, notes, timestamps, or raw Health facts.
- Estimate remaining medication quantity; current storage does not capture inventory.
- Add a direct database view, grant, shared table, or Travel-to-Health Python import.

## Decisions

### Health provides a purpose-specific MCP tool

Health registers `medication_travel_snapshot`. It reads active medications from the existing
Health-owned fact surface and projects each record to `name`, `dosage`, `frequency`, and
`schedule`. It never returns notes, dose history, timestamps, fact content, entity identifiers, or
unrelated health data.

The provider returns a strict `health.medication-travel.v1` envelope. A successful lookup with no
active medication returns `status = "ok"` and `medications = []`; absence is not an error.

Alternative considered: route directly to the existing `medication_list` tool. Rejected because it
returns notes, identifiers, and timestamps that Travel does not need.

### Travel owns the Switchboard-routed consumer

Travel registers `health_medication_snapshot`. Its implementation:

1. Checks `public.permissions` for Travel's `cross_butler` capability using the Travel pool.
2. Calls the connected Switchboard client's `route` MCP tool with `target_butler = "health"`,
   `tool_name = "medication_travel_snapshot"`, and `source_butler = "travel"`.
3. Unwraps only the Switchboard route tool's serialized `CallToolResult` payload at
   `result.data` and validates it against the strict shared contract. A missing or malformed
   wrapper fails closed rather than treating wrapper fields or text content as Health data.
4. Returns the validated success envelope or a structured error envelope with an error code and
   retryability flag.

The consumer never accepts a target butler or tool name from its caller, preventing it from becoming
a generic health export or arbitrary routing proxy.

Alternative considered: add a direct Health connection or a cross-schema view. Rejected because an
interactive Travel read must use MCP and because a view would broaden database privileges.

### One strict shared contract, two response states

`MedicationTravelSnapshot` is a Pydantic model configured with `extra = "forbid"` and a constant
schema version. `status = "ok"` requires no error and may contain medication entries. `status =
"error"` requires an error object and an empty medication list. Medication entries also forbid extra
fields, so a provider cannot accidentally add private fields without breaking validation.

Errors are normalized as follows:

- explicit permission revocation: `permission_denied`, not retryable;
- missing Switchboard client: `switchboard_unavailable`, retryable;
- timeout, MCP failure, or Health routing failure: `health_unavailable`, retryable;
- response contract mismatch: `invalid_health_response`, not retryable.

An outer Switchboard MCP error or an inner Health `CallToolResult` with `is_error = true` is
provider unavailability. A successful wrapper whose `result.data` is absent or malformed is an
invalid response. Travel never falls back to parsing `content` text because that would create a
second, less strict health-data path.

Alternative considered: raise every failure as an MCP transport error. Rejected because downstream
jobs need deterministic, typed handling while preserving successful-empty behavior.

### No storage migration

The existing Health medication fact metadata contains every field required by this contract.
Remaining quantity and refill inventory are not introduced because bu-za343 only needs to know that
active medications exist and how they are scheduled when forming a preparation reminder.

## Risks / Trade-offs

- [Health is temporarily unavailable] -> Return a retryable error envelope; never substitute an
  empty success because that would hide missing data.
- [A future provider adds fields] -> Strict Travel validation fails with `invalid_health_response`
  until the versioned contract is deliberately updated.
- [Current medication schedules are free-form strings] -> Preserve them as `list[str]`; do not
  invent clinical interpretation or timing semantics.
- [The permissions lookup fails open by repository policy] -> Reuse the canonical permission
  helper so this integration has the same owner-control semantics as other runtime gates.
- [The consumer adds one Travel MCP tool] -> Keep the surface purpose-specific and parameterless so
  it does not become a generic cross-butler proxy.

## Migration Plan

Deploy Health and Travel together. On rollback, remove both MCP registrations and their shared
contract module; storage is unchanged and no data rollback is required.

## Open Questions

None. Medication inventory tracking, if later required, needs its own Health-owned storage and spec
change rather than an expansion of this integration.
