## Why

`calendar_respond` declares the approval mode `always_per_event`, so the gate parks it for the
owner's tap on every call and never matches a standing rule. The rule writes did not know that: an
owner could approve an RSVP with "always allow", get a rule that showed as active and never applied,
and every RSVP approval still counted toward promotion (bu-5ii4gj.4.1, B2 of bu-5ii4gj.4).

## What changes

- Every public rule write refuses a tool whose mode is `always_per_event` with the typed reason
  `rule_refused_always_per_event` (HTTP 409 on the dashboard): direct create, create from an action,
  approve with a rule, and the suggestion preview, on both the dashboard and the MCP tools.
- The refusal happens before step-up is consumed and before any rule or `rule_created` row commits.
- Approvals of such a tool record no promotion history and create no suggestion.
- The approval detail carries `rule_eligible`, and the dashboard shows no "always allow" prompt for
  an ineligible action.
- The mode is read from a checked-in set (`ALWAYS_PER_EVENT_TOOLS`) because the dashboard holds no
  module instance; a contract test pins it equal to what every module declares.

## Deferred

- None.
