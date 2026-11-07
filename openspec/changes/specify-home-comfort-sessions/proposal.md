## Why

A thermostat accepting a setpoint does not prove that the room became comfortable. A bounded session should show what was requested, what the device accepted and what fresh room observations actually achieved, while respecting manual override and expiry.

## What Changes

- Propose observe-only sessions and an optional single exact setpoint action through Home's existing physical-risk boundary.
- Define a five-minute observed-band success window, explicit source freshness, a finite achievement deadline, override/Stop behavior and zero routine inference.
- Reuse the adopted event-response contract and the Home source/owner-control conventions established by the appliance specification; keep physical control separately reviewable.

## Capabilities

### New Capabilities

- `home-comfort-session`: Durable, finite, measured-room outcome sessions, distinct from immediate HA command verification and scheduled environment reports.

### Modified Capabilities

None. Existing module-home-assistant risk/approval rules remain binding and unchanged. The Home identity writer in `generalist-lead-focused-delegation` remains the sole writer of its requirements; this new lifecycle adds no generic rule engine or broad standing control authority.

## Impact

Future surfaces are Home-owned session state and room/actuator claims, Home module/API entrypoints, the existing actuation ledger and owner cards. No predictive occupancy, health or safety device control, multi-device controller, mode/range change, automatic restore, tariff optimization, all-night reasoning or general automation language.

Status: **feature specification proposed, not adopted or implemented**. Shared REQ-event-response-contract-001 through 005 are adopted. This specific profile and control scope still need owner adoption. Tracking: `bu-okh0fw.3`, after appliance drafting in `bu-okh0fw.1`; base `8465542f4`. No real room sensor/actuator pair or performance has been commissioned. Selection is required before activation, not fabricated in this specification.
