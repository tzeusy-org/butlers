## Context

The current Dashboard Chronicles main specification already defines the Activity
lane taxonomy as `sleep`, `exercise`, `work`, `butler_ops`, `play`, `social`,
`travel`, `eat`, and `rest`. However, its Editorial KPI Endpoint requirement
only says that `hours_by_top_lanes` contains the top three lanes by minutes. It
does not state which lane vocabulary that API field uses.

The shipped aggregation and editorial code calculate KPI lanes from activity
records only and expose the same nine values. `butler_ops` is a later,
intentional extension to the original eight IEA-derived life-balance lanes: it
separates internal butler sessions from the owner's occupation work.

## Goals / Non-Goals

**Goals:**

- Make the Editorial KPI Endpoint requirement unambiguous about the lane values
  it returns.
- Keep the API requirement aligned with the existing Category Taxonomy Mapping
  requirement and the shipped aggregation contract.
- Preserve the owner-work versus `butler_ops` distinction in KPI output.

**Non-Goals:**

- Change the KPI endpoint, aggregation algorithm, API schema, database, or
  frontend.
- Replace the current nine-lane contract with the historical eight-lane IEA
  snapshot.
- Define a source-category vocabulary for KPI output.

## Decisions

### Bind KPI entries to the existing Activity lane contract

The KPI requirement will explicitly require every
`hours_by_top_lanes[*].lane` value to use the same nine Activity lanes already
defined by Category Taxonomy Mapping. This avoids a second, competing taxonomy
and makes the existing API behavior testable at the specification level.

Alternative considered: enumerate only the original eight IEA life-balance
lanes. Rejected because the current aggregation, API tests, frontend taxonomy,
and governing main taxonomy requirement intentionally include `butler_ops`.

### Keep the change specification-only

The code already implements the desired behavior. Updating runtime code or API
payloads would add risk without resolving the missing normative statement.

Alternative considered: revise the Pydantic field description that still calls
the values "ten taxonomy categories." Rejected for this bead because the
requested scope is the governing Dashboard Chronicles OpenSpec; it can be
handled as a separate code-adjacent documentation cleanup.

## Risks / Trade-offs

- [A later lane is added without updating the KPI requirement] → The KPI
  requirement references the central Activity taxonomy and lists the current
  values, so future taxonomy changes have an explicit paired-spec review point.
- [Readers mistake `butler_ops` for owner work] → The KPI scenario repeats the
  existing rule that owner occupation maps to `work` while internal butler
  sessions map to `butler_ops`.
