## Why

The Home safety sentinel (bu-jv1moa.6, .18) shipped leak, smoke, carbon monoxide and gas alerts. Three
promises from the same outcome did not ship:

- an honest "home sensing is blind" condition while the owner is away;
- a freezer-temperature alarm;
- a door or window left open while the owner is away.

Today, an owner who is away gets silence when Home Assistant stops reporting. Silence reads as "all is
well", which is the failure the sentinel was built to avoid.

## What Changes

- Specify three deterministic Home producers on the existing owner condition ledger:
  - `home:sensing-blind`;
  - `home:freezer`;
  - `home:away-door`.
  - Each has no model, no Home Assistant service call and no prepared action.
- Specify the `home.sensing` situation projection that RFC 0038 already registers but nothing writes. The blind condition consumes that projection; it is not a second health store.
- The away premise is the canonical `owner.place` slot and timeline from the presence reducer (.17), read through the same owner reader the departure card (.8) uses. There is no second presence inference.
- Every threshold is an explicit owner-confirmed value on a declaration row:
  - freezer maximum, hysteresis and dwell;
  - open-door duration;
  - blind dwell.
- A missing value means `unconfigured`, and nothing alerts. The `home:thresholds:*` default-fallback rule does not apply.
- Delivery is routine, through `_send_notify`. A quiet-hours suppression is held by the ledger retry and never lost. Breaking quiet hours for any of these producers is a pending owner prerequisite (design.md, P1), tied to bu-dhloi1.3. It is not assumed.
- Split with the departure card:
  - .8 sends one card at departure.
  - `home:away-door` is the continuing condition for the rest of the absence.
  - Both read the same `departure_watch` declaration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `home-deterministic-jobs`: adds REQ-home-deterministic-jobs-002 through 007 (spec delta in this change).

## Impact

Future owners:

- the Home deterministic job module and Home migrations (`temperature_watch`, a `departure_watch` column, an away-safety settings row);
- owner-only Home API routes;
- the Home dashboard Devices panels;
- a `home.sensing` writer login.

Unaffected:

- the leak/smoke/CO/gas sentinel;
- the departure card;
- the shared ledger default ladder;
- every other condition source.

Status: **specification proposed, not adopted or implemented**. Tracking: `bu-jv1moa.32`. Base: `53832dfda9` plus the departure card in #5789/#5790 (not yet merged). No producer, schedule, login or flag is activated by this change.
