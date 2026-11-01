## Why

A washing machine finishing leaves a human task unfinished. Home needs to carry an explicitly observed appliance cycle through one owner handoff without treating the machine's completion, an opened door or a delivered message as proof the chore is done.

## What Changes

- Propose owner enrollment of up to eight washers or dishwashers exposing verified explicit phase states, with one cycle identity and a separate owner handoff.
- Define deterministic completion/error handling, Done/Later/Stop watching controls, expiry, replay protection and a concrete zero-inference response profile.
- Apply adopted REQ-event-response-contract-001 through 005. Numerical settings below are proposed initial runtime seeds, not measured performance or deployment changes.

## Capabilities

### New Capabilities

- `home-appliance-handoff`: Durable lifecycle from an explicit appliance cycle to the owner's unload/transfer acknowledgement. This is distinct from interval maintenance, generic device-health alerts and one-shot HA commands.

### Modified Capabilities

None. Existing Home, connector, delivery and goal-runtime contracts are reused. No parallel modification of the Home identity clauses in `generalist-lead-focused-delegation` is required: the reducer is a deterministic domain consumer, not a reasoning subscription.

## Impact

Future owners: Home module registration, domain-owned cycle/handoff data, Home API and existing owner-card delivery. No appliance control, power-waveform inference, camera or occupant inference, household messaging, new butler, external provider or general task manager.

Status: **feature specification proposed, not adopted or implemented**. The owner adopted the shared response contract and released specification work on 2026-10-28; that does not adopt this domain profile. Tracking: `bu-okh0fw.1`. Base: `8465542f4`. The actual appliance model/entity is a commissioning input, not an invented supported-device claim. No enrollment activates without its validated mapping and source evidence.
