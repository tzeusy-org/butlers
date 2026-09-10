## ADDED Requirements

### Requirement: Complete Role-Fit Tool Registration

Concierge SHALL register its complete role-fit MCP surface through the
canonical FastMCP registry. The registered/callable set remains governed by
effective core groups, module groups, type/name gates, module state, startup
success, roster configuration, and manifesto scope. Canonical `tools/list`
SHALL remain complete for the handlers admitted by those controls.

The repo-wide 30-50 target SHALL apply to full tool definitions initially
loaded into model context, as defined by RFC 0002 Amendment 1 and RFC 0027. It
SHALL NOT be interpreted as a hard ceiling on Concierge's registered handlers
or used to remove legitimate role-fit tools.

#### Scenario: Complete registered surface is independent of initial model context

- **WHEN** a roster integration test boots Concierge with the `dashboard_read`
  module enabled and enumerates the canonical registered surface
- **THEN** every core and module handler admitted by Concierge's effective
  registration controls is available from canonical `tools/list`
- **AND** the registered `dashboard_read_*` names exactly match the module's
  role-fit dashboard-read contract
- **AND** every `dashboard_read_*` tool's docstring is non-empty (docstring
  completeness for this bead's own tool surface; pre-existing gaps in shared
  core tools are tracked separately, not by this assertion)
- **AND** the registered handler count is not treated as evidence of the
  number or schema bytes of definitions initially loaded into model context

## REMOVED Requirements

### Requirement: Tool Surface Budget
**Reason**: The requirement incorrectly applies RFC 0002's former 30-50 eager-presentation target to the complete canonical registered handler set, contradicting accepted RFC 0002 Amendment 1 and RFC 0027.
**Migration**: Use Complete Role-Fit Tool Registration above; initial-context definition and schema-byte limits remain owned by RFC 0027 conformance and `bu-ondtw`.
