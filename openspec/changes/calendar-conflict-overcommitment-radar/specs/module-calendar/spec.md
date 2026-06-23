# Module Calendar — Spec delta for calendar-conflict-overcommitment-radar

## MODIFIED Requirements

### Requirement: Calendar Event CRUD Tools — tool count

The module MUST register **23 MCP tools total** (was 22). The new tool is
`calendar_scan_conflicts`, added by the `calendar-conflict-overcommitment-radar`
capability. All previously registered tools SHALL remain unchanged.

The full tool list after this change:
`calendar_list_events`, `calendar_get_event`, `calendar_create_event`,
`calendar_update_event`, `calendar_delete_event`,
`calendar_update_event_instance`, `calendar_delete_event_instance`,
`calendar_find_free_slots`, `calendar_list_calendars`,
`calendar_create_butler_event`, `calendar_update_butler_event`,
`calendar_delete_butler_event`, `calendar_toggle_butler_event`,
`calendar_add_attendees`, `calendar_remove_attendees`,
`reminder_create`, `reminder_list`, `reminder_dismiss`,
`calendar_sync_status`, `calendar_force_sync`, `calendar_set_primary`,
`calendar_propose_event`, `calendar_scan_conflicts`.

#### Scenario: Tool registration includes `calendar_scan_conflicts`

- **WHEN** the Calendar module registers its MCP tools
- **THEN** `calendar_scan_conflicts` is registered and callable by a butler
  LLM session
- **AND** the total registered tool count is 23
